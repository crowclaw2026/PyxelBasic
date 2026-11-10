import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('monitor',Path(__file__).resolve().parents[1]/'tools/monitor_stall.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class MonitorTests(unittest.TestCase):
    def test_normal_capture_growth_not_leak(self):
        self.assertEqual(m.anomalies({'samples':[{'elapsed_s':0,'rss_bytes':90*2**20},{'elapsed_s':20,'rss_bytes':130*2**20}]}),[])
    def test_real_stall_and_post_warmup_growth(self):
        samples=[{'elapsed_s':60+i,'rss_bytes':130*2**20+i*2**20} for i in range(121)]
        self.assertEqual(m.anomalies({'update':{'max_ms':2100},'samples':samples}),['update-2s','rss-growth-64MiB'])
    def test_events_deduplicate_and_remain_pending(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t);s={}
            self.assertTrue(m.event(d,s,'a',{'run':'one'},now=30000))
            self.assertFalse(m.event(d,s,'a',{'run':'two'},now=30001))
            self.assertTrue(m.event(d,s,'b',{'run':'three'},now=30001))
            self.assertEqual(len(json.loads((d/'pending.json').read_text())['events']),2)
            self.assertTrue(m.event(d,s,'a',{'run':'four'},now=60000))
    def test_retention_preserves_evidence_and_unowned_dirs(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t);(d/'runs').mkdir()
            for i in range(4):
                p=d/'runs'/f'run-{i}';p.mkdir();(p/'normal.json').write_text('{}')
            p=d/'runs'/'run-anomaly';p.mkdir();(p/'anomaly.json').write_text('{}')
            (d/'runs'/'unowned').mkdir()
            m.trim_runs(d,keep=2)
            self.assertFalse((d/'runs'/'run-0').exists())
            self.assertTrue(p.exists());self.assertTrue((d/'runs'/'unowned').exists())
    def test_live_child_trial(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t)
            r=m.trial(d,('idle',[]),2,{})
            self.assertEqual(r['kinds'],[])
            self.assertGreaterEqual(r['elapsed_s'],2)
            self.assertEqual(r['returncode'],0)

if __name__=='__main__':unittest.main()
