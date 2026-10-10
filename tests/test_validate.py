"""Rule-based data checks: safe fixes are automatic, matches need the reviewer's tick, corrupted codes stop."""
import csv
import io
import unittest
from decimal import Decimal as D
from pathlib import Path

from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

import validate
from finance import aggregate, load_csv
from ingest import separate_csv
from pack import build_pack, pack_excel, pack_markdown

APP = Path(__file__).resolve().parents[1] / 'app.py'
ROLES = ['month', 'country', 'cost_center', 'account', 'amount']


def spec(name, rows):
    return (['Month', 'Country', 'CC', 'GL', 'Amount'], rows, ROLES, name)


ACTUALS = spec('actuals.csv', [['2026-08', 'India', 'IN-SAL', '510100', 120000], ['2026-08', 'India', 'IN-SAL ', '520100', 30000]])
BUDGET = spec('budget.xlsx', [['2026-08', 'India', 'IN-SAL', '00510100', 100000], ['2026-08', 'India', 'in-sal', 520100, 40000]])


class RuleTests(unittest.TestCase):
    def test_match_key_and_difference(self):
        self.assertEqual({validate.match_key(v) for v in ('00510100', '510100', '5101-00', ' 510.100')}, {'510100'})
        self.assertEqual(validate.match_key('GL-0510'), validate.match_key('gl 510'))
        self.assertNotEqual(validate.match_key('510100'), validate.match_key('510101'))
        self.assertEqual(validate.difference('in-sal', 'IN-SAL'), 'upper/lower case')
        self.assertEqual(validate.difference('5101-00', '510100'), 'separators (- . _ / or spaces)')
        self.assertEqual(validate.difference('00510100', '510100'), 'leading zeros')

    def test_text_and_number_codes_already_match(self):
        _, _, suggestions = validate.check({'actuals': spec('a.csv', [['2026-08', 'India', 'X', '520100', 1]]),
                                            'budget': spec('b.xlsx', [['2026-08', 'India', 'X', 520100.0, 1]])})
        self.assertEqual(suggestions, [])  # Excel's 520100.0 and the text "520100" are the same code

    def test_spaces_are_fixed_and_spellings_suggested(self):
        cleaned, auto, suggestions = validate.check({'actuals': ACTUALS, 'budget': BUDGET})
        self.assertEqual([(a['check'], a['file'], a['count']) for a in auto], [('Extra spaces removed', 'actuals.csv', 1)])
        self.assertEqual(cleaned['actuals'][1][1][2], 'IN-SAL')
        found = {s['id']: (s['to'], [v['value'] for v in s['from']]) for s in suggestions}
        self.assertEqual(found, {'account:510100': ('510100', ['00510100']),  # the actuals' spelling wins
                                 'cost_center:INSAL': ('IN-SAL', ['in-sal'])})

    def test_nothing_merges_without_a_tick(self):
        cleaned, auto, suggestions = validate.check({'actuals': ACTUALS, 'budget': BUDGET})
        _, _, report = separate_csv(validate.apply(cleaned, suggestions, []))
        self.assertEqual(len(report['unbudgeted']), 2)
        self.assertTrue(all(e['action'] in ('Fixed automatically', 'Left as is (not accepted)')
                            for e in validate.log(auto, suggestions, [])))
        ids = [s['id'] for s in suggestions]
        text, _, report = separate_csv(validate.apply(cleaned, suggestions, ids))
        self.assertEqual((report['unbudgeted'], report['unspent']), ([], []))
        rows = {r['account']: r for r in load_csv(text)}
        self.assertEqual((rows['510100']['actual'], rows['510100']['budget']), (D(120000), D(100000)))
        entries = validate.log(auto, suggestions, ids)
        self.assertIn('Treated as "510100" (accepted by reviewer)', [e['action'] for e in entries])

    def test_gl_mapping_spelling_is_the_master(self):
        _, _, suggestions = validate.check({'actuals': ACTUALS}, gl_codes=['00510100', '00520100'])
        self.assertEqual({s['to'] for s in suggestions if s['field'] == 'account'}, {'00510100', '00520100'})

    def test_corrupted_codes_stop(self):
        for value, message in (('5.101E+11', 'scientific notation'), (1234567890123456.0, 'too long')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                validate.check({'actuals': spec('a.csv', [['2026-08', 'India', 'X', value, 1]])})


class AuditTrailTests(unittest.TestCase):
    def test_log_is_fingerprinted_and_exported(self):
        rows = aggregate(load_csv('month,country,account,currency,actual,budget,forecast\n2026-08,India,Travel,INR,10,9,9\n'), ())
        entries = [{'check': 'Extra spaces removed', 'file': 'a.csv', 'detail': 'Cost centre: 1 value(s)', 'action': 'Fixed automatically'}]
        plain, logged = build_pack(rows, 'budget', D(1), D(1), {}), build_pack(rows, 'budget', D(1), D(1), {}, data_log=entries)
        self.assertNotIn('data_checks', plain)  # no checks, no change to the pack (demo recordings stay valid)
        self.assertNotEqual(plain['content_hash'], logged['content_hash'])
        sheet = load_workbook(io.BytesIO(pack_excel(logged)))['Data checks']
        self.assertEqual([c.value for c in sheet[2]], ['Extra spaces removed', 'a.csv', 'Cost centre: 1 value(s)', 'Fixed automatically'])
        self.assertIn('## Data checks', pack_markdown(logged))


class AppTests(unittest.TestCase):
    def test_reviewer_accepts_a_match(self):
        def upload(rows):
            out = io.StringIO()
            csv.writer(out).writerows([['Month', 'Country', 'Cost Center', 'GL Account', 'Amount']] + rows)
            return out.getvalue().encode()

        app = AppTest.from_file(str(APP), default_timeout=60).run()
        app.sidebar.radio[0].set_value('Separate files').run()
        app.sidebar.file_uploader[0].upload('actuals.csv', upload(ACTUALS[1]), 'text/csv').run()
        app.sidebar.file_uploader[1].upload('budget.csv', upload(BUDGET[1]), 'text/csv').run()
        self.assertFalse(app.exception)
        self.assertTrue(any('2 possible match(es) to review' in e.label for e in app.expander))
        self.assertTrue(any('treated as unbudgeted' in w.value for w in app.warning))
        for box in app.checkbox:
            if box.key and box.key.startswith('datafix_'):
                box.check()
        app.run()
        self.assertFalse(app.exception)
        self.assertFalse(any('treated as unbudgeted' in w.value for w in app.warning))


if __name__ == '__main__':
    unittest.main()
