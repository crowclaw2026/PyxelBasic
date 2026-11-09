#!/usr/bin/env python3
"""既存Appを変更せず、フレーム時間・CPU・RSSをJSONへ記録する診断用起動器。"""
import argparse
from datetime import datetime, timezone
import faulthandler
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--capture-sec', type=int, default=None,
                        help='診断限定でPyxel録画秒数を上書き（比較用、既定は変更なし）')
    parser.add_argument('app_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 0 < args.seconds <= 300:
        parser.error('--seconds は 0 より大きく 300 以下にしてください')
    if args.capture_sec is not None and not 0 <= args.capture_sec <= 30:
        parser.error('--capture-sec は 0 から 30 にしてください')
    from main import build_parser
    app_args = args.app_args[1:] if args.app_args[:1] == ['--'] else args.app_args
    options = build_parser().parse_args(app_args)
    if options.version or (options.run and not (options.load or options.file)):
        parser.error('通常起動の引数を指定してください。--run にはプログラムが必要です')
    # Pyxelは起動スクリプトの場所へcwdを変更するため、先に確定する。
    if options.workdir:
        options.workdir = str(Path(options.workdir).resolve())
    # 結果の上書きを避け、出力不能はウィンドウ起動前に検出する。
    with args.output.open('x', encoding='utf-8') as out, args.output.with_suffix('.samples.jsonl').open('x', encoding='utf-8') as sample_out:
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
        update_times, draw_times, intervals, samples = [], [], [], []
        original_update, original_draw = app.update, app.draw
        started, cpu_started = time.perf_counter(), time.process_time()
        started_at = datetime.now(timezone.utc).isoformat()
        last_frame, next_sample = started, started

        def sample(now):
            return {"at_utc": datetime.now(timezone.utc).isoformat(),
                    "elapsed_s": now - started, "cpu_s": time.process_time() - cpu_started,
                    "rss_bytes": rss_bytes(), "mode": app.session.mode,
                    "vm_state": app.session.interp.state, "line": app.session.interp.cur_line,
                    # 単一VM producer。ロックを待って計測を止めない概数。
                    "gfx_queue": len(getattr(app.gfx_queue, '_dq', ())),
                    "audio_queue": len(getattr(app.audio_queue, '_dq', ())),
                    "gosub_depth": len(app.session.interp.gosub_stack),
                    "for_depth": len(app.session.interp.for_stack)}

        def update():
            nonlocal last_frame, next_sample
            faulthandler.dump_traceback_later(2, repeat=True)
            now = time.perf_counter()
            intervals.append(now - last_frame)
            last_frame = now
            original_update()
            ended = time.perf_counter()
            update_times.append(ended - now)
            if ended >= next_sample:
                item = sample(ended)
                samples.append(item)
                sample_out.write(json.dumps(item, ensure_ascii=False) + '\n')
                sample_out.flush()  # 強制終了・ネイティブ終了でも直前までの証拠を残す。
                next_sample = ended + 1
            if ended - started >= args.seconds:
                finish()
                pyxel.quit()

        def draw():
            now = time.perf_counter()
            original_draw()
            draw_times.append(time.perf_counter() - now)

        finished = False

        def finish():
            nonlocal finished
            if finished:
                return
            finished = True
            faulthandler.cancel_dump_traceback_later()
            app.session.request_stop()
            app.gfx_queue.stop()
            app.audio_queue.stop()
            if app.session_thread:
                app.session_thread.join(timeout=1)
            ended = time.perf_counter()
            samples.append(sample(ended))
            result = {"schema_version": 1, "python": platform.python_version(),
                      "started_at_utc": started_at,
                      "platform": platform.platform(), "pyxel": pyxel.VERSION,
                      "app_args": app_args, "requested_seconds": args.seconds,
                      "capture_sec_override": args.capture_sec,
                      "elapsed_s": ended - started, "cpu_s": time.process_time() - cpu_started,
                      "update": distribution(update_times), "draw": distribution(draw_times),
                      "frame_interval": distribution(intervals), "samples": samples,
                      "session_thread_alive": bool(app.session_thread and app.session_thread.is_alive()),
                      "screen": [''.join(row) for row in app.session.screen.chars]}
            json.dump(result, out, ensure_ascii=False, indent=2)
            out.write('\n')
            out.flush()  # PyxelのネイティブquitはPythonのfinallyを通らず終了し得る。
            print(json.dumps({k: v for k, v in result.items() if k not in ('samples', 'screen')}, ensure_ascii=False), flush=True)

        app.update, app.draw = update, draw
        # 2秒以上戻らない場合のPythonスタックをstderrへ。ネイティブ原因は別途OS計測が必要。
        faulthandler.dump_traceback_later(2, repeat=True)
        try:
            app.run()
        finally:
            finish()


if __name__ == '__main__':
    main()
