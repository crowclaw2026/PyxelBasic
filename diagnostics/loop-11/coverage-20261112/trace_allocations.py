"""One-off tracemalloc fixture, not a performance/fault reproduction benchmark."""
import json
from pathlib import Path
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from pyxelbasic.app import App
import diagnose_stall

trace_output = Path(sys.argv[1])
del sys.argv[1]
original_run = App.run


def run(self):
    original_update = self.update
    started = time.perf_counter()
    baseline = None
    baseline_at = None
    finished = False

    def update():
        nonlocal baseline, baseline_at, finished
        elapsed = time.perf_counter() - started
        if baseline is None and elapsed >= 20:
            baseline = tracemalloc.take_snapshot()
            baseline_at = elapsed
        if baseline is not None and not finished and elapsed >= 75:
            finished = True
            current = tracemalloc.take_snapshot()
            stats = current.compare_to(baseline, 'lineno')
            rows = []
            for item in stats:
                frame = item.traceback[0]
                rows.append({'file': frame.filename, 'line': frame.lineno,
                             'size_diff': item.size_diff, 'count_diff': item.count_diff,
                             'size': item.size, 'count': item.count})
            trace_output.write_text(json.dumps({
                'note': 'Tracing changes CPU/memory; Python allocations only, not native memory.',
                'baseline_elapsed_s': baseline_at, 'final_elapsed_s': elapsed,
                'rss_bytes_at_final': diagnose_stall.rss_bytes(),
                'traced_current_peak_bytes': tracemalloc.get_traced_memory(),
                'total_size_diff': sum(x.size_diff for x in stats),
                'top_positive': sorted(rows, key=lambda x: -x['size_diff'])[:30],
                'top_negative': sorted(rows, key=lambda x: x['size_diff'])[:10],
            }, indent=2) + '\n')
        original_update()

    self.update = update
    return original_run(self)


App.run = run
tracemalloc.start(1)
diagnose_stall.main()
