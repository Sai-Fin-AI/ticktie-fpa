"""GL mapping: GL codes roll up to reporting accounts, nothing is dropped, and the mapping file round-trips."""
import unittest
from decimal import Decimal as D
from pathlib import Path

from streamlit.testing.v1 import AppTest

from finance import (UnmappedGL, aggregate, apply_gl_map, gl_detail, gl_lines, load_csv, load_gl_map,
                     looks_like_gl_codes, merge_datasets, sample_csv, sample_gl_csv, sample_gl_map_csv,
                     sample_gl_pl_csv, sample_pl_csv)
from gl_panel import mapping_workbook
from ingest import separate_csv, table_csv

APP = Path(__file__).resolve().parents[1] / 'app.py'
HEADER = 'month,country,cost_center,account,currency,actual,budget,forecast\n'
MAP = 'gl_code,gl_name,account\n510100,Salaries,Employee costs\n510200,Bonus,Employee costs\n540900,Recharges,Warehousing\n'


def gl_demo():
    data = merge_datasets(load_csv(sample_gl_csv()),
                          load_csv(sample_gl_pl_csv(), 'Sales'))
    return apply_gl_map(data, load_gl_map(sample_gl_map_csv()))


class RollUpTests(unittest.TestCase):
    def test_gl_demo_adds_up_to_the_account_demo(self):
        data, info = gl_demo()
        accounts = merge_datasets(load_csv(sample_csv()), load_csv(sample_pl_csv(), 'Sales'))
        self.assertEqual(list(aggregate(data, data.dims)), list(aggregate(accounts, accounts.dims)))
        self.assertEqual((info['gl_codes'], info['accounts'], info['unused']), (28, 11, []))
        self.assertTrue(any(r['actual'] < 0 for r in data))  # the credit GL code 540900

    def test_drill_down_adds_up_to_the_line(self):
        data, _ = gl_demo()
        for line in aggregate(data, data.dims):
            parts = gl_detail(data, line, 'budget')
            with self.subTest(line=(line['cost_center'], line['account'])):
                self.assertEqual(sum(p['actual'] for p in parts), line['actual'])
                self.assertEqual(sum(p['budget'] for p in parts), line['budget'])
        every = gl_lines(data, ('cost_center',), 'budget')
        self.assertEqual([abs(g['variance']) for g in every], sorted((abs(g['variance']) for g in every), reverse=True))
        top = every[0]  # largest GL movement first
        self.assertEqual((top['country'], top['gl_code'], top['variance'], top['direction']),
                         ('Australia', '410100', D(200000), 'Favorable'))  # sales above plan

    def test_unknown_gl_codes_stop_and_are_listed(self):
        data = load_csv(HEADER + '2026-08,India,IN-SAL,510100,INR,10,9,9\n2026-08,India,IN-SAL,599999,INR,1,1,1\n')
        with self.assertRaises(UnmappedGL) as caught:
            apply_gl_map(data, load_gl_map(MAP))
        self.assertEqual(caught.exception.codes, ['599999'])
        self.assertIn('599999', str(caught.exception))

    def test_account_level_values_pass_through(self):
        # Actuals post to GL codes, the budget is kept at account level: both land on Employee costs.
        data = load_csv(HEADER + '2026-08,India,IN-SAL,510100,INR,70,0,0\n2026-08,India,IN-SAL,510200,INR,40,0,0\n'
                                 '2026-08,India,IN-SAL,Employee costs,INR,0,100,100\n')
        data, _ = apply_gl_map(data, load_gl_map(MAP))
        (line,) = aggregate(data, data.dims)
        self.assertEqual((line['account'], line['actual'], line['budget']), ('Employee costs', D(110), D(100)))
        parts = {p['gl_code']: p for p in gl_detail(data, line, 'budget')}
        self.assertEqual(parts['Employee costs']['gl_name'], 'Booked at account level')

    def test_an_account_may_net_below_zero(self):
        data, _ = apply_gl_map(load_csv(HEADER + '2026-08,India,IN-OPS,540900,INR,-50,-40,-40\n'), load_gl_map(MAP))
        (line,) = aggregate(data, data.dims)
        self.assertEqual((line['account'], line['actual']), ('Warehousing', D(-50)))  # flagged by review_lines

    def test_separate_files_match_at_account_level(self):
        actuals = (['Month', 'Country', 'GL', 'Amount'], [['2026-08', 'India', '510100', 70], ['2026-08', 'India', '510200', 40]],
                   ['month', 'country', 'account', 'amount'], 'actuals.csv')
        budget = (['Month', 'Country', 'Account', 'Amount'], [['2026-08', 'India', 'Employee costs', 100]],
                  ['month', 'country', 'account', 'amount'], 'budget.csv')
        gl_map = load_gl_map(MAP)
        _, _, report = separate_csv({'actuals': actuals, 'budget': budget}, resolve=lambda c: gl_map.get(c, {'account': c})['account'])
        self.assertEqual((report['unbudgeted'], report['unspent']), ([], []))
        _, _, plain = separate_csv({'actuals': actuals, 'budget': budget})
        self.assertEqual(len(plain['unbudgeted']), 2)


class MappingFileTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(load_gl_map('GL Code,GL Description,Reporting account\n510100,Salaries,Employee costs\n'),
                         {'510100': {'account': 'Employee costs', 'gl_name': 'Salaries'}})
        cases = {'gl_code,gl_name\n1,x\n': 'needs a gl_code column and an account',
                 'gl_code,account\n1,A\n1,A\n': 'listed twice',
                 'gl_code,account\n1,\n': 'has no account',
                 'gl_code,account,pl_line\n1,A,Other income\n': 'not one of',
                 'gl_code,account,pl_line\n1,A,Sales\n2,A,Opex\n': 'two P&L lines'}
        for text, message in cases.items():
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                load_gl_map(text)

    def test_workbook_round_trips_and_flags_new_codes(self):
        gl_map = load_gl_map(sample_gl_map_csv())
        back = load_gl_map(table_csv(mapping_workbook(gl_map, list(gl_map)), 'gl_mapping.xlsx'))
        self.assertEqual(back, gl_map)
        with_new = mapping_workbook(gl_map, list(gl_map), ['000777'])
        text = table_csv(with_new, 'gl_mapping.xlsx')
        self.assertIn('000777,,,,New: fill in the account', text)  # leading zeros survive
        with self.assertRaisesRegex(ValueError, 'GL 000777 has no account'):
            load_gl_map(text)

    def test_looks_like_gl_codes(self):
        self.assertTrue(looks_like_gl_codes(['510100', 'GL-5102', '6100-200']))
        self.assertFalse(looks_like_gl_codes(['Travel', 'Employee costs', 'Consulting']))


class AppTests(unittest.TestCase):
    def test_demo_runs_on_gl_codes(self):
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        self.assertFalse(app.exception)
        self.assertTrue(any('28 GL codes roll up into 11 accounts' in c.value for c in app.caption))
        self.assertEqual({m.label: m.value for m in app.metric}['Net profit · INR'], '2,270,000')
        view = next(r for r in app.radio if r.label == 'View by')
        view.set_value('GL codes').run()
        self.assertFalse(app.exception)
        self.assertTrue(any(e.label.startswith('GL codes in this line') for e in app.expander))

    def test_gl_file_without_mapping_offers_a_template(self):
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        app.sidebar.file_uploader[0].upload('gl.csv', sample_gl_csv().encode(), 'text/csv').run()
        self.assertTrue(any('looks like GL codes' in i.value for i in app.info))

    def test_missing_code_stops_with_a_download(self):
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        app.sidebar.file_uploader[0].upload('gl.csv', sample_gl_csv().encode(), 'text/csv').run()
        partial = '\n'.join(sample_gl_map_csv().splitlines()[:-12]) + '\n'
        app.sidebar.file_uploader[3].upload('map.csv', partial.encode(), 'text/csv').run()
        self.assertTrue(any('not in the GL mapping' in e.value for e in app.error))
        app.sidebar.file_uploader[3].upload('map.csv', sample_gl_map_csv().encode(), 'text/csv').run()
        self.assertFalse(app.error)
        self.assertTrue(any('GL codes roll up into' in c.value for c in app.caption))


if __name__ == '__main__':
    unittest.main()
