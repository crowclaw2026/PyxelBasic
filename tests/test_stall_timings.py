import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('diagnose', Path(__file__).resolve().parents[1] / 'tools/diagnose_stall.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class TimingTests(unittest.TestCase):
    def test_evicted_stall_remains_in_whole_run_max(self):
        series = d.TimingSeries(8)
        series.append(3.0)
        for _ in range(100):
            series.append(.01)
        result = series.summary()
        self.assertEqual(result['count'], 101)
        self.assertEqual(result['retained_count'], 8)
        self.assertEqual(result['max_ms'], 3000)
        self.assertEqual(result['p50_ms'], 10)
        self.assertEqual(result['quantile_scope'], 'last-retained-values')

    def test_full_comparison_preserves_all_values(self):
        series = d.TimingSeries(None)
        self.assertIsNone(series.summary())
        for value in [.01, .02, .03]:
            series.append(value)
        result = series.summary()
        self.assertEqual(result['retained_count'], 3)
        self.assertEqual(result['quantile_scope'], 'whole-run')
        self.assertEqual(result['max_ms'], 30)


if __name__ == '__main__':
    unittest.main()
