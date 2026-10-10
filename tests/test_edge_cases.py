"""Edge cases found by the synthetic stress run (random datasets pushed through the whole pipeline)."""
import io
import unittest
from decimal import Decimal as D
from pathlib import Path

from pptx import Presentation
from streamlit.testing.v1 import AppTest

import validate
from agents.deck import _pts, make_deck
from agents.writer import to_markdown
from finance import aggregate, load_csv, merge_datasets, sample_pl_csv, statement
from ingest import guess_roles, normalize_month, separate_csv
from pack import build_pack, lock_pack

APP = Path(__file__).resolve().parents[1] / 'app.py'
BRANDS = 'month,country,profit_center,brand,account,currency,actual,budget,forecast\n'


def pack_of(text, default_line='Sales'):
    data = load_csv(text, default_line)
    return lock_pack(build_pack(aggregate(data, data.dims), 'budget', D(25000), D(10), {}, (D(50000), D(5))))


class SalesWithoutCostsTests(unittest.TestCase):
    """A P&L file with sales and cost of sales only: no operating expenses, so nothing sits just above EBITDA."""

    def test_statement_still_has_ebitda(self):
        rows = aggregate(load_csv(sample_pl_csv(), 'Sales'), ())
        labels = [e['label'] for e in statement(rows, 'budget') if e['kind'] == 'subtotal']
        self.assertEqual(labels[:2], ['Gross margin', 'EBITDA'])

    def test_deck_and_app(self):
        make_deck(pack_of(sample_pl_csv()), None)  # used to fail on the missing EBITDA tile
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        app.sidebar.file_uploader[0].upload('sales.csv', sample_pl_csv().encode(), 'text/csv').run()
        self.assertFalse(app.exception)
        self.assertEqual({m.label: m.value for m in app.metric}['EBITDA · INR'], '16,600,000')


class InputShapeTests(unittest.TestCase):
    def test_names_with_a_pipe_are_not_a_false_duplicate(self):
        main = load_csv('month,country,cost_center,account,currency,actual,budget,forecast\n'
                        '2026-08,India,"A | B",Travel,INR,1,1,1\n')
        extra = load_csv(BRANDS + '2026-08,India,A,B,Travel,INR,1,1,1\n')
        self.assertEqual(len(merge_datasets(main, extra)), 2)

    def test_separate_files_may_leave_the_material_blank(self):
        headers = ['Period', 'Entity', 'Profit Center', 'Material', 'GL Account', 'Amount']
        roles = guess_roles(headers, 'actuals')
        actuals = (headers, [['Aug-2026', 'India', 'PC-IN', 'M1', 'Product sales', 500],
                             ['Aug-2026', 'India', 'PC-IN', '', 'Travel', 20]], roles, 'a.csv')
        budget = (headers, [['2026-08', 'India', 'PC-IN', 'M1', 'Product sales', 450],
                            ['2026-08', 'India', 'PC-IN', '', 'Travel', 25]], roles, 'b.csv')
        text, _, report = separate_csv({'actuals': actuals, 'budget': budget})
        rows = load_csv(text, 'Sales')
        self.assertEqual([(r.get('sku'), r['actual'], r['budget']) for r in rows],
                         [('M1', D(500), D(450)), (None, D(20), D(25))])
        self.assertEqual(report['unbudgeted'], [])

    def test_sap_period_without_separator(self):
        self.assertEqual((normalize_month('202608'), normalize_month(202608.0)), ('2026-08', '2026-08'))

    def test_excel_number_saved_as_text_is_offered_as_a_match(self):
        roles = ['month', 'country', 'account', 'amount']
        specs = {'actuals': (['M', 'C', 'A', 'Amt'], [['2026-08', 'India', 510100, 1]], roles, 'a.csv'),
                 'budget': (['M', 'C', 'A', 'Amt'], [['2026-08', 'India', '510100.0', 1], ['2026-08', 'India', '0510 100', 1]],
                            roles, 'b.csv')}
        _, _, suggestions = validate.check(specs)
        self.assertEqual([(v['value'], v['difference']) for v in suggestions[0]['from']],
                         [('0510 100', 'separators (- . _ / or spaces) and leading zeros'),
                          ('510100.0', 'a trailing .0 (Excel number format)')])


class OutputTests(unittest.TestCase):
    def test_pl_commentary_title(self):
        pack = pack_of(sample_pl_csv())
        self.assertTrue(to_markdown(pack, {'executive_summary': [], 'lines': []}, [], 'x').startswith('# CFO commentary: 2026-08 P&L'))

    def test_margin_points_never_show_minus_zero(self):
        self.assertEqual((_pts('-0.04'), _pts('-0.06'), _pts('0')), ('0.0 pts', '−0.1 pts', '0.0 pts'))

    def test_long_names_and_many_brands_stay_on_the_slide(self):
        long = 'Regional Sales and Customer Development'
        text = 'month,country,cost_center,cc_name,account,currency,actual,budget,forecast\n' + ''.join(
            f'2026-08,{c},{c[:2]}-{k},{c} {long},Recruitment,INR,{-k * 1000 - 5000},{300000 + k},{300000}\n'
            for c in ('India', 'Singapore', 'Australia', 'United Arab Emirates') for k in range(3))
        sales = BRANDS + ''.join(f'2026-08,India,PC-IN,Brand {i:02d},Product sales,INR,{100000 + i * 40000},100000,100000\n'
                                 f'2026-08,India,PC-IN,Brand {i:02d},Cost of goods sold,INR,50000,50000,50000\n' for i in range(15))
        data = merge_datasets(load_csv(text), load_csv(sales, 'Sales'))
        pack = lock_pack(build_pack(aggregate(data, data.dims), 'budget', D(25000), D(10), {}, (D(50000), D(5))))
        deck = Presentation(io.BytesIO(make_deck(pack, None)['pptx']))
        footer = 7.0 * 914400  # the footer line sits at 7.02 in
        for number, slide in enumerate(deck.slides, 1):
            for shape in slide.shapes:
                if number > 1 and shape.top < footer:  # the title slide has a full-page background
                    with self.subTest(slide=number, shape=shape.name):
                        self.assertLessEqual(shape.top + shape.height, footer)
        products = next(s for s in deck.slides
                        if any('sales and gross margin by brand' in getattr(x, 'text', '').lower() for x in s.shapes))
        table = next(x for x in products.shapes if x.has_table).table
        self.assertEqual(len(table.rows), 9)  # eight brands that move most, plus the header
        self.assertIn('Brand 14', [table.cell(r, 0).text for r in range(1, 9)])  # the largest gain is always shown


if __name__ == '__main__':
    unittest.main()
