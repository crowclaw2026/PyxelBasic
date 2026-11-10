#!/usr/bin/env python3
"""Isolated timing-store allocation comparison, NOT a Pyxel/Windows reproduction."""
import argparse
import json
import subprocess
import sys
import time
from diagnose_stall import TimingSeries, rss_bytes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--worker', type=int, choices=[0, 4096])
    args = ap.parse_args()
    if args.worker is None:
        results = []
        for capacity in [0, 4096]:
            results.append(json.loads(subprocess.check_output(
                [sys.executable, __file__, '--worker', str(capacity)], text=True)))
        print(json.dumps({'note': '72000 frames simulates timing allocations only; no real-time wait or App',
                          'results': results}, indent=2))
        return
    series = [TimingSeries(args.worker or None) for _ in range(3)]
    checkpoints = []
    started = time.process_time()
    for frame in range(72000):
        for index, item in enumerate(series):
            item.append((frame + index) * 1e-9)
        if frame + 1 in [3600, 72000]:
            checkpoints.append({'frames': frame + 1, 'rss_bytes': rss_bytes(),
                                'retained': [len(s.values) for s in series]})
    print(json.dumps({'capacity': args.worker, 'cpu_s': time.process_time() - started,
                      'checkpoints': checkpoints,
                      'rss_growth_bytes': checkpoints[-1]['rss_bytes'] - checkpoints[0]['rss_bytes']}))


if __name__ == '__main__':
    main()
