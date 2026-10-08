import unittest
from pathlib import Path
from finance import analyze, load_csv, sample_csv
from evidence import make_note, evidence_commentary
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / 'app.py'

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.row = next(r for r in analyze(load_csv(sample_csv('account')), 'forecast') if r['country'] == 'India' and r['account'] == 'Distribution')

    def note(self, amount='100000', status='Confirmed by reviewer', **kwargs):
        args = dict(reason='Extra freight for an urgent shipment.', reference='Fictional freight note', amount=amount, status=status, timing='One-off')
        args.update(kwargs)
        return make_note(self.row, 'forecast', **args)

    def test_no_full_and_partial_explanations(self):
        self.assertIn('does not establish the cause', evidence_commentary(self.row, 'forecast'))
        full = evidence_commentary(self.row, 'forecast', self.note())
        for expected in ('INR 100,000.00 above forecast (9.1%', 'Unexplained amount: INR 0.00', 'Fictional freight note', 'NOTE-'):
            self.assertIn(expected, full)
        self.assertIn('Unexplained amount: INR 40,000.00', evidence_commentary(self.row, 'forecast', self.note('60000')))

    def test_unconfirmed_and_conflicting_not_counted(self):
        for status in ('Unconfirmed', 'Conflicting explanations'):
            text = evidence_commentary(self.row, 'forecast', self.note(status=status))
            self.assertIn('Unexplained amount: INR 100,000.00', text)
            self.assertIn('not counted', text)

    def test_invalid_notes_rejected(self):
        for amount in ('-1', '100001', 'NaN', 'Infinity', '1.001', '', 'abc'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.note(amount)
        for values in ({'reason': ' '}, {'reference': ''}, {'status': 'verified'}, {'timing': 'definitely'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.note(**values)

    def test_note_does_not_cross_comparison_or_record(self):
        note = self.note()
        for row, basis in ((self.row, 'budget'), ({**self.row, 'month': '2026-09'}, 'forecast'), ({**self.row, 'country': 'Australia'}, 'forecast')):
            self.assertNotIn('Extra freight', evidence_commentary(row, basis, note))

    def test_favorable_is_not_proven_savings(self):
        row = next(r for r in analyze(load_csv(sample_csv('account'))) if r['variance'] < 0)
        self.assertIn('does not by itself establish lasting savings', evidence_commentary(row, 'budget'))

class EvidenceAppTests(unittest.TestCase):
    def widget(self, group, label):
        return next(x for x in group if x.label == label)

    def ready(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.file_uploader[0].upload('account.csv', sample_csv('account').encode(), 'text/csv').run()
        self.widget(app.selectbox, 'Country').select('India').run()
        self.widget(app.selectbox, 'Compare actuals against').select('forecast').run()
        self.assertFalse(app.exception)
        return app

    def save(self, app, amount='60000', status='Confirmed by reviewer'):
        self.widget(app.text_area, 'Business explanation').set_value('Extra freight for an urgent shipment.')
        self.widget(app.text_input, 'Supporting reference').set_value('Fictional freight note')
        self.widget(app.text_input, 'Amount explained (INR)').set_value(amount)
        self.widget(app.selectbox, 'Review status').select(status)
        self.widget(app.selectbox, 'Timing').select('One-off')
        self.widget(app.button, 'Save explanation').click().run()
        self.assertFalse(app.exception)

    def text(self, app):
        return '\n'.join(x.value for x in app.text)

    def test_save_update_remove(self):
        app = self.ready()
        self.save(app)
        self.assertIn('Unexplained amount: INR 40,000.00', self.text(app))
        self.save(app, '100000')
        self.assertIn('Unexplained amount: INR 0.00', self.text(app))
        self.widget(app.button, 'Remove saved explanation').click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('Extra freight', self.text(app))
        self.assertEqual(self.widget(app.text_area, 'Business explanation').value, '')

    def test_filters_comparison_and_changed_data(self):
        app = self.ready()
        self.save(app)
        self.widget(app.selectbox, 'Compare actuals against').select('budget').run()
        self.assertNotIn('Extra freight', self.text(app))
        self.widget(app.selectbox, 'Compare actuals against').select('forecast').run()
        self.assertIn('Extra freight', self.text(app))
        self.widget(app.selectbox, 'Country').select('Australia').run()
        self.assertNotIn('Extra freight', self.text(app))
        self.widget(app.selectbox, 'Country').select('India').run()
        self.assertIn('Extra freight', self.text(app))
        app.file_uploader[0].upload('new.csv', sample_csv('account').encode(), 'text/csv').run()
        self.assertFalse(app.exception)
        self.assertNotIn('Extra freight', self.text(app))
        self.assertTrue(any('cleared' in x.value for x in app.warning))
        app.file_uploader[0].clear().run()
        self.assertNotIn('Extra freight', self.text(app))

    def test_cost_centre_note_shows_owner_and_rolls_up(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        self.widget(app.selectbox, 'Country').select('India').run()
        box = self.widget(app.selectbox, 'Account to explain')
        target = next(o for o in box.options if 'IN-OPS India Operations | Distribution' in o)
        box.select(target).run()
        self.assertTrue(any('Owner: Arjun Mehta' in x.value for x in app.caption))
        self.save(app, '180000')
        self.assertTrue(any('Explanation saved' in x.value for x in app.success))
        self.assertIn('Unexplained amount: INR 60,000.00', self.text(app))
        next(x for x in app.radio if x.label == 'View by').set_value('Account').run()
        frame = app.dataframe[0].value
        row = frame.loc[frame['Account'] == 'India | Distribution'].iloc[0]
        self.assertEqual((row['Explained INR'], row['Gross movement INR'], row['Net variance INR']), (180000, 280000, 200000))
        self.assertIn('Offsetting', row['Flags'])

    def test_invalid_save_and_session_isolation(self):
        app = self.ready()
        self.save(app)
        self.save(app, '100001')
        self.assertTrue(any('cannot exceed' in x.value for x in app.error))
        self.assertIn('Unexplained amount: INR 40,000.00', self.text(app))
        self.assertNotIn('Extra freight', self.text(self.ready()))

    def test_conflict_stays_unresolved(self):
        app = self.ready()
        self.save(app, '100000', 'Conflicting explanations')
        self.assertIn('Unexplained amount: INR 100,000.00', self.text(app))
        self.assertIn('not counted', self.text(app))

if __name__ == '__main__':
    unittest.main()
