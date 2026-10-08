"""Exercise the real Streamlit entrypoint, widgets and upload validation."""
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest
from finance import sample_csv

APP = Path(__file__).resolve().parents[1] / 'app.py'


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

    def test_default_budget_totals(self):
        app = self.new_app()
        self.assertEqual(self.metrics(app), {
            'Actual expense · INR': '12,380,000', 'Budget · INR': '12,000,000',
            'Net variance · INR': '+380,000', 'Lines to explain': '13', 'Explained (confirmed)': '0%'})
        self.assertEqual(len(app.dataframe[0].value), 23)
        self.assertIn('Synthetic demo data', app.info[0].value)

    def test_forecast_comparison(self):
        app = self.new_app()
        self.choose(app, 'Compare actuals against', 'forecast')
        self.assertEqual(self.metrics(app)['Forecast · INR'], '12,190,000')
        self.assertEqual(self.metrics(app)['Net variance · INR'], '+190,000')

    def test_country_filter_and_zero_budget(self):
        app = self.new_app()
        self.choose(app, 'Country', 'India')
        self.assertEqual(self.metrics(app)['Actual expense · INR'], '4,315,000')
        self.assertEqual(self.metrics(app)['Budget · INR'], '4,000,000')
        frame = app.dataframe[0].value
        self.assertEqual(set(frame['Country']), {'India'})
        consulting = frame.loc[frame['Account'] == 'Consulting'].iloc[0]
        self.assertEqual(consulting['Variance %'], 'N/A')
        self.assertEqual(consulting['Material'], 'Yes')

    def test_threshold_change(self):
        app = self.new_app()
        next(x for x in app.number_input if x.label == 'Minimum variance (INR)').set_value(1000000).run()
        self.assertEqual(self.metrics(app)['Lines to explain'], '10')  # spread rule off; cost centre lines still flagged
        next(x for x in app.number_input if x.label == 'Line minimum variance (INR)').set_value(1000000).run()
        self.assertEqual(self.metrics(app)['Lines to explain'], '0')
        self.assertTrue(any('No account movements meet' in x.value for x in app.markdown))

    def test_uploaded_values_replace_demo_and_label(self):
        app = self.new_app()
        changed = sample_csv('account').replace('2100000', '2200000', 1)
        app.file_uploader[0].upload('changed.csv', changed.encode(), 'text/csv').run()
        self.assertEqual(self.metrics(app)['Actual expense · INR'], '12,480,000')
        self.assertIn('Uploaded data is loaded', app.info[0].value)
        self.assertFalse(any('Synthetic demo data is loaded' in x.value for x in app.info))
        app.file_uploader[0].clear().run()
        self.assertEqual(self.metrics(app)['Actual expense · INR'], '12,380,000')
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
                app.file_uploader[0].upload(name, data, 'text/csv').run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.metric), 0)
                self.assertEqual(len(app.error), 1)
                self.assertIn(message, app.error[0].value)

    def test_analyse_by_level_rolls_up_cost_centres(self):
        app = self.new_app()
        self.assertTrue(any('SG-COR' in x.value for x in app.warning))  # completeness check from the master
        self.assertIn('Owner', app.dataframe[0].value.columns)
        next(x for x in app.multiselect if x.label == 'Analyse by').unselect('cost_center').run()
        self.assertEqual(self.metrics(app), {
            'Actual expense · INR': '12,380,000', 'Budget · INR': '12,000,000',
            'Net variance · INR': '+380,000', 'Lines to explain': '9', 'Explained (confirmed)': '0%'})
        self.assertEqual(len(app.dataframe[0].value), 14)
        self.assertFalse(any(x.label == 'Line minimum variance (INR)' for x in app.number_input))

    def test_rollup_views_show_gross_and_offsets(self):
        app = self.new_app()
        next(x for x in app.radio if x.label == 'View by').set_value('Cost centre').run()
        frame = app.dataframe[0].value
        sg_ops = frame.loc[frame['Cost centre'] == 'Singapore | SG-OPS Singapore Operations'].iloc[0]
        self.assertEqual((sg_ops['Net variance INR'], sg_ops['Gross movement INR']), (-130000, 210000))
        next(x for x in app.radio if x.label == 'View by').set_value('Function').run()
        self.assertEqual(set(app.dataframe[0].value['Function']), {'Sales', 'Operations', 'Corporate'})

    def test_extra_dimension_column_is_offered(self):
        app = self.new_app()
        text = ('month,country,cost_center,project,account,currency,actual,budget,forecast\n'
                '2026-08,India,IN-OPS,Alpha,Travel,INR,150000,100000,100000\n'
                '2026-08,India,IN-OPS,Beta,Travel,INR,90000,100000,100000\n')
        app.file_uploader[0].upload('projects.csv', text.encode(), 'text/csv').run()
        self.assertEqual(len(app.exception), 0)
        level = next(x for x in app.multiselect if x.label == 'Analyse by')
        self.assertEqual(level.value, ['cost_center', 'project'])
        self.assertEqual(len(app.dataframe[0].value), 2)
        level.unselect('project').run()
        self.assertEqual(len(app.dataframe[0].value), 1)

    def test_two_simulated_sessions_keep_uploads_separate(self):
        first = self.new_app()
        changed = sample_csv('account').replace('2100000', '2200000', 1)
        first.file_uploader[0].upload('changed.csv', changed.encode(), 'text/csv').run()
        second = self.new_app()
        self.assertEqual(self.metrics(first)['Actual expense · INR'], '12,480,000')
        self.assertEqual(self.metrics(second)['Actual expense · INR'], '12,380,000')


if __name__ == '__main__':
    unittest.main()
