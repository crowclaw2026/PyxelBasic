#!/usr/bin/env python3
"""既存Appを変更せず、フレーム時間・CPU・RSSをJSONへ記録する診断用起動器。"""
import argparse
from collections import deque
from datetime import datetime, timezone
import faulthandler
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ProgressWatchdog:
    """Dump from an attached Python thread, not the GIL-less native timer.

    A native extension holding the GIL can prevent this thread from running;
    the independent monitor's process/sample deadlines remain the fallback.
    """
    def __init__(self, timeout=2, poll=.25, file=None):
        self.timeout, self.poll = timeout, poll
        self.file = file if file is not None else sys.stderr
        self.last_progress = time.monotonic()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name='diagnostic-watchdog', daemon=True)

    def start(self):
        self.thread.start()

    def touch(self):
        self.last_progress = time.monotonic()

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=1)

    def _run(self):
        last_dump = 0
        while not self.stopped.wait(self.poll):
            now = time.monotonic()
            if now - self.last_progress >= self.timeout and now - last_dump >= self.timeout:
                print('Python watchdog timeout (requires GIL)', file=self.file, flush=True)
                faulthandler.dump_traceback(file=self.file, all_threads=True)
                last_dump = now


def rss_bytes():
    """現在のRSS / Working Set。取得不能ならnull（最大RSSとは混同しない）。"""
    if sys.platform == "linux":
        return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize
    return None


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return None
    return {"count": len(values), "p50_ms": statistics.median(ordered) * 1000,
            "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * .95))] * 1000,
            "p99_ms": ordered[min(len(ordered) - 1, int(len(ordered) * .99))] * 1000,
            "max_ms": ordered[-1] * 1000}


class TimingSeries:
    """Bound memory while preserving whole-run count/max (quantiles are tail-only)."""
    def __init__(self, capacity=4096):
        self.values = deque(maxlen=capacity)
        self.count = 0
        self.maximum = 0.0

    def append(self, value):
        self.values.append(value)
        self.count += 1
        self.maximum = max(self.maximum, value)

    def summary(self):
        result = distribution(self.values)
        if result is not None:
            result.update(count=self.count, retained_count=len(self.values),
                          max_ms=self.maximum * 1000,
                          quantile_scope='whole-run' if self.count == len(self.values) else 'last-retained-values')
        return result


def cgroup_cpu():
    """Cumulative cgroup-v2 counters; deltas, not totals, indicate throttling."""
    if sys.platform != 'linux':
        return None
    try:
        read_started = time.perf_counter()
        entry = next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines()
                     if line.startswith('0::'))
        root = Path('/sys/fs/cgroup') / entry.lstrip('/')
        result = {'path': entry, 'cpu_max': (root / 'cpu.max').read_text().strip(),
                'cpu_stat': {k: int(v) for k, v in
                             (line.split() for line in (root / 'cpu.stat').read_text().splitlines())}}
        result.update(read_started_perf_s=read_started, read_ended_perf_s=time.perf_counter())
        return result
    except (OSError, ValueError, StopIteration):
        return None


def thread_schedstat():
    """Linux cumulative runtime/runqueue wait for the calling thread, if exposed."""
    try:
        read_started = time.perf_counter()
        values = Path('/proc/thread-self/schedstat').read_text().split()
        result = dict(zip(('runtime_ns', 'runqueue_wait_ns', 'timeslices'), map(int, values)))
        result.update(read_started_perf_s=read_started, read_ended_perf_s=time.perf_counter())
        return result
    except (OSError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timing-capacity', type=int, default=4096,
                        help='フレーム時間保持上限。0は旧方式同等の全件保持（比較専用）')
    parser.add_argument('--slow-update-ms', type=float, default=500,
                        help='壁時計/スレッドCPU時間を詳細記録するupdate閾値（既定500ms）')
    parser.add_argument('--sample-storage', choices=('stream', 'memory'), default='stream',
                        help='毎秒サンプルは既定でJSONLのみ保持。memoryは旧方式比較専用')
    parser.add_argument('--watchdog', choices=('python', 'native', 'off'), default='python',
                        help='既定はPython threadから同期stack採取。nativeは旧方式の比較専用')
    parser.add_argument('--capture-sec', type=int, default=None,
                        help='診断限定でPyxel録画秒数を上書き（比較用、既定は変更なし）')
    parser.add_argument('app_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 0 < args.seconds <= 3600:
        parser.error('--seconds は 0 より大きく 3600 以下にしてください')
    if args.capture_sec is not None and not 0 <= args.capture_sec <= 30:
        parser.error('--capture-sec は 0 から 30 にしてください')
    if not 0 <= args.timing_capacity <= 1000000:
        parser.error('--timing-capacity は 0 から 1000000 にしてください')
    if not 0 <= args.slow_update_ms <= 10000:
        parser.error('--slow-update-ms は 0 から 10000 にしてください')
    from main import build_parser
    app_args = args.app_args[1:] if args.app_args[:1] == ['--'] else args.app_args
    options = build_parser().parse_args(app_args)
    if options.version or (options.run and not (options.load or options.file)):
        parser.error('通常起動の引数を指定してください。--run にはプログラムが必要です')
    # Pyxelは起動スクリプトの場所へcwdを変更するため、先に確定する。
    if options.workdir:
        options.workdir = str(Path(options.workdir).resolve())
    # 結果の上書きを避け、出力不能はウィンドウ起動前に検出する。
    with args.output.open('x', encoding='utf-8') as out, args.output.with_suffix('.samples.jsonl').open('x+', encoding='utf-8') as sample_out, args.output.with_suffix('.slow.jsonl').open('x', encoding='utf-8') as slow_out:
        import pyxel
        from pyxelbasic.app import App
        if args.capture_sec is not None:
            original_init = pyxel.init

            def diagnostic_init(*pos, **kw):
                kw['capture_sec'] = args.capture_sec
                return original_init(*pos, **kw)

            pyxel.init = diagnostic_init
        faulthandler.enable()
        app = App(autoload=options.load or options.file, workdir=options.workdir,
                  autorun=options.run, extensions=options.ext, show_fps=options.showfps,
                  gfx_queue_size=options.gfx_queue_size, cycle_steps=options.vm_cycle_steps,
                  cycle_period=options.vm_cycle_ms / 1000,
                  debug_throttle=options.debug_throttle, exec_mode=options.exec_mode,
                  steps_per_frame=options.steps_per_frame)
        if options.run and not app.session.interp.code:
            parser.error('プログラムを読み込めませんでした。結果は有効な負荷計測ではありません')
        update_times, draw_times, intervals = [TimingSeries(args.timing_capacity or None) for _ in range(3)]
        samples = []
        slow_updates = deque(maxlen=64)
        slow_update_count = 0
        original_update, original_draw = app.update, app.draw
        started, cpu_started = time.perf_counter(), time.process_time()
        started_at = datetime.now(timezone.utc).isoformat()
        last_frame, next_sample = started, started
        peak_update = peak_draw = peak_frame = 0.0
        watchdog = None

        def sample(now):
            return {"at_utc": datetime.now(timezone.utc).isoformat(),
                    "elapsed_s": now - started, "cpu_s": time.process_time() - cpu_started,
                    "rss_bytes": rss_bytes(), "mode": app.session.mode,
                    "update_count": update_times.count, "draw_count": draw_times.count,
                    "cgroup_cpu": cgroup_cpu(),
                    "thread_schedstat": thread_schedstat(),
                    "timing_retained": {k: len(v.values) for k, v in
                                        [('update', update_times), ('draw', draw_times), ('frame_interval', intervals)]},
                    "vm_state": app.session.interp.state, "line": app.session.interp.cur_line,
                    # 単一VM producer。ロックを待って計測を止めない概数。
                    "gfx_queue": len(getattr(app.gfx_queue, '_dq', ())),
                    "audio_queue": len(getattr(app.audio_queue, '_dq', ())),
                    "gosub_depth": len(app.session.interp.gosub_stack),
                    "for_depth": len(app.session.interp.for_stack),
                    "max_update_ms": peak_update * 1000,
                    "max_draw_ms": peak_draw * 1000,
                    "max_frame_ms": peak_frame * 1000}

        def update():
            nonlocal last_frame, next_sample, peak_update, peak_frame, slow_update_count
            if watchdog is not None:
                watchdog.touch()
            elif args.watchdog == 'native':
                faulthandler.dump_traceback_later(2, repeat=True)
            thread_started, process_started = time.thread_time(), time.process_time()
            now = time.perf_counter()
            intervals.append(now - last_frame)
            peak_frame = max(peak_frame, now - last_frame)
            last_frame = now
            original_update()
            ended = time.perf_counter()
            thread_s, process_s = time.thread_time() - thread_started, time.process_time() - process_started
            update_times.append(ended - now)
            peak_update = max(peak_update, ended - now)
            if (ended - now) * 1000 >= args.slow_update_ms:
                slow_update_count += 1
                detail = {'ended_at_utc': datetime.now(timezone.utc).isoformat(),
                          'start_elapsed_s': now - started, 'end_elapsed_s': ended - started,
                          'wall_ms': (ended - now) * 1000, 'thread_cpu_ms': thread_s * 1000,
                          'process_cpu_ms': process_s * 1000,
                          'non_cpu_wall_ms': max(0, ended - now - thread_s) * 1000,
                          'line': app.session.interp.cur_line, 'cgroup_cpu_end': cgroup_cpu(),
                          'thread_schedstat_end': thread_schedstat(),
                          'note': 'Non-CPU wall time includes blocking/preemption; not a cause attribution.'}
                slow_updates.append(detail)
                slow_out.write(json.dumps(detail, ensure_ascii=False) + '\n')
                slow_out.flush()
            if ended >= next_sample:
                item = sample(ended)
                if args.sample_storage == 'memory':
                    samples.append(item)
                sample_out.write(json.dumps(item, ensure_ascii=False) + '\n')
                sample_out.flush()  # 強制終了・ネイティブ終了でも直前までの証拠を残す。
                next_sample = ended + 1
            if ended - started >= args.seconds:
                finish()
                pyxel.quit()

        def draw():
            nonlocal peak_draw
            now = time.perf_counter()
            original_draw()
            elapsed = time.perf_counter() - now
            draw_times.append(elapsed)
            peak_draw = max(peak_draw, elapsed)

        finished = False

        def finish():
            nonlocal finished
            if finished:
                return
            finished = True
            if watchdog is not None:
                watchdog.close()
            elif args.watchdog == 'native':
                faulthandler.cancel_dump_traceback_later()
            app.session.request_stop()
            app.gfx_queue.stop()
            app.audio_queue.stop()
            if app.session_thread:
                app.session_thread.join(timeout=1)
            ended = time.perf_counter()
            active_cpu_s = time.process_time() - cpu_started
            final_sample = sample(ended)  # RSS before result-only sample materialization.
            in_run_retained = len(samples)
            sample_out.write(json.dumps(final_sample, ensure_ascii=False) + '\n')
            sample_out.flush()
            materialize_started = time.perf_counter()
            materialize_cpu_started = time.process_time()
            if args.sample_storage == 'memory':
                samples.append(final_sample)
                result_samples = samples
            else:
                sample_out.seek(0)
                result_samples = [json.loads(line) for line in sample_out]
            materialize_seconds = time.perf_counter() - materialize_started
            result = {"schema_version": 6, "python": platform.python_version(),
                      "watchdog_backend": args.watchdog,
                      "watchdog_thread_alive": bool(watchdog and watchdog.thread.is_alive()),
                      "sample_storage": args.sample_storage,
                      "in_run_retained_samples": in_run_retained,
                      "sample_materialization_s": materialize_seconds,
                      "sample_materialization_cpu_s": time.process_time() - materialize_cpu_started,
                      "rss_after_sample_materialization_bytes": rss_bytes(),
                      "started_perf_counter_s": started,
                      "timing_capacity": args.timing_capacity,
                      "slow_update_ms": args.slow_update_ms, "slow_update_count": slow_update_count,
                      "slow_updates_tail": list(slow_updates),
                      "started_at_utc": started_at,
                      "platform": platform.platform(), "pyxel": pyxel.VERSION,
                      "app_args": app_args, "requested_seconds": args.seconds,
                      "capture_sec_override": args.capture_sec,
                      "elapsed_s": ended - started, "cpu_s": active_cpu_s,
                      "update": update_times.summary(), "draw": draw_times.summary(),
                      "frame_interval": intervals.summary(), "samples": result_samples,
                      "session_thread_alive": bool(app.session_thread and app.session_thread.is_alive()),
                      "screen": [''.join(row) for row in app.session.screen.chars]}
            json.dump(result, out, ensure_ascii=False, indent=2)
            out.write('\n')
            out.flush()  # PyxelのネイティブquitはPythonのfinallyを通らず終了し得る。
            print(json.dumps({k: v for k, v in result.items() if k not in ('samples', 'screen')}, ensure_ascii=False), flush=True)

        app.update, app.draw = update, draw
        # 2秒以上戻らない場合のPythonスタックをstderrへ。ネイティブ原因は別途OS計測が必要。
        if args.watchdog == 'python':
            watchdog = ProgressWatchdog()
            watchdog.start()
        elif args.watchdog == 'native':
            faulthandler.dump_traceback_later(2, repeat=True)
        try:
            app.run()
        finally:
            finish()


if __name__ == '__main__':
    main()
