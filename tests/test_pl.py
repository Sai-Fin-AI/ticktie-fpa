"""Full P&L: P&L lines, the sales file, sign logic, subtotals, margins and the bridge choices."""
import unittest
from decimal import Decimal as D
from pathlib import Path

from streamlit.testing.v1 import AppTest

from finance import (BRIDGES, aggregate, bridge, bridge_options, compare, load_csv, merge_datasets, pl_line_for,
                     review_lines, sample_csv, sample_pl_csv, statement)
from ingest import guess_roles

APP = Path(__file__).resolve().parents[1] / 'app.py'
HEADER = 'month,country,cost_center,account,currency,actual,budget,forecast\n'


def demo_rows():
    data = merge_datasets(load_csv(sample_csv()), load_csv(sample_pl_csv(), default_line='Sales'))
    return aggregate(data, data.dims)


class PLLineTests(unittest.TestCase):
    def test_account_names_place_themselves(self):
        cases = {'Product sales': 'Sales', 'Net revenue': 'Sales', 'Revenue': 'Sales', 'Cost of goods sold': 'Cost of sales',
                 'COGS': 'Cost of sales', 'Depreciation': 'Depreciation and amortisation', 'Amortization of software':
                 'Depreciation and amortisation', 'Interest expense': 'Interest', 'Income tax': 'Tax',
                 'Sales commission': 'Operating expenses', 'Payroll tax': 'Operating expenses', 'Travel': 'Operating expenses'}
        for account, line in cases.items():
            with self.subTest(account=account):
                self.assertEqual(pl_line_for(account), line)
        self.assertEqual(pl_line_for('Service fees', default='Sales'), 'Sales')  # unknown names in the sales file

    def test_pl_line_column_is_checked(self):
        text = HEADER.replace('currency', 'pl_line,currency')
        good = load_csv(text + '2026-08,India,IN-SAL,Fees,Revenue,INR,100,90,95\n')
        self.assertEqual(good[0]['pl_line'], 'Sales')  # "Revenue" is an accepted name for Sales
        with self.assertRaisesRegex(ValueError, 'not one of'):
            load_csv(text + '2026-08,India,IN-SAL,Fees,Other income,INR,100,90,95\n')
        with self.assertRaisesRegex(ValueError, 'two P&L lines'):
            load_csv(text + '2026-08,India,IN-SAL,Fees,Sales,INR,100,90,95\n2026-08,India,IN-OPS,Fees,Opex,INR,1,1,1\n')

    def test_merge_refuses_mismatched_files(self):
        main = load_csv(sample_csv())
        mixed = merge_datasets(main, load_csv(HEADER.replace('cost_center', 'profit_center,sku')
                                              + '2026-08,India,PC-IN,AUR-1,Product sales,INR,5,4,4\n', 'Sales'))
        self.assertEqual(mixed.dims, ('cost_center', 'sku'))  # levels may differ: costs by cost centre, sales by SKU
        self.assertEqual(mixed[-1]['dims'], ('cost_center', 'sku'))
        with self.assertRaisesRegex(ValueError, 'repeats lines'):
            merge_datasets(main, load_csv(sample_csv()))
        with self.assertRaisesRegex(ValueError, 'is for 2026-09'):
            merge_datasets(main, load_csv(HEADER + '2026-09,India,IN-SAL,Product sales,INR,1,1,1\n', 'Sales'))

    def test_sales_above_plan_is_favorable(self):
        self.assertEqual(compare(D(110), D(100), D(0), D(0), True)['direction'], 'Favorable')
        self.assertEqual(compare(D(110), D(100), D(0), D(0))['direction'], 'Unfavorable')
        lines = {(r['cost_center'], r.get('sku'), r['account']): r for r in review_lines(demo_rows(), 'budget', (D(1), D(0)))}
        self.assertEqual(lines[('PC-IN', 'NIM-100', 'Product sales')]['direction'], 'Unfavorable')  # sales below plan
        self.assertEqual(lines[('PC-IN', 'AUR-100', 'Product sales')]['direction'], 'Favorable')


class StatementTests(unittest.TestCase):
    def setUp(self):
        self.rows = demo_rows()
        self.pl = {e['label']: e for e in statement(self.rows, 'budget')}

    def test_subtotals_and_margins(self):
        expect = {'Sales': (30500000, 30000000, 500000), 'Gross margin': (16600000, 16500000, 100000),
                  'EBITDA': (4225000, 4490000, -265000), 'EBIT': (3325000, 3590000, -265000),
                  'Profit before tax': (3025000, 3290000, -265000), 'Net profit': (2270000, 2465000, -195000),
                  'Operating expenses': (12375000, 12010000, -365000), 'Tax': (755000, 825000, 70000)}
        for label, (actual, plan, impact) in expect.items():
            with self.subTest(label=label):
                e = self.pl[label]
                self.assertEqual((e['actual'], e['baseline'], e['impact']), (D(actual), D(plan), D(impact)))
        self.assertEqual(round(self.pl['Gross margin %']['pts'], 2), D('-0.57'))
        self.assertEqual(round(self.pl['EBITDA %']['actual_pct'], 1), D('13.9'))
        self.assertEqual(self.pl['Tax']['direction'], 'Favorable')  # tax below plan helps profit
        order = [e['label'] for e in statement(self.rows, 'budget') if e['kind'] in ('line', 'subtotal')]
        self.assertEqual(order, ['Sales', 'Cost of sales', 'Gross margin', 'Operating expenses', 'EBITDA',
                                 'Depreciation and amortisation', 'EBIT', 'Interest', 'Profit before tax', 'Tax', 'Net profit'])

    def test_expense_only_data_has_no_statement(self):
        rows = aggregate(load_csv(sample_csv()), ('cost_center',))
        self.assertEqual(statement(rows, 'budget'), [])
        self.assertEqual(bridge_options(rows), ['Costs'])

    def test_every_bridge_walks_from_start_to_end(self):
        self.assertEqual(bridge_options(self.rows), list(BRIDGES))
        for measure in BRIDGES:
            with self.subTest(measure=measure):
                walk = bridge(self.rows, 'budget', measure)
                self.assertEqual(walk['start'] + sum(v for _, v, _ in walk['steps']), walk['end'])
        ebitda = bridge(self.rows, 'budget', 'EBITDA')
        self.assertEqual((ebitda['start'], ebitda['end']), (D(4490000), D(4225000)))
        costs = bridge(self.rows, 'budget', 'Costs')
        self.assertEqual(costs['steps'][0], ('Cost of goods sold', D(400000), False))  # more cost is unfavorable
        self.assertEqual(bridge(self.rows, 'budget', 'Sales')['steps'], [('Product sales', D(500000), True)])


class WaterfallChartTests(unittest.TestCase):
    def test_every_bar_keeps_its_own_label(self):
        from charts import bridge_chart
        walk = bridge(demo_rows(), 'budget', 'Costs')
        spec = bridge_chart(walk, 'budget').to_dict()
        layer = spec['layer'][0]
        self.assertFalse(layer['encoding']['x']['axis']['labelOverlap'])  # never hide labels, or names shift bars
        steps = [row['step'] for row in spec['datasets'][next(iter(spec['datasets']))]]
        self.assertEqual(steps, ['Budget costs', 'Cost of goods sold', 'Distribution', 'Consulting', 'Income tax',
                                 'Employee costs', 'Warehousing', 'Recruitment', 'Actual costs'])


class SalesUploadTests(unittest.TestCase):
    def test_column_dropdown_knows_pl_line(self):
        self.assertEqual(guess_roles(['Month', 'Account', 'P&L line', 'Actual', 'Budget', 'Forecast'], 'pl')[2], 'pl_line')

    def test_uploading_expenses_and_sales_gives_the_p_and_l(self):
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        app.sidebar.file_uploader[0].upload('expenses.csv', sample_csv().encode(), 'text/csv').run()
        self.assertIn('Actual expense · INR', [m.label for m in app.metric])
        app.sidebar.file_uploader[1].upload('sales.csv', sample_pl_csv().encode(), 'text/csv').run()
        self.assertFalse(app.exception)
        self.assertEqual({m.label: m.value for m in app.metric}['Net profit · INR'], '2,270,000')
        waterfall = next(s for s in app.selectbox if s.label == 'Waterfall for')
        self.assertEqual(waterfall.options, list(BRIDGES))
        waterfall.set_value('Sales').run()
        self.assertFalse(app.exception)

    def test_sales_file_alone_keeps_the_demo(self):
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        app.sidebar.file_uploader[1].upload('sales.csv', sample_pl_csv().encode(), 'text/csv').run()
        self.assertTrue(any('Upload the main expense file as well' in i.value for i in app.info))


if __name__ == '__main__':
    unittest.main()
