"""Inject one 2.3s sleep to exercise the new diagnostic watchdog, not the original crash."""
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from pyxelbasic.app import App
import diagnose_stall
original = App.update
first = True
def update(self):
    global first
    if first:
        first = False
        time.sleep(2.3)
    return original(self)
App.update = update
diagnose_stall.main()
