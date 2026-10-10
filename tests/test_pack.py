import io
import json
import unittest
from decimal import Decimal as D
from pathlib import Path

from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

from finance import analyze, load_csv, sample_csv
from evidence import make_note, note_key
from pack import build_pack, lock_pack, load_pack, pack_json, pack_excel, pack_markdown, export_notes, import_notes

APP = Path(__file__).resolve().parents[1] / 'app.py'
AMOUNT, PCT = D('50000'), D('5')


def india_distribution(rows, basis='forecast'):
    return next(r for r in analyze(rows, basis) if r['country'] == 'India' and r['account'] == 'Distribution')


class PackTests(unittest.TestCase):
    def setUp(self):
        self.rows = load_csv(sample_csv('account'))
        self.row = india_distribution(self.rows)

    def notes(self, amount='100000', status='Confirmed by reviewer'):
        note = make_note(self.row, 'forecast', 'Extra freight for an urgent shipment.', 'Fictional freight note',
                         amount, status, 'One-off', 'Review approvals.')
        return {note['key']: note}

    def test_totals_reconcile_to_engine(self):
        pack = build_pack(self.rows, 'budget', AMOUNT, PCT, {})
        self.assertEqual((pack['totals']['actual'], pack['totals']['baseline'], pack['totals']['variance']),
                         ('12375000', '12010000', '365000'))
        self.assertEqual((pack['summary']['rows'], pack['summary']['material'], pack['scope']), (16, 10, 'All countries'))
        material = [r for r in analyze(self.rows, 'budget') if r['material']]
        # plus the Recruitment line: below the thresholds, but a negative balance always needs an explanation
        self.assertEqual(D(pack['summary']['unexplained']), sum(abs(r['variance']) for r in material) + D('15000'))
        self.assertEqual(pack['summary']['open_items'], 10)

    def test_only_confirmed_full_notes_close_items(self):
        empty = build_pack(self.rows, 'forecast', AMOUNT, PCT, {})['summary']
        partial = build_pack(self.rows, 'forecast', AMOUNT, PCT, self.notes('60000'))['summary']
        full = build_pack(self.rows, 'forecast', AMOUNT, PCT, self.notes())['summary']
        unconfirmed = build_pack(self.rows, 'forecast', AMOUNT, PCT, self.notes(status='Unconfirmed'))['summary']
        self.assertEqual(D(partial['explained']), D('60000'))
        self.assertEqual(D(empty['unexplained']) - D(partial['unexplained']), D('60000'))
        self.assertEqual(partial['open_items'], empty['open_items'])
        self.assertEqual(full['open_items'], empty['open_items'] - 1)
        self.assertEqual((D(unconfirmed['explained']), unconfirmed['open_items']), (D('0'), empty['open_items']))

    def test_fingerprint_and_tamper_check(self):
        first = build_pack(self.rows, 'forecast', AMOUNT, PCT, self.notes())
        self.assertEqual(first['content_hash'], build_pack(self.rows, 'forecast', AMOUNT, PCT, self.notes())['content_hash'])
        for other in (build_pack(self.rows, 'forecast', AMOUNT, PCT, self.notes('60000')),
                      build_pack(self.rows, 'forecast', D('60000'), PCT, self.notes()),
                      build_pack(self.rows, 'budget', AMOUNT, PCT, {})):
            self.assertNotEqual(first['content_hash'], other['content_hash'])
        text = pack_json(lock_pack(first))
        self.assertEqual(load_pack(text)['content_hash'], first['content_hash'])
        tampered = json.loads(text)
        tampered['totals']['actual'] = '1'
        for bad in (json.dumps(tampered), '{not json', '{"schema_version": "0"}'):
            with self.subTest(bad=bad[:20]), self.assertRaises(ValueError):
                load_pack(bad)

    def test_excel_and_markdown_exports(self):
        pack = lock_pack(build_pack(self.rows, 'forecast', AMOUNT, PCT, self.notes('60000')))
        book = load_workbook(io.BytesIO(pack_excel(pack)))
        self.assertEqual(book.sheetnames, ['Summary', 'Variances', 'Explanations', 'Open items'])
        summary = {row[0]: row[1] for row in book['Summary'].iter_rows(min_row=2, values_only=True)}
        self.assertEqual(summary['Net variance'], 182500)
        self.assertEqual(summary['Pack fingerprint'], pack['content_hash'])
        self.assertEqual(book['Variances'].max_row, 17)
        explained = [r for r in book['Explanations'].iter_rows(min_row=2, values_only=True) if r[1] == 'Distribution' and r[0] == 'India']
        self.assertEqual((explained[0][10], explained[0][11]), (60000, 40000))
        opens = [r for r in book['Open items'].iter_rows(min_row=2, values_only=True) if r[0] == 'India' and r[1] == 'Distribution']
        self.assertEqual(opens[0][4], 'Explanation covers part of the variance')
        text = pack_markdown(pack)
        for expected in ('# Variance review: 2026-08 vs forecast', pack['content_hash'], '## Open items',
                         'Unexplained amount: INR 40,000.00', 'reviewer-supplied'):
            self.assertIn(expected, text)

    def test_notes_round_trip_and_stale_notes_skipped(self):
        notes = self.notes('60000')
        restored, skipped = import_notes(export_notes(notes), self.rows)
        self.assertEqual((restored, skipped), (notes, 0))
        changed = load_csv(sample_csv('account').replace('1200000', '1250000', 1))
        self.assertEqual(import_notes(export_notes(notes), changed), ({}, 1))
        data = json.loads(export_notes(notes))
        data['notes'][0]['amount'] = '999999999'
        self.assertEqual(import_notes(json.dumps(data), self.rows), ({}, 1))
        for bad in ('', '[]', '{"notes": "x", "schema_version": "1"}'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                import_notes(bad, self.rows)


class CostCentrePackTests(unittest.TestCase):
    def setUp(self):
        from finance import aggregate, apply_master, load_cc_master, sample_cc_master_csv
        data, _ = apply_master(load_csv(sample_csv()), load_cc_master(sample_cc_master_csv()))
        self.rows = aggregate(data, data.dims)
        self.pack = build_pack(self.rows, 'budget', D('25000'), D('10'), {}, (AMOUNT, PCT))

    def test_rollups_gross_net_offsets_and_spread(self):
        accounts = {e['label']: e for e in self.pack['rollups']['account']}
        dist = accounts['India | Distribution']
        self.assertEqual((dist['net'], dist['gross'], dist['hidden']), ('200000', '280000', '80000'))
        self.assertEqual(dist['flags'], ['Offsetting movements'])
        self.assertEqual((accounts['Singapore | Employee costs']['net'], accounts['Singapore | Employee costs']['gross']), ('0', '180000'))
        self.assertEqual(accounts['India | Employee costs']['flags'], ['Material in total only'])
        self.assertEqual(set(self.pack['rollups']), {'account', 'cost_center', 'function'})
        self.assertEqual(self.pack['grain'], ['cost_center'])
        self.assertEqual(self.pack['totals']['variance'], '365000')
        line = next(l for l in self.pack['lines'] if l['dims'] == {'cost_center': 'IN-OPS'} and l['account'] == 'Distribution')
        self.assertEqual((line['label'], line['attrs']['owner']), ('India | IN-OPS India Operations | Distribution', 'Arjun Mehta'))

    def test_excel_has_dimension_columns_and_rollup_sheets(self):
        book = load_workbook(io.BytesIO(pack_excel(lock_pack(self.pack))))
        self.assertEqual(book.sheetnames, ['Summary', 'Variances', 'Explanations', 'Open items',
                                           'By account', 'By centre', 'By function'])
        header = [c.value for c in book['Variances'][1]]
        self.assertEqual(header[:4], ['Country', 'Cost / profit centre', 'Account', 'Owner'])
        self.assertEqual(book['Variances'].max_row, 25)
        flags = {r[0]: r[9] for r in book['By account'].iter_rows(min_row=2, values_only=True)}
        self.assertEqual(flags['India | Distribution'], 'Offsetting movements')

    def test_cost_centre_notes_round_trip(self):
        row = next(r for r in analyze(self.rows, 'budget') if r['cost_center'] == 'IN-OPS' and r['account'] == 'Distribution')
        note = make_note(row, 'budget', 'Air freight.', 'Ref', '180000', 'Confirmed by reviewer', 'One-off')
        self.assertIn('cost_center=IN-OPS', note['key'])
        restored, skipped = import_notes(export_notes({note['key']: note}), self.rows)
        self.assertEqual((list(restored), skipped), ([note['key']], 0))
        from finance import aggregate
        self.assertEqual(import_notes(export_notes({note['key']: note}), aggregate(self.rows, ())), ({}, 1))


class PackAppTests(unittest.TestCase):
    def widget(self, group, label):
        return next(x for x in group if x.label == label)

    def test_lock_then_change_requires_relock(self):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertIn('Not locked yet', ''.join(x.value for x in app.markdown))
        self.widget(app.button, 'Lock review pack').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('Pack locked' in x.value and '16 material movements' in x.value for x in app.success))
        self.widget(app.number_input, 'Minimum variance (INR)').set_value(60000).run()
        self.assertTrue(any('changed since the pack was locked' in x.value for x in app.warning))
        self.widget(app.button, 'Lock review pack').click().run()
        self.assertFalse(any('changed since' in x.value for x in app.warning))

    def test_restore_explanations_from_file(self):
        rows = load_csv(sample_csv('account'))
        row = india_distribution(rows)
        note = make_note(row, 'forecast', 'Extra freight for an urgent shipment.', 'Fictional freight note',
                         '60000', 'Confirmed by reviewer', 'One-off')
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        app.sidebar.file_uploader[0].upload('account.csv', sample_csv('account').encode(), 'text/csv').run()
        self.widget(app.selectbox, 'Country').select('India').run()
        self.widget(app.selectbox, 'Compare actuals against').select('forecast').run()
        app.sidebar.file_uploader[5].upload('explanations.json', export_notes({note_key(row, 'forecast'): note}).encode(),
                                    'application/json').run()
        self.assertFalse(app.exception)
        self.assertTrue(any('Restored 1 explanation' in x.value for x in app.success))
        self.assertIn('Unexplained amount: INR 40,000.00', '\n'.join(x.value for x in app.text))
        self.assertEqual(self.widget(app.text_input, 'Amount explained (INR)').value, '60000')


if __name__ == '__main__':
    unittest.main()
