import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import subprocess

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import judge_stall as j

class JudgeTests(unittest.TestCase):
    def run_judge(self,d):
        with patch.object(sys,'argv',['judge','--state-dir',str(d)]): j.main()
    def test_idle_makes_no_model_call(self):
        with tempfile.TemporaryDirectory() as t,patch.object(j.subprocess,'run') as run:
            self.run_judge(Path(t));run.assert_not_called()
    def test_success_acknowledges_only_after_terminal_ok(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t);(d/'pending.json').write_text('{"events":[{"key":"test"}]}')
            result=subprocess.CompletedProcess([],0,'{"status":"ok"}','')
            with patch.object(j.subprocess,'run',return_value=result) as run:
                self.run_judge(d);self.assertEqual(run.call_count,1)
            self.assertTrue((d/'last-reviewed.json').exists())
            self.assertFalse((d/'inflight.json').exists())
    def test_failure_retains_evidence_and_cools_down(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t);(d/'pending.json').write_text('{"events":[{"key":"test"}]}')
            result=subprocess.CompletedProcess([],1,'{"status":"error"}','failed')
            with patch.object(j.subprocess,'run',return_value=result):
                with self.assertRaises(SystemExit): self.run_judge(d)
            self.assertTrue((d/'inflight.json').exists())
            with patch.object(j.subprocess,'run') as run:
                self.run_judge(d);run.assert_not_called()
            self.assertEqual(json.loads((d/'judge-status.json').read_text())['attempts'],1)

if __name__=='__main__':unittest.main()
