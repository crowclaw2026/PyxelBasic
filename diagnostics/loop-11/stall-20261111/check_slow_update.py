"""Artificial 600ms sleep validates diagnostic clocks; not a fault reproduction."""
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
        time.sleep(.6)
    return original(self)
App.update = update
diagnose_stall.main()
