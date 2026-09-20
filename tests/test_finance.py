import unittest
from decimal import Decimal as D
from finance import load_csv, sample_csv, analyze, compare, commentary


class FinanceTests(unittest.TestCase):
    def test_sample_control_totals(self):
        rows = load_csv(sample_csv())
        self.assertEqual(len(rows), 15)
        self.assertEqual(sum(r['budget'] for r in rows), D('12000000'))
        self.assertEqual(sum(r['actual'] for r in rows), D('12380000'))
        self.assertEqual(sum(r['forecast'] for r in rows), D('12190000'))
        results = analyze(rows)
        self.assertEqual(sum(r['variance'] for r in results), D('380000'))
        self.assertEqual(sum(r['material'] for r in results), 9)

    def test_zero_baseline_and_zero_movement(self):
        result = compare(D('75000'), D('0'))
        self.assertIsNone(result['pct'])
        self.assertTrue(result['material'])
        self.assertFalse(compare(D('0'), D('0'), D('0'), D('0'))['material'])

    def test_both_thresholds_and_boundary(self):
        self.assertFalse(compare(D('2040000'), D('2000000'))['material'])
        self.assertFalse(compare(D('3060000'), D('3000000'))['material'])
        self.assertTrue(compare(D('1050000'), D('1000000'))['material'])
        self.assertEqual(compare(D('80000'), D('100000'))['direction'], 'Favorable')

    def test_invalid_inputs_blocked(self):
        sample = sample_csv()
        for bad in ('', 'NaN', '-1', '1.001', 'Infinity'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                load_csv(sample.replace('2100000', bad, 1))
        with self.assertRaises(ValueError):
            load_csv(sample + sample.splitlines()[1] + '\n')
        with self.assertRaises(ValueError):
            load_csv(sample.replace('INR', 'USD', 1))
        with self.assertRaises(ValueError):
            load_csv(sample.replace('2026-08', '2026-09', 1))

    def test_forecast_and_commentary(self):
        rows = analyze(load_csv(sample_csv()), 'forecast')
        self.assertEqual(sum(r['variance'] for r in rows), D('190000'))
        text = commentary(rows[0], 'forecast')
        self.assertIn('does not establish the cause', text)


if __name__ == '__main__':
    unittest.main()
