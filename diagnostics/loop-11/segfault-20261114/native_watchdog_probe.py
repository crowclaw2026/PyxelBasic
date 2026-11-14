"""Bounded pure-Python frame churn; no Pyxel, SDL, audio, or result serialization."""
import argparse
import faulthandler
import json
import resource
import time

ap = argparse.ArgumentParser()
ap.add_argument('--seconds', type=float, default=10)
ap.add_argument('--interval', type=float, default=.0001)
args = ap.parse_args()
assert 0 < args.seconds <= 30 and 0 < args.interval <= 1
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def frames(depth):
    if depth:
        return frames(depth - 1) + 1
    return sum(i for i in range(5))


started = time.monotonic()
with open('/dev/null', 'w') as sink:
    faulthandler.enable()
    faulthandler.dump_traceback_later(args.interval, repeat=True, file=sink)
    try:
        while time.monotonic() - started < args.seconds:
            frames(20)
    finally:
        faulthandler.cancel_dump_traceback_later()
print(json.dumps({'elapsed_s': time.monotonic() - started, 'note': 'No crash does not exclude a rare race'}))
