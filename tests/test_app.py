"""Exercise the real Streamlit entrypoint, widgets and upload validation."""
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest
from finance import sample_csv

APP = Path(__file__).resolve().parents[1] / 'app.py'


def table(app, column='Material'):
    """The dataframe on screen that has this column (the variance lines by default)."""
    return next(d.value for d in app.dataframe if column in d.value.columns)


class AppTests(unittest.TestCase):
    def new_app(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertEqual(len(app.exception), 0)
        return app

    def metrics(self, app):
        self.assertEqual(len(app.exception), 0)
        return {item.label: item.value for item in app.metric}

    def choose(self, app, label, value):
        next(item for item in app.selectbox if item.label == label).select(value).run()

    def deltas(self, app):
        return {item.label: item.delta for item in app.metric}

    def test_default_demo_is_a_full_p_and_l(self):
        app = self.new_app()
        self.assertEqual(self.metrics(app), {
            'Sales · INR': '30,500,000', 'EBITDA · INR': '4,225,000', 'Net profit · INR': '2,270,000',
            'Lines to explain': '16', 'Explained (confirmed)': '0%'})  # incl. 2 brand lines
        self.assertEqual(self.deltas(app)['Net profit · INR'], '−195,000 vs budget')
        self.assertEqual(len(table(app)), 51)  # 24 expense lines + 18 brand lines (sales, cost of sales) + 9 corporate P&L lines
        self.assertIn('Synthetic demo data', app.info[0].value)
        self.assertTrue(any('Gross margin 54.4% (-0.6 pts)' in c.value for c in app.caption))

    def test_expense_only_upload_keeps_the_expense_review(self):
        app = self.new_app()
        app.sidebar.file_uploader[0].upload('expenses.csv', sample_csv().encode(), 'text/csv').run()
        self.assertEqual(self.metrics(app), {
            'Actual expense · INR': '12,375,000', 'Budget · INR': '12,010,000',
            'Net variance · INR': '+365,000', 'Lines to explain': '14', 'Explained (confirmed)': '0%'})
        self.assertEqual(len(table(app)), 24)
        self.assertFalse(any(s.label == 'Waterfall for' for s in app.selectbox))

    def test_forecast_comparison(self):
        app = self.new_app()
        self.choose(app, 'Compare actuals against', 'forecast')
        self.assertEqual(self.deltas(app)['Net profit · INR'], '−97,500 vs forecast')
        self.assertEqual(self.deltas(app)['Sales · INR'], '+250,000 vs forecast')

    def test_country_filter_and_zero_budget(self):
        app = self.new_app()
        self.choose(app, 'Country', 'India')
        self.assertEqual(self.metrics(app)['Sales · INR'], '12,300,000')
        self.assertEqual(self.metrics(app)['Net profit · INR'], '1,664,000')
        frame = table(app)
        self.assertEqual(set(frame['Country']), {'India'})
        consulting = frame.loc[frame['Account'] == 'Consulting'].iloc[0]
        self.assertEqual(consulting['Variance %'], 'N/A')
        self.assertEqual(consulting['Material'], 'Yes')

    def test_threshold_change(self):
        app = self.new_app()
        next(x for x in app.number_input if x.label == 'Minimum variance (INR)').set_value(1000000).run()
        self.assertEqual(self.metrics(app)['Lines to explain'], '13')  # spread rule off; centre, brand and negative lines still flagged
        next(x for x in app.number_input if x.label == 'Line minimum variance (INR)').set_value(1000000).run()
        self.assertEqual(self.metrics(app)['Lines to explain'], '1')  # a negative balance needs a look whatever its size
        frame = table(app)
        flagged = frame.loc[frame['Material'] != 'No']
        self.assertEqual(list(flagged['Account']), ['Recruitment'])
        self.assertEqual((flagged.iloc[0]['Material'], flagged.iloc[0]['Flag']), ('Yes (negative balance)', '⚠️ Negative balance'))
        self.assertTrue(any('Negative balance: the actual is INR -5,000.00' in w.value for w in app.warning))

    def test_uploaded_values_replace_demo_and_label(self):
        app = self.new_app()
        changed = sample_csv('account').replace('2100000', '2200000', 1)
        app.sidebar.file_uploader[0].upload('changed.csv', changed.encode(), 'text/csv').run()
        self.assertEqual(self.metrics(app)['Actual expense · INR'], '12,475,000')
        self.assertIn('Uploaded data is loaded', app.info[0].value)
        self.assertFalse(any('Synthetic demo data is loaded' in x.value for x in app.info))
        app.sidebar.file_uploader[0].clear().run()
        self.assertEqual(self.metrics(app)['Sales · INR'], '30,500,000')
        self.assertIn('Synthetic demo data', app.info[0].value)

    def test_invalid_uploads_stop_analysis(self):
        sample = sample_csv('account')
        invalid = {
            'missing.csv': (sample.replace('2100000', '', 1).encode(), 'missing value'),
            'duplicate.csv': ((sample + sample.splitlines()[1] + '\n').encode(), 'duplicate'),
            'currency.csv': (sample.replace('INR', 'USD', 1).encode(), 'INR'),
            'encoding.csv': (b'\xff\xfe\xfa', 'decode'),
        }
        for name, (data, message) in invalid.items():
            with self.subTest(name=name):
                app = self.new_app()
                app.sidebar.file_uploader[0].upload(name, data, 'text/csv').run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.metric), 0)
                self.assertEqual(len(app.error), 1)
                self.assertIn(message, app.error[0].value)

    def test_analyse_by_level_rolls_up_cost_centres(self):
        app = self.new_app()
        self.assertTrue(any('SG-COR' in x.value for x in app.warning))  # completeness check from the master
        self.assertIn('Owner', table(app).columns)
        next(x for x in app.multiselect if x.label == 'Analyse by').unselect('cost_center').run()
        self.assertEqual(self.metrics(app), {
            'Sales · INR': '30,500,000', 'EBITDA · INR': '4,225,000', 'Net profit · INR': '2,270,000',
            'Lines to explain': '12', 'Explained (confirmed)': '0%'})
        self.assertEqual(len(table(app)), 42)  # 15 expense + 18 brand (sales, cost of sales) + 9 corporate lines
        # brand lines are still finer than country x account, so the line thresholds stay
        self.assertTrue(any(x.label == 'Line minimum variance (INR)' for x in app.number_input))
        next(x for x in app.selectbox if x.label == 'Sales and cost of sales by').set_value('total').run()
        self.assertFalse(any(x.label == 'Line minimum variance (INR)' for x in app.number_input))
        self.assertEqual(len(table(app)), 30)  # country x account: 15 expense + 6 sales and cost of sales + 9 corporate

    def test_rollup_views_show_gross_and_offsets(self):
        app = self.new_app()
        next(x for x in app.radio if x.label == 'View by').set_value('Cost / profit centre').run()
        frame = table(app, 'Net variance INR')
        centre = lambda name: frame.loc[frame['Cost / profit centre'] == name].iloc[0]
        # SG-OPS carries only costs: employee -90k, warehousing -80k, distribution +40k, tax -23k
        sg_ops = centre('Singapore | SG-OPS Singapore Operations')
        self.assertEqual((sg_ops['Net variance INR'], sg_ops['Gross movement INR']), (-153000, 233000))
        # the India profit centre mixes sales (+300k) and cost of sales (+250k): profit impact +50k, while its
        # brand lines move 1,470k gross (Nimbus down, Aurora and Breeze up; Aurora's input costs)
        pc_in = centre('India | PC-IN India Commercial')
        self.assertEqual((pc_in['Net variance INR'], pc_in['Gross movement INR']), (50000, 1470000))
        next(x for x in app.radio if x.label == 'View by').set_value('Brand').run()
        self.assertEqual(set(table(app, 'Net variance INR')['Brand']), {'Aurora', 'Nimbus', 'Breeze'})
        next(x for x in app.radio if x.label == 'View by').set_value('Function').run()
        self.assertEqual(set(table(app, 'Net variance INR')['Function']), {'Sales', 'Operations', 'Corporate', 'Commercial'})

    def test_extra_dimension_column_is_offered(self):
        app = self.new_app()
        text = ('month,country,cost_center,project,account,currency,actual,budget,forecast\n'
                '2026-08,India,IN-OPS,Alpha,Travel,INR,150000,100000,100000\n'
                '2026-08,India,IN-OPS,Beta,Travel,INR,90000,100000,100000\n')
        app.sidebar.file_uploader[0].upload('projects.csv', text.encode(), 'text/csv').run()
        self.assertEqual(len(app.exception), 0)
        level = next(x for x in app.multiselect if x.label == 'Analyse by')
        self.assertEqual(level.value, ['cost_center', 'project'])
        self.assertEqual(len(table(app)), 2)
        level.unselect('project').run()
        self.assertEqual(len(table(app)), 1)

    def test_two_simulated_sessions_keep_uploads_separate(self):
        first = self.new_app()
        changed = sample_csv('account').replace('2100000', '2200000', 1)
        first.sidebar.file_uploader[0].upload('changed.csv', changed.encode(), 'text/csv').run()
        second = self.new_app()
        self.assertEqual(self.metrics(first)['Actual expense · INR'], '12,475,000')
        self.assertEqual(self.metrics(second)['Sales · INR'], '30,500,000')


class DemoNotesTests(unittest.TestCase):
    def test_loaded_notes_fill_the_form_and_the_kpis(self):
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        self.assertEqual(app.text_area[0].value, '')
        next(b for b in app.button if b.label == 'Load demo explanations').click().run()
        self.assertFalse(app.exception)
        self.assertIn('A key retailer delisted Nimbus', app.text_area[0].value)  # the largest line comes first
        self.assertEqual(app.text_input[1].value, '300000')
        self.assertEqual(next(s for s in app.selectbox if s.label == 'Review status').value, 'Confirmed by reviewer')
        explained = next(m for m in app.metric if m.label == 'Explained (confirmed)')
        self.assertEqual(explained.value, '66%')  # 1,110,000 confirmed of 1,685,000 material movement
        self.assertTrue(any('Loaded 9 demo explanations' in s.value for s in app.success))


if __name__ == '__main__':
    unittest.main()
