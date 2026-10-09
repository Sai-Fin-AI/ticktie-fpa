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

from finance import CC, CC_ATTRS, analyze, compare, dim_label, line_label, review_lines
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


def _q(value):
    # Fixed precision so 5, 5.0 and 5.00 give the same fingerprint.
    return value.quantize(Decimal('.01')) if value is not None else None


MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December')


def fy_outlook(lines, period, fy_start):
    """Full-year effect of confirmed explanations, on a stated run-rate assumption.

    Recurring items continue at this month's amount for the rest of the financial year; timing differences
    reverse within the year; one-offs (and confirmed items of unknown timing) happen once. Unexplained
    variance is shown but never projected. Amounts are signed: positive = above plan (unfavorable).
    """
    year, month = (int(p) for p in period.split('-'))
    months_left = (fy_start - 1 - month) % 12  # months after this one, up to the financial year end
    start_year = year if month >= fy_start else year - 1
    label = f'FY{start_year}' if fy_start == 1 else f'FY{start_year}-{(start_year + 1) % 100:02d}'
    by_timing = {t: Decimal('0') for t in ('One-off', 'Recurring', 'Timing difference', 'Unknown')}
    for line in lines:
        explained = Decimal(str(line['explained'] or 0))  # Decimal while building, text in a saved pack
        if line['material'] and explained:
            sign = 1 if Decimal(str(line['variance'])) > 0 else -1
            by_timing[line['note']['timing']] += sign * explained
    recurring = by_timing['Recurring']
    return {'fy': label, 'fy_start': MONTHS[fy_start - 1], 'months_left': months_left,
            'one_off': by_timing['One-off'], 'unknown_timing': by_timing['Unknown'],
            'timing': by_timing['Timing difference'], 'recurring_month': recurring,
            'recurring_rest': recurring * months_left, 'recurring_fy': recurring * (months_left + 1),
            'fy_effect': by_timing['One-off'] + by_timing['Unknown'] + recurring * (months_left + 1)}


def build_pack(rows, baseline, amount_threshold, pct_threshold, notes, total_thresholds=None, fy_start=4):
    """Consolidated pack for every validated row of the month, regardless of on-screen filters.

    amount/pct thresholds apply to each line. total_thresholds apply to country x account totals
    when lines are finer than that (for example by cost centre). fy_start is the month (1-12)
    the financial year begins, used only for the full-year outlook.
    """
    grain = list(rows[0].get('dims', ()))
    total_thresholds = total_thresholds if grain else None
    actual = sum(r['actual'] for r in rows)
    plan = sum(r[baseline] for r in rows)
    total = compare(actual, plan, *(total_thresholds or (amount_threshold, pct_threshold)))
    lines, explained, unexplained, open_items = [], Decimal('0'), Decimal('0'), 0
    for r in review_lines(rows, baseline, (amount_threshold, pct_threshold), total_thresholds):
        note = notes.get(note_key(r, baseline))
        line = {'label': line_label(r), 'country': r['country'], 'dims': {d: r[d] for d in grain},
                'attrs': {a: r[a] for a in CC_ATTRS if r.get(a)}, 'account': r['account'], 'actual': r['actual'],
                'baseline': r[baseline], 'variance': r['variance'], 'pct': r['pct'],
                'direction': r['direction'], 'material': r['material'], 'scope': r['scope'],
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
    hidden_floor = (total_thresholds or (amount_threshold, pct_threshold))[0]
    content = _plain({
        'schema_version': SCHEMA_VERSION, 'period': rows[0]['month'], 'currency': rows[0]['currency'],
        'basis': baseline, 'scope': 'All countries', 'grain': grain,
        'thresholds': {'amount': _q(amount_threshold), 'pct': _q(pct_threshold),
                       'total_amount': _q(total_thresholds[0]) if total_thresholds else None,
                       'total_pct': _q(total_thresholds[1]) if total_thresholds else None},
        'totals': {'actual': actual, 'baseline': plan, 'variance': total['variance'],
                   'pct': total['pct'], 'direction': total['direction']},
        'summary': {'rows': len(lines), 'material': sum(l['material'] for l in lines),
                    'explained': explained, 'unexplained': unexplained, 'open_items': open_items},
        'lines': lines,
        'outlook': fy_outlook(lines, rows[0]['month'], fy_start),
        'rollups': _rollups(lines, grain, total_thresholds, hidden_floor)})
    return {**content, 'content_hash': _fingerprint(content)}


def rollup_views(grain, lines):
    """Which summary views make sense for this grain: (view name, label, key function)."""
    if not grain:
        return []
    views = [('account', 'Account', lambda l: (l['country'], l['account']))]
    if CC in grain:
        views.append(('cost_center', 'Cost centre',
                      lambda l: (l['country'], f"{l['dims'][CC]} {l['attrs'].get('cc_name', '')}".strip())))
    if any(l['attrs'].get('function') for l in lines):
        views.append(('function', 'Function', lambda l: (l['attrs'].get('function', 'Unassigned'),)))
    views += [(d, dim_label(d), lambda l, d=d: (l['dims'][d],)) for d in grain if d != CC]
    return views


def _rollups(lines, grain, total_thresholds, hidden_floor):
    """Net and gross movement per group. Gross shows what offsetting lines hide inside a net figure."""
    out = {}
    for view, _, key in rollup_views(grain, lines):
        groups = {}
        for line in lines:
            groups.setdefault(key(line), []).append(line)
        entries = []
        for parts, members in groups.items():
            actual, plan = sum(m['actual'] for m in members), sum(m['baseline'] for m in members)
            net = actual - plan
            gross = sum(abs(m['variance']) for m in members)
            ups, downs = any(m['variance'] > 0 for m in members), any(m['variance'] < 0 for m in members)
            in_scope = [m for m in members if m['material']]
            flags = []
            if ups and downs and gross - abs(net) >= hidden_floor:
                flags.append('Offsetting movements')
            if any(m['scope'] == 'Account total' for m in members) and view == 'account':
                flags.append('Material in total only')
            result = compare(actual, plan, *(total_thresholds or (hidden_floor, Decimal(0))))
            entries.append({'label': ' | '.join(p for p in parts if p), 'actual': actual, 'baseline': plan, 'net': net,
                            'pct': result['pct'], 'direction': result['direction'], 'gross': gross,
                            'hidden': gross - abs(net), 'flags': flags, 'lines': len(members), 'to_explain': len(in_scope),
                            'explained': sum((m['explained'] for m in in_scope), Decimal(0)),
                            'unexplained': sum((m['unexplained'] for m in in_scope), Decimal(0))})
        out[view] = sorted(entries, key=lambda e: (abs(e['net']), e['gross']), reverse=True)
    return out


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


def _outlook_rows(outlook):
    if not outlook:
        return []
    return [(f"Full-year view ({outlook['fy']}): months left after this one", outlook['months_left']),
            ('Full-year view: one-off (confirmed)', _money(outlook['one_off'])),
            ('Full-year view: recurring this month (confirmed)', _money(outlook['recurring_month'])),
            ('Full-year view: recurring for the remaining months', _money(outlook['recurring_rest'])),
            ('Full-year view: timing differences (expected to reverse)', _money(outlook['timing'])),
            ('Full-year view: effect of confirmed explanations', _money(outlook['fy_effect'])),
            ('Full-year view: assumption', 'Recurring items continue at this month\'s amount; timing differences reverse '
                                           'within the year; unexplained variance is not projected. + = above plan.')]


def pack_excel(pack):
    book = Workbook()
    bold, money, pct = Font(bold=True), '#,##0.00', '0.0%'

    def sheet(title, header, rows, money_cols=(), pct_cols=()):
        """money_cols / pct_cols name header columns to format."""
        ws = book.create_sheet(title[:31])  # Excel's sheet-name limit
        ws.append(header)
        for cell in ws[1]:
            cell.font = bold
        for row in rows:
            ws.append(row)
        for name, fmt in [(c, money) for c in money_cols] + [(c, pct) for c in pct_cols]:
            for cell in list(ws.columns)[header.index(name)][1:]:
                cell.number_format = fmt
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = max(12, min(60, max(len(str(c.value or '')) for c in column) + 2))
        ws.freeze_panes = 'A2'

    book.remove(book.active)
    t, s = pack['totals'], pack['summary']
    sheet('Summary', ['Item', 'Value'], [
        ('Period', pack['period']), ('Comparison basis', pack['basis'].title()), ('Scope', pack['scope']),
        ('Currency', pack['currency']), ('Actual', _money(t['actual'])), (pack['basis'].title(), _money(t['baseline'])),
        ('Net variance', _money(t['variance'])), ('Net outcome', t['direction']),
        ('Analysed by', ' × '.join(['Country'] + [dim_label(d) for d in pack['grain']] + ['Account'])),
        ('Materiality per line: amount', _money(pack['thresholds']['amount'])),
        ('Materiality per line: %', _money(pack['thresholds']['pct'])),
        ('Materiality of account totals: amount', _money(pack['thresholds']['total_amount']) if pack['thresholds']['total_amount'] else 'Same as line'),
        ('Materiality of account totals: %', _money(pack['thresholds']['total_pct']) if pack['thresholds']['total_pct'] else 'Same as line'),
        ('Material movements', s['material']), ('Explained (confirmed)', _money(s['explained'])),
        ('Unexplained', _money(s['unexplained'])), ('Open items', s['open_items']),
        *_outlook_rows(pack.get('outlook')),
        ('Locked at (UTC)', pack.get('locked_at', 'Not locked')), ('Pack fingerprint', pack['content_hash'])])
    for cell in book['Summary']['B'][1:]:
        if isinstance(cell.value, Decimal):
            cell.number_format = money
    grain, base = pack['grain'], pack['basis'].title()
    has_owner = any(l['attrs'].get('owner') for l in pack['lines'])
    ids = ['Country'] + [dim_label(d) for d in grain] + ['Account'] + (['Owner'] if has_owner else [])

    def who(l):
        return (l['country'], *(l['dims'][d] for d in grain), l['account']) + ((l['attrs'].get('owner', ''),) if has_owner else ())

    sheet('Variances', ids + ['Actual', base, 'Variance', 'Variance %', 'Outcome', 'Needs explanation'],
          [who(l) + (_money(l['actual']), _money(l['baseline']), _money(l['variance']),
                     _money(l['pct']) / 100 if l['pct'] is not None else 'N/A', l['direction'],
                     ('Yes (account total)' if l['scope'] == 'Account total' else 'Yes') if l['material'] else 'No')
           for l in pack['lines']], money_cols=('Actual', base, 'Variance'), pct_cols=('Variance %',))
    material = [l for l in pack['lines'] if l['material']]
    sheet('Explanations', ids + ['Variance', 'Note ID', 'Explanation', 'Reference', 'Amount stated',
                                 'Status', 'Timing', 'Action', 'Explained (confirmed)', 'Unexplained'],
          [who(l) + (_money(l['variance']),) + (
              (l['note']['id'], l['note']['reason'], l['note']['reference'], _money(l['note']['amount']),
               l['note']['status'], l['note']['timing'], l['note']['action']) if l['note'] else
              ('', 'No explanation supplied', '', None, '', '', '')) + (_money(l['explained']), _money(l['unexplained']))
           for l in material], money_cols=('Variance', 'Amount stated', 'Explained (confirmed)', 'Unexplained'))
    sheet('Open items', ids + ['Variance', 'Unexplained', 'Reason open'],
          [who(l) + (_money(l['variance']), _money(l['unexplained']), open_reason(l))
           for l in material if open_reason(l)], money_cols=('Variance', 'Unexplained'))
    for view, label, _ in rollup_views(grain, pack['lines']):
        sheet(f'By {label.lower()}', [label, 'Actual', base, 'Net variance', 'Gross movement', 'Hidden by offsets',
                                      'Lines to explain', 'Explained (confirmed)', 'Unexplained', 'Flags'],
              [(e['label'], _money(e['actual']), _money(e['baseline']), _money(e['net']), _money(e['gross']), _money(e['hidden']),
                e['to_explain'], _money(e['explained']), _money(e['unexplained']), ', '.join(e['flags']))
               for e in pack['rollups'][view]],
              money_cols=('Actual', base, 'Net variance', 'Gross movement', 'Hidden by offsets', 'Explained (confirmed)', 'Unexplained'))
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def open_reason(line):
    note = line['note']
    if note is None:
        return 'No explanation supplied'
    if note['status'] == 'Unconfirmed':
        return 'Explanation not yet confirmed'
    if note['status'] != 'Confirmed by reviewer':
        return 'Explanations conflict'
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
    opens = [l for l in material if open_reason(l)]
    if opens:
        out += ['## Open items', ''] + [f"- {l['label']}: INR {Decimal(l['unexplained']):,.2f} unexplained. {open_reason(l)}."
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
