"""Tick-and-tie for AI text: every figure must exist in the locked pack. No AI is used here."""
import re
from decimal import Decimal, InvalidOperation

UNITS = {'lakh': Decimal('1e5'), 'lakhs': Decimal('1e5'), 'lac': Decimal('1e5'), 'crore': Decimal('1e7'),
         'crores': Decimal('1e7'), 'cr': Decimal('1e7'), 'million': Decimal('1e6'), 'mn': Decimal('1e6'),
         'k': Decimal('1e3'), 'thousand': Decimal('1e3')}
# Digits inside codes such as CC-1001 or FY27 are not figures, hence the look-behind.
NUMBER = re.compile(r'(?<![\w.\-/])(\d[\d,]*(?:\.\d+)?)\s*(%|percent\b|lakhs?\b|lac\b|crores?\b|cr\b|million\b|mn\b|k\b|thousand\b)?', re.I)


def _dec(value):
    return Decimal(str(value).replace(',', ''))


def allowed_figures(pack):
    """Amounts and percentages the AI may quote, plus numbers the reviewer typed into notes."""
    amounts, pcts = set(), set()
    t, s = pack['totals'], pack['summary']
    for value in (t['actual'], t['baseline'], t['variance'], s['explained'], s['unexplained'],
                  pack['thresholds']['amount']):
        amounts.add(abs(_dec(value)))
    th = pack['thresholds']
    pcts.update(_dec(th[k]) for k in ('pct', 'total_pct') if th.get(k) is not None)
    amounts.update(_dec(th[k]) for k in ('total_amount',) if th.get(k) is not None)
    if t['pct'] is not None:
        pcts.add(abs(_dec(t['pct'])))
    gross = sum(abs(_dec(l['variance'])) for l in pack['lines'] if l['material'])
    if gross:
        pcts.add(_dec(s['explained']) / gross * 100)  # share of material movement explained
    counts = {s['rows'], s['material'], s['open_items'], len({l['country'] for l in pack['lines']})}
    for entries in pack.get('rollups', {}).values():
        for e in entries:
            amounts.update(abs(_dec(e[k])) for k in ('actual', 'baseline', 'net', 'gross', 'hidden', 'explained', 'unexplained'))
            if e['pct'] is not None:
                pcts.add(abs(_dec(e['pct'])))
            counts.update((e['lines'], e['to_explain']))
    for line in pack['lines']:
        for key in ('actual', 'baseline', 'variance', 'explained', 'unexplained'):
            if line[key] is not None:
                amounts.add(abs(_dec(line[key])))
        if line['pct'] is not None:
            pcts.add(abs(_dec(line['pct'])))
        if line['note']:
            amounts.add(_dec(line['note']['amount']))
            for field in ('reason', 'reference', 'action'):
                for match in NUMBER.finditer(line['note'][field]):
                    amounts.add(_dec(match.group(1)))
    year, month = pack['period'].split('-')
    plain = {Decimal(n) for n in counts} | {Decimal(year), Decimal(month)} | {Decimal(n) for n in range(11)}
    return {'amounts': amounts, 'pcts': pcts, 'plain': plain}


def _matches(value, decimals, targets, scale=Decimal(1)):
    tolerance = scale * Decimal(10) ** -decimals / 2 if scale != 1 else Decimal(0)
    return any(abs(value * scale - target) <= tolerance for target in targets)


def unsupported_figures(text, allowed):
    """Return each figure in `text` that cannot be traced to the pack."""
    problems = []
    for match in NUMBER.finditer(text):
        raw, unit = match.group(1), (match.group(2) or '').lower()
        try:
            value = _dec(raw)
        except InvalidOperation:
            problems.append(match.group(0).strip())
            continue
        decimals = len(raw.split('.')[1]) if '.' in raw else 0
        if unit in ('%', 'percent'):
            ok = any(abs(value - p) <= Decimal(10) ** -decimals / 2 for p in allowed['pcts'])
        elif unit:
            ok = _matches(value, decimals, allowed['amounts'], UNITS[unit])
        else:
            ok = value in allowed['amounts'] or value in allowed['plain']
        if not ok:
            problems.append(match.group(0).strip())
    return problems


def check_line_text(text, line):
    """Direction and review-status wording for one line."""
    issues, lowered = [], text.lower()
    says_favorable = re.search(r'(?<!un)favou?rable', lowered)
    says_unfavorable = re.search(r'unfavou?rable|adverse', lowered)
    if line['direction'] == 'Unfavorable' and says_favorable and not says_unfavorable:
        issues.append('calls an unfavorable variance favorable')
    if line['direction'] == 'Favorable' and says_unfavorable and not says_favorable:
        issues.append('calls a favorable variance unfavorable')
    status = line['note']['status'] if line['note'] else None
    # "unconfirmed", "not yet confirmed", "no explanation confirmed", "awaiting confirmation"...
    if status == 'Unconfirmed' and not re.search(
            r'unconfirmed|\b(?:no|not|without|awaiting|pending)\b(?:\W+\w+){0,3}?\W+confirm', lowered):
        issues.append('does not say the explanation is unconfirmed')
    if status == 'Conflicting explanations' and 'conflict' not in lowered:
        issues.append('does not say the explanations conflict')
    unexplained = Decimal(line['unexplained']) if line['unexplained'] is not None else Decimal(0)
    if line['note'] and unexplained > 0 and not _mentions_amount(text, unexplained):
        issues.append(f'hides the unexplained INR {unexplained:,.2f}')
    return issues


def _mentions_amount(text, amount):
    for match in NUMBER.finditer(text):
        unit = (match.group(2) or '').lower()
        if unit in ('%', 'percent'):
            continue
        raw = match.group(1)
        decimals = len(raw.split('.')[1]) if '.' in raw else 0
        if _matches(_dec(raw), decimals, {amount}, UNITS.get(unit, Decimal(1))):
            return True
    return False


def figure_records(pack, extra=()):
    """(label, amounts, pcts) for every line and roll-up, plus any extra records a caller derives from the pack."""
    records = []
    for line in pack['lines']:
        label = ' | '.join([line.get('label', f"{line['country']} | {line['account']}"),
                            *(v for k, v in line.get('attrs', {}).items() if k in ('owner', 'function'))])
        amounts = {abs(_dec(line[k])) for k in ('actual', 'baseline', 'variance', 'explained', 'unexplained') if line[k] is not None}
        if line['note']:
            amounts.add(_dec(line['note']['amount']))
            label += ' | ' + line['note']['timing']
        records.append((label, amounts, {abs(_dec(line['pct']))} if line['pct'] is not None else set()))
    for entries in pack.get('rollups', {}).values():
        for e in entries:
            records.append((e['label'], {abs(_dec(e[k])) for k in ('actual', 'baseline', 'net', 'gross', 'hidden', 'explained', 'unexplained')},
                            {abs(_dec(e['pct']))} if e['pct'] is not None else set()))
    return records + [(label, {abs(_dec(a)) for a in amounts}, set()) for label, amounts in extra]


def entity_names(pack):
    """{name: kind}. Structure names (country, cost centre, account...) identify a line together;
    owners and timing types stand on their own."""
    names = {t: 'timing' for t in ('One-off', 'Recurring', 'Timing difference')}
    for line in pack['lines']:
        for name in (line['country'], line['account'], *line.get('dims', {}).values(),
                     *(v for k, v in line.get('attrs', {}).items() if k in ('cc_name', 'function'))):
            if name:
                names.setdefault(name, 'structure')
        if line.get('attrs', {}).get('owner'):
            names.setdefault(line['attrs']['owner'], 'owner')
    return names


def misattributed_figures(text, pack, extra=()):
    """Amounts in a sentence that names an entity must belong to that entity (or be a grand total).

    Catches the right number attached to the wrong country, account, cost centre or owner.
    """
    kinds = entity_names(pack)
    mentioned = [n for n in kinds if re.search(rf'(?<!\w){re.escape(n)}(?!\w)', text, re.I)]
    if not mentioned:
        return []
    # A cost centre name such as "India Operations" also mentions "India"; keep only the most specific names.
    mentioned = [n for n in mentioned if not any(n != m and n.lower() in m.lower() for m in mentioned)]
    structure = [n for n in mentioned if kinds[n] == 'structure']
    standalone = [n for n in mentioned if kinds[n] != 'structure']
    t, s, th = pack['totals'], pack['summary'], pack['thresholds']
    global_amounts = {abs(_dec(v)) for v in (t['actual'], t['baseline'], t['variance'], s['explained'], s['unexplained'],
                                            th['amount'], th.get('total_amount')) if v is not None}
    allowed = allowed_figures(pack)
    gross = sum(abs(_dec(l['variance'])) for l in pack['lines'] if l['material'])
    global_pcts = {_dec(th[k]) for k in ('pct', 'total_pct') if th.get(k) is not None}
    global_pcts |= {abs(_dec(t['pct']))} if t['pct'] is not None else set()
    global_pcts |= {_dec(s['explained']) / gross * 100} if gross else set()
    every = figure_records(pack, extra)
    records = [r for r in every if structure and all(m.lower() in r[0].lower() for m in structure)]
    records += [r for r in every if any(m.lower() in r[0].lower() for m in standalone)]
    problems = []
    for match in NUMBER.finditer(text):
        raw, unit = match.group(1), (match.group(2) or '').lower()
        value, decimals = _dec(raw), (len(raw.split('.')[1]) if '.' in raw else 0)
        if unit in ('%', 'percent'):
            near = lambda targets: any(abs(value - p) <= Decimal(10) ** -decimals / 2 for p in targets)
            if not near(global_pcts) and not any(near(r[2]) for r in records):
                problems.append(match.group(0).strip())
            continue
        if not unit and value in allowed['plain']:
            continue
        scale = UNITS.get(unit, Decimal(1))
        if not _matches(value, decimals, global_amounts, scale) and not any(_matches(value, decimals, r[1], scale) for r in records):
            problems.append(match.group(0).strip())
    return [f'"{p}" does not belong to {" / ".join(mentioned)}' for p in problems]


def internal_codes(text):
    """Line IDs such as L03 are for the machine, not the CFO."""
    return re.findall(r'\bL\d{2}\b', text)
