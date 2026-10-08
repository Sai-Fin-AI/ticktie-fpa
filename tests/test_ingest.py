"""Column mapping, Excel/CSV reading and matching separate actuals, budget and forecast files."""
import io
import unittest
from decimal import Decimal as D
from pathlib import Path

from openpyxl import Workbook
from streamlit.testing.v1 import AppTest

from finance import load_csv, sample_csv
from ingest import (check_roles, combined_csv, guess_roles, normalize_month, parse_amount, read_table,
                    sample_actuals_csv, sample_budget_xlsx, sample_forecast_csv, separate_csv)

APP = Path(__file__).resolve().parents[1] / 'app.py'


def spec(kind, data, name):
    headers, rows = read_table(data, name)
    return headers, rows, guess_roles(headers, kind), name


def lines(rows):
    return sorted((r['country'], r.get('cost_center'), r['account'], r['actual'], r['budget'], r['forecast']) for r in rows)


class MappingTests(unittest.TestCase):
    def test_guesses_from_system_style_headers(self):
        headers = ['Posting Period', 'Entity', 'Cost Center', 'GL Account', 'Description', 'Amount (INR)', 'Project']
        self.assertEqual(guess_roles(headers, 'actuals'),
                         ['month', 'country', 'cost_center', 'account', 'ignore', 'amount', 'extra'])
        self.assertEqual(guess_roles(['Period', 'GL', 'Actual', 'Budget', 'Outlook'], 'combined'),
                         ['month', 'account', 'actual', 'budget', 'forecast'])
        self.assertEqual(guess_roles(['GL Code', 'GL Name'], 'actuals'), ['account', 'ignore'])

    def test_role_problems_are_explained(self):
        self.assertIn('Choose a column for Amount.', check_roles(['month', 'account'], 'budget', 'India'))
        self.assertTrue(any('entity name' in p for p in check_roles(['month', 'account', 'amount'], 'budget')))
        self.assertEqual(check_roles(['month', 'account', 'amount'], 'budget', 'India'), [])
        self.assertTrue(any('more than one column' in p for p in check_roles(['month', 'account', 'amount', 'amount'], 'budget', 'X')))

    def test_months_and_amounts(self):
        from datetime import datetime
        for value in ('2026-08', '2026-08-31', '2026/8', '08/2026', 'Aug-26', 'Aug-2026', 'August 2026', '2026 Aug',
                      datetime(2026, 8, 31)):
            with self.subTest(value=value):
                self.assertEqual(normalize_month(value), '2026-08')
        for bad in ('31/08/2026', '2026-13', 'Q3 2026', ''):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_month(bad)
        self.assertEqual(parse_amount('1,20,000.50'), D('120000.50'))
        self.assertEqual(parse_amount('(20,000)', allow_negative=True), D('-20000'))
        self.assertEqual(parse_amount(1234.56), D('1234.56'))
        for bad in ('(5)', '-1', '1.001', 'abc'):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                parse_amount(bad)

    def test_combined_file_with_other_names_excel_and_no_currency(self):
        book = Workbook()
        sheet = book.active
        sheet.append(('Period', 'GL', 'Actual', 'Budget', 'Outlook'))
        sheet.append(('Aug-2026', 'Travel', 150000, 100000, 120000.0))
        data = io.BytesIO()
        book.save(data)
        headers, rows, roles, name = spec('combined', data.getvalue(), 'plan.xlsx')
        rows_out = load_csv(combined_csv(headers, rows, roles, name, country='India'))
        self.assertEqual(lines(rows_out), [('India', None, 'Travel', D('150000'), D('100000'), D('120000'))])
        self.assertEqual(rows_out[0]['currency'], 'INR')


class SeparateFileTests(unittest.TestCase):
    def files(self, **override):
        files = {'actuals': spec('actuals', sample_actuals_csv().encode(), 'actuals.csv'),
                 'budget': spec('budget', sample_budget_xlsx(), 'budget.xlsx'),
                 'forecast': spec('forecast', sample_forecast_csv().encode(), 'forecast.csv')}
        files.update(override)
        return {k: v for k, v in files.items() if v is not None}

    def test_separate_files_rebuild_the_combined_demo(self):
        text, attrs, report = separate_csv(self.files())
        self.assertEqual(lines(load_csv(text)), lines(load_csv(sample_csv())))
        totals = {f['kind']: (f['rows'], f['lines'], f['other_months'], f['total']) for f in report['files']}
        self.assertEqual(totals['actuals'], (46, 23, 0, D('12380000')))
        self.assertEqual(totals['budget'], (252, 21, 11, D('12000000')))
        self.assertEqual(report['unbudgeted'], ['India | IN-COR | Consulting', 'Australia | AU-COR | Consulting'])
        self.assertEqual(report['unspent'], [])

    def test_budget_only_lines_and_no_forecast(self):
        budget = 'Month,Country,CC,GL,Budget\n2026-08,India,IN-OPS,Travel,5000\n'
        text, _, report = separate_csv(self.files(budget=spec('budget', budget.encode(), 'b.csv'), forecast=None))
        rows = load_csv(text)
        self.assertEqual(report['unspent'], ['India | IN-OPS | Travel'])
        self.assertFalse(report['forecast'])
        travel = next(r for r in rows if r['cost_center'] == 'IN-OPS' and r['account'] == 'Travel')
        self.assertEqual((travel['actual'], travel['budget'], travel['forecast']), (D('0'), D('5000'), D('5000')))

    def test_budget_without_cost_centres_matches_at_account_level(self):
        budget = 'Month,Country,GL,Budget\n2026-08,India,Travel,200000\n'
        text, _, report = separate_csv(self.files(budget=spec('budget', budget.encode(), 'b.csv'), forecast=None))
        self.assertEqual(report['dims'], [])
        self.assertEqual(report['dropped_dims'], ['cost_center'])
        travel = next(r for r in load_csv(text) if r['country'] == 'India' and r['account'] == 'Travel')
        self.assertEqual((travel['actual'], travel['budget']), (D('140000'), D('200000')))

    def test_blocking_problems(self):
        two_months = sample_actuals_csv().replace('Aug-2026', 'Sep-2026', 1)
        with self.assertRaisesRegex(ValueError, 'one month'):
            separate_csv(self.files(actuals=spec('actuals', two_months.encode(), 'a.csv')))
        july = 'Month,Country,CC,GL,Budget\n2026-07,India,IN-OPS,Travel,5000\n'
        with self.assertRaisesRegex(ValueError, 'no rows for 2026-08'):
            separate_csv(self.files(budget=spec('budget', july.encode(), 'b.csv')))
        credit = sample_actuals_csv() + 'Aug-2026,India,IN-OPS,Warehousing,Reversal,"(900,000)"\n'
        with self.assertRaisesRegex(ValueError, 'nets to a negative'):
            separate_csv(self.files(actuals=spec('actuals', credit.encode(), 'a.csv')))
        blank = sample_actuals_csv().replace('Aug-2026,India,IN-SAL,Employee costs,Monthly posting,', 'Aug-2026,India,IN-SAL,Employee costs,Monthly posting,,', 1)
        with self.assertRaisesRegex(ValueError, 'row 2'):
            separate_csv(self.files(actuals=spec('actuals', blank.encode(), 'a.csv')))


class IngestAppTests(unittest.TestCase):
    def metrics(self, app):
        self.assertEqual(len(app.exception), 0)
        return {m.label: m.value for m in app.metric}

    def test_separate_uploads_reconcile_to_the_demo(self):
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        next(r for r in app.radio if r.label == 'Data source').set_value('Separate files').run()
        self.assertTrue(any('Upload at least the actuals and budget' in x.value for x in app.info))
        app.file_uploader[0].upload('actuals.csv', sample_actuals_csv().encode(), 'text/csv').run()
        app.file_uploader[1].upload('budget.xlsx', sample_budget_xlsx(),
                                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet').run()
        self.assertEqual(self.metrics(app)['Actual expense · INR'], '12,380,000')
        self.assertEqual(self.metrics(app)['Budget · INR'], '12,000,000')
        self.assertTrue(any('2 line(s) have actuals but no budget' in x.value for x in app.warning))
        table = next(x.value for x in app.markdown if x.value.startswith('| File |'))
        self.assertEqual(table.count('✅'), 2)
        next(s for s in app.selectbox if s.label == 'Compare actuals against').select('forecast').run()
        self.assertTrue(any('No forecast file' in x.value for x in app.error))
        app.file_uploader[2].upload('forecast.csv', sample_forecast_csv().encode(), 'text/csv').run()
        self.assertEqual(self.metrics(app)['Forecast · INR'], '12,190,000')

    def test_dropdown_fixes_an_unrecognised_column(self):
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        text = sample_csv('account').replace('actual,budget', 'Spend,budget', 1)
        app.file_uploader[0].upload('odd.csv', text.encode(), 'text/csv').run()
        self.assertTrue(any('Choose a column for Actual amount' in x.value for x in app.error))
        next(s for s in app.selectbox if s.label == '“Spend”').select('actual').run()
        self.assertEqual(self.metrics(app)['Actual expense · INR'], '12,380,000')


if __name__ == '__main__':
    unittest.main()
