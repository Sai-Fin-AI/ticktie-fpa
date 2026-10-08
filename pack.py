"""Locked review pack: one fingerprinted record of figures and reviewer notes.

Every downstream output (exports now, AI commentary and slides later) reads the pack,
never the raw upload, so all outputs describe exactly the same reviewed numbers.
"""
import hashlib
import io
import json
from datetime import datetime, timezone
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font

from finance import analyze, compare
from evidence import accepted_amount, evidence_commentary, make_note, note_key

SCHEMA_VERSION = '1'
NOTE_FIELDS = ('id', 'reason', 'reference', 'amount', 'status', 'timing', 'action')


def _plain(value):
    """Decimals become strings so JSON keeps exact amounts."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _fingerprint(content):
    text = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(text.encode()).hexdigest()


def build_pack(rows, baseline, amount_threshold, pct_threshold, notes):
    """Consolidated pack for every validated row of the month, regardless of on-screen filters."""
    actual = sum(r['actual'] for r in rows)
    plan = sum(r[baseline] for r in rows)
    total = compare(actual, plan, amount_threshold, pct_threshold)
    lines, explained, unexplained, open_items = [], Decimal('0'), Decimal('0'), 0
    for r in analyze(rows, baseline, amount_threshold, pct_threshold):
        note = notes.get(note_key(r, baseline))
        line = {'country': r['country'], 'account': r['account'], 'actual': r['actual'],
                'baseline': r[baseline], 'variance': r['variance'], 'pct': r['pct'],
                'direction': r['direction'], 'material': r['material'],
                'note': {k: note[k] for k in NOTE_FIELDS} if note else None,
                'explained': None, 'unexplained': None, 'commentary': None}
        if r['material']:
            line['explained'] = accepted_amount(note)
            line['unexplained'] = abs(r['variance']) - line['explained']
            line['commentary'] = evidence_commentary(r, baseline, note)
            explained += line['explained']
            unexplained += line['unexplained']
            open_items += line['unexplained'] > 0 or note is None or note['status'] != 'Confirmed by reviewer'
        lines.append(line)
    content = _plain({
        'schema_version': SCHEMA_VERSION, 'period': rows[0]['month'], 'currency': rows[0]['currency'],
        'basis': baseline, 'scope': 'All countries',
        'thresholds': {'amount': amount_threshold, 'pct': pct_threshold},
        'totals': {'actual': actual, 'baseline': plan, 'variance': total['variance'],
                   'pct': total['pct'], 'direction': total['direction']},
        'summary': {'rows': len(lines), 'material': sum(l['material'] for l in lines),
                    'explained': explained, 'unexplained': unexplained, 'open_items': open_items},
        'lines': lines})
    return {**content, 'content_hash': _fingerprint(content)}


def lock_pack(pack):
    return {**pack, 'locked_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}


def pack_json(pack):
    return json.dumps(pack, indent=2, ensure_ascii=False)


def load_pack(text):
    """Read a pack file and refuse it if any figure or note was edited after locking."""
    try:
        pack = json.loads(text)
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError('The pack file is not valid JSON.') from None
    if not isinstance(pack, dict) or pack.get('schema_version') != SCHEMA_VERSION or 'content_hash' not in pack:
        raise ValueError('This is not a version 1 review pack.')
    content = {k: v for k, v in pack.items() if k not in ('content_hash', 'locked_at')}
    if _fingerprint(content) != pack['content_hash']:
        raise ValueError('The pack was changed after locking; its fingerprint does not match.')
    return pack


def _money(text):
    return Decimal(text) if text is not None else None


def pack_excel(pack):
    book = Workbook()
    bold, money, pct = Font(bold=True), '#,##0.00', '0.0%'

    def sheet(title, header, rows, money_cols=(), pct_cols=()):
        ws = book.create_sheet(title)
        ws.append(header)
        for cell in ws[1]:
            cell.font = bold
        for row in rows:
            ws.append(row)
        for col in money_cols:
            for cell in ws[col][1:]:
                cell.number_format = money
        for col in pct_cols:
            for cell in ws[col][1:]:
                cell.number_format = pct
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = max(12, min(60, max(len(str(c.value or '')) for c in column) + 2))
        ws.freeze_panes = 'A2'

    book.remove(book.active)
    t, s = pack['totals'], pack['summary']
    sheet('Summary', ['Item', 'Value'], [
        ('Period', pack['period']), ('Comparison basis', pack['basis'].title()), ('Scope', pack['scope']),
        ('Currency', pack['currency']), ('Actual', _money(t['actual'])), (pack['basis'].title(), _money(t['baseline'])),
        ('Net variance', _money(t['variance'])), ('Net outcome', t['direction']),
        ('Materiality: amount', _money(pack['thresholds']['amount'])), ('Materiality: %', _money(pack['thresholds']['pct'])),
        ('Material movements', s['material']), ('Explained (confirmed)', _money(s['explained'])),
        ('Unexplained', _money(s['unexplained'])), ('Open items', s['open_items']),
        ('Locked at (UTC)', pack.get('locked_at', 'Not locked')), ('Pack fingerprint', pack['content_hash'])])
    for cell in book['Summary']['B'][1:]:
        if isinstance(cell.value, Decimal):
            cell.number_format = money
    sheet('Variances', ['Country', 'Account', 'Actual', pack['basis'].title(), 'Variance', 'Variance %', 'Outcome', 'Material'],
          [(l['country'], l['account'], _money(l['actual']), _money(l['baseline']), _money(l['variance']),
            _money(l['pct']) / 100 if l['pct'] is not None else 'N/A', l['direction'], 'Yes' if l['material'] else 'No')
           for l in pack['lines']], money_cols='CDE', pct_cols='F')
    material = [l for l in pack['lines'] if l['material']]
    sheet('Explanations', ['Country', 'Account', 'Variance', 'Note ID', 'Explanation', 'Reference', 'Amount stated',
                           'Status', 'Timing', 'Action', 'Explained (confirmed)', 'Unexplained'],
          [(l['country'], l['account'], _money(l['variance'])) + (
              (l['note']['id'], l['note']['reason'], l['note']['reference'], _money(l['note']['amount']),
               l['note']['status'], l['note']['timing'], l['note']['action']) if l['note'] else
              ('', 'No explanation supplied', '', None, '', '', '')) + (_money(l['explained']), _money(l['unexplained']))
           for l in material], money_cols='CGKL')
    sheet('Open items', ['Country', 'Account', 'Variance', 'Unexplained', 'Reason open'],
          [(l['country'], l['account'], _money(l['variance']), _money(l['unexplained']), _open_reason(l))
           for l in material if _open_reason(l)], money_cols='CD')
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def _open_reason(line):
    note = line['note']
    if note is None:
        return 'No explanation supplied'
    if note['status'] != 'Confirmed by reviewer':
        return f"Explanation {note['status'].lower()}"
    if Decimal(line['unexplained']) > 0:
        return 'Explanation covers part of the variance'
    return ''


def pack_markdown(pack):
    t, s, basis = pack['totals'], pack['summary'], pack['basis']
    out = [f"# Variance review: {pack['period']} vs {basis}", '',
           f"Scope: {pack['scope']} · Currency: {pack['currency']} · Locked: {pack.get('locked_at', 'not locked')} UTC", '',
           '| Measure | INR |', '| --- | ---: |',
           f"| Actual | {Decimal(t['actual']):,.2f} |", f"| {basis.title()} | {Decimal(t['baseline']):,.2f} |",
           f"| Net variance ({t['direction'].lower()}) | {Decimal(t['variance']):+,.2f} |",
           f"| Explained (confirmed) | {Decimal(s['explained']):,.2f} |", f"| Unexplained | {Decimal(s['unexplained']):,.2f} |",
           '', f"{s['material']} material movements; {s['open_items']} open items.", '', '## Material movements', '']
    material = [l for l in pack['lines'] if l['material']]
    out += [l['commentary'].replace('\n', '  \n') + '\n' for l in material] or ['No account movements meet the thresholds.\n']
    opens = [l for l in material if _open_reason(l)]
    if opens:
        out += ['## Open items', ''] + [f"- {l['country']} | {l['account']}: INR {Decimal(l['unexplained']):,.2f} unexplained. {_open_reason(l)}."
                                        for l in opens] + ['']
    out += ['---', f"Pack fingerprint: `{pack['content_hash']}`. Figures are engine-calculated; explanations are reviewer-supplied."]
    return '\n'.join(out) + '\n'


def export_notes(notes):
    return json.dumps({'schema_version': SCHEMA_VERSION, 'notes': [
        {'key': list(n['key']), **_plain({k: n[k] for k in NOTE_FIELDS if k != 'id'})} for n in notes.values()]},
        indent=2, ensure_ascii=False)


def import_notes(text, rows):
    """Re-validate saved notes against the current data. Notes for changed figures are skipped, not adapted."""
    try:
        data = json.loads(text)
        entries = data['notes']
        if data.get('schema_version') != SCHEMA_VERSION or not isinstance(entries, list):
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ValueError('The explanations file is not a valid version 1 export.') from None
    by_basis = {basis: {note_key(r, basis): r for r in analyze(rows, basis)} for basis in ('budget', 'forecast')}
    restored, skipped = {}, 0
    for entry in entries:
        try:
            key = tuple(entry['key'])
            row = by_basis[key[-1]][key]
            note = make_note(row, key[-1], entry['reason'], entry['reference'], entry['amount'],
                             entry['status'], entry['timing'], entry.get('action', ''))
        except (KeyError, TypeError, IndexError, AttributeError, ValueError):
            skipped += 1
            continue
        restored[key] = note
    return restored, skipped
