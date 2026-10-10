import unittest
from decimal import Decimal as D
from finance import (aggregate, analyze, apply_master, commentary, compare, line_label, load_cc_master, load_csv,
                     review_lines, sample_cc_master_csv, sample_csv)

LINE, TOTAL = (D('25000'), D('10')), (D('50000'), D('5'))


class FinanceTests(unittest.TestCase):
    def test_sample_control_totals(self):
        rows = load_csv(sample_csv('account'))
        self.assertEqual(len(rows), 16)
        self.assertEqual(sum(r['budget'] for r in rows), D('12010000'))
        self.assertEqual(sum(r['actual'] for r in rows), D('12375000'))
        self.assertEqual(sum(r['forecast'] for r in rows), D('12192500'))
        results = analyze(rows)
        self.assertEqual(sum(r['variance'] for r in results), D('365000'))
        self.assertEqual(sum(r['material'] for r in results), 9)  # thresholds only
        self.assertEqual(sum(r['material'] for r in review_lines(rows, 'budget', (D(50000), D(5)))), 10)  # + negative

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
        sample = sample_csv('account')
        for bad in ('', 'NaN', '1.001', 'Infinity', 'abc'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                load_csv(sample.replace('2100000', bad, 1))
        with self.assertRaises(ValueError):
            load_csv(sample + sample.splitlines()[1] + '\n')
        with self.assertRaises(ValueError):
            load_csv(sample.replace('INR', 'USD', 1))
        with self.assertRaises(ValueError):
            load_csv(sample.replace('2026-08', '2026-09', 1))

    def test_forecast_and_commentary(self):
        rows = analyze(load_csv(sample_csv('account')), 'forecast')
        self.assertEqual(sum(r['variance'] for r in rows), D('182500'))
        text = commentary(rows[0], 'forecast')
        self.assertIn('does not establish the cause', text)


class CostCentreTests(unittest.TestCase):
    def setUp(self):
        self.data, self.checks = apply_master(load_csv(sample_csv()), load_cc_master(sample_cc_master_csv()))

    def test_cost_centres_reconcile_to_account_file(self):
        def totals(rows):
            return {(r['country'], r['account']): (r['actual'], r['budget'], r['forecast'])
                    for r in rows if r['actual'] or r['budget']}
        self.assertEqual(len(self.data), 24)
        self.assertEqual(totals(aggregate(self.data, ())), totals(load_csv(sample_csv('account'))))
        self.assertEqual(self.data.dims, ('cost_center',))

    def test_columns_any_order_aliases_and_extra_dimensions(self):
        text = ('Project,Cost Centre,Account,Country,Month,Currency,Budget,Actual,Forecast\n'
                'P1,IN-OPS,Travel,India,2026-08,INR,100,150,120\n'
                'P2,IN-OPS,Travel,India,2026-08,INR,100,80,90\n')
        rows = load_csv(text)
        self.assertEqual(rows.dims, ('cost_center', 'project'))
        self.assertEqual(len(aggregate(rows, ('cost_center',))), 1)
        self.assertEqual(aggregate(rows, ())[0]['actual'], D('230'))
        self.assertEqual(line_label(rows[0]), 'India | IN-OPS | P1 | Travel')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            load_csv(text.replace('P2', 'P1'))
        with self.assertRaisesRegex(ValueError, 'Missing columns: forecast'):
            load_csv(text.replace('Forecast', 'Outlook'))

    def test_cost_centre_details_must_be_consistent(self):
        text = ('month,country,cost_center,owner,account,currency,actual,budget,forecast\n'
                '2026-08,India,IN-OPS,Arjun,Travel,INR,1,1,1\n2026-08,India,IN-OPS,Priya,Warehousing,INR,1,1,1\n')
        with self.assertRaisesRegex(ValueError, 'different country, name, function or owner'):
            load_csv(text)
        with self.assertRaisesRegex(ValueError, 'needs a cost_center column'):
            load_csv('month,country,owner,account,currency,actual,budget,forecast\n2026-08,India,A,Travel,INR,1,1,1\n')

    def test_master_checks_unknown_conflicting_and_missing(self):
        self.assertIn('SG-COR', self.checks[0])
        self.assertEqual(self.data.attrs['IN-OPS']['owner'], 'Arjun Mehta')
        with self.assertRaisesRegex(ValueError, 'missing from the master: AU-COR'):
            apply_master(load_csv(sample_csv()), {k: v for k, v in load_cc_master(sample_cc_master_csv()).items() if k != 'AU-COR'})
        clash = sample_cc_master_csv().replace('IN-OPS,India', 'IN-OPS,Singapore')
        with self.assertRaisesRegex(ValueError, 'disagree on country'):
            apply_master(load_csv(sample_csv()), load_cc_master(clash))
        with self.assertRaisesRegex(ValueError, 'no cost or profit centre column'):
            apply_master(load_csv(sample_csv('account')), load_cc_master(sample_cc_master_csv()))

    def test_spread_variance_brings_small_lines_into_scope(self):
        rows = aggregate(self.data, self.data.dims)
        results = review_lines(rows, 'budget', LINE, TOTAL)
        india_staff = [r for r in results if r['country'] == 'India' and r['account'] == 'Employee costs']
        self.assertEqual({r['scope'] for r in india_staff}, {'Account total'})
        self.assertEqual(sum(r['material'] for r in results), 14)
        self.assertEqual(sum(r['material'] for r in review_lines(rows, 'budget', LINE)), 11)
        flat = review_lines(aggregate(self.data, ()), 'budget', TOTAL, TOTAL)
        self.assertEqual(sum(r['material'] for r in flat), 10)
        self.assertNotIn('Account total', {r['scope'] for r in flat})


if __name__ == '__main__':
    unittest.main()
