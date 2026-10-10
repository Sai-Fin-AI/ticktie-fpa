"""Tick-and-tie for AI text: every figure must exist in the locked pack. No AI is used here."""
import re
from decimal import Decimal, InvalidOperation

UNITS = {'lakh': Decimal('1e5'), 'lakhs': Decimal('1e5'), 'lac': Decimal('1e5'), 'crore': Decimal('1e7'),
         'crores': Decimal('1e7'), 'cr': Decimal('1e7'), 'million': Decimal('1e6'), 'mn': Decimal('1e6'),
         'k': Decimal('1e3'), 'thousand': Decimal('1e3')}
# Digits inside codes such as CC-1001, FY27 or Aug-2026 are not figures, hence the look-behinds. A minus sign
# before a figure ("INR -15,000") is not part of a code, so that figure is still checked.
NUMBER = re.compile(r'(?<![\w./])(?<!\w-)(\d[\d,]*(?:\.\d+)?)\s*(%|percent\b|pts?\b|pp\b|points?\b|percentage points?\b|lakhs?\b|lac\b|crores?\b|cr\b|million\b|mn\b|k\b|thousand\b)?', re.I)
POINTS = ('pt', 'pts', 'pp', 'point', 'points', 'percentage point', 'percentage points')


def _dec(value):
    return Decimal(str(value).replace(',', ''))


def allowed_figures(pack):
    """Amounts and percentages the AI may quote, plus numbers the reviewer typed into notes."""
    amounts, pcts, points = set(), set(), set()
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
    for e in pack.get('pl') or []:  # P&L lines, subtotals and margins
        if e['kind'] == 'margin':
            pcts.update(abs(_dec(e[k])) for k in ('actual_pct', 'baseline_pct'))
            points.add(abs(_dec(e['pts'])))
        else:
            amounts.update(abs(_dec(e[k])) for k in ('actual', 'baseline', 'variance'))
            if e['pct'] is not None:
                pcts.add(abs(_dec(e['pct'])))
    outlook = pack.get('outlook')
    if outlook:
        amounts.update(abs(_dec(outlook[k])) for k in ('one_off', 'unknown_timing', 'timing', 'recurring_month',
                                                        'recurring_rest', 'recurring_fy', 'fy_effect'))
        counts.add(outlook['months_left'])
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
    return {'amounts': amounts, 'pcts': pcts, 'points': points, 'plain': plain}


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
        if unit in POINTS:
            ok = any(abs(value - p) <= Decimal(10) ** -decimals / 2 for p in allowed.get('points', ()))
        elif unit in ('%', 'percent'):
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
        if unit in ('%', 'percent') or unit in POINTS:
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
    for e in pack.get('pl') or []:
        if e['kind'] == 'margin':
            records.append((e['label'], set(), {abs(_dec(e['actual_pct'])), abs(_dec(e['baseline_pct']))}))
        else:
            records.append((e['label'], {abs(_dec(e[k])) for k in ('actual', 'baseline', 'variance')},
                            {abs(_dec(e['pct']))} if e['pct'] is not None else set()))
    return records + [(label, {abs(_dec(a)) for a in amounts}, set()) for label, amounts in extra]


def entity_names(pack):
    """{name: kind}. Country and account ('structure') and cost centre or other levels ('dim') identify a
    line together; owners and timing types stand on their own."""
    names = {t: 'timing' for t in ('One-off', 'Recurring', 'Timing difference')}
    for e in pack.get('pl') or []:  # P&L lines and subtotals (Sales, EBITDA...) name pack-wide figures
        if e['kind'] in ('line', 'subtotal'):
            names.setdefault(e['label'], 'statement')
    for line in pack['lines']:
        for name in (line['country'], line['account']):
            names.setdefault(name, 'structure')
        for name in (*line.get('dims', {}).values(),
                     *(v for k, v in line.get('attrs', {}).items() if k in ('cc_name', 'function'))):
            if name:
                names.setdefault(name, 'dim')
        if line.get('attrs', {}).get('owner'):
            names.setdefault(line['attrs']['owner'], 'owner')
    return names


def line_names(line):
    """Names that identify a line below country x account: cost centre code and name, other levels."""
    return [v for v in (*line.get('dims', {}).values(), line.get('attrs', {}).get('cc_name')) if v]


def short_name(line):
    """How commentary should name one line: "India Operations Distribution", or "India Distribution"
    when the data has no cost centres (then the line is the account total)."""
    names = line_names(line)
    return f"{line.get('attrs', {}).get('cc_name') or (names[0] if names else line['country'])} {line['account']}"


def account_total_confusion(text, line, pack):
    """With cost centres, "India Distribution" is the account total. Commentary about one cost-centre
    line must not name it by country and account alone, or the reader takes it for the total."""
    if not pack.get('grain'):
        return []
    has = lambda name: re.search(rf'(?<!\w){re.escape(name)}(?!\w)', text, re.I)
    if has(line['country']) and has(line['account']) and not any(has(n) for n in line_names(line)):
        return [f'names the account total "{line["country"]} {line["account"]}"; call this line "{short_name(line)}"']
    return []


def _name_groups(text, kinds):
    """Runs of adjacent entity names, such as "India | IN-OPS India Operations | Distribution", with positions."""
    spans = sorted((m.start(), m.end(), name) for name in kinds
                   for m in re.finditer(rf'(?<!\w){re.escape(name)}(?!\w)', text, re.I))
    groups = []  # [start, end, names]
    for start, end, name in spans:
        if groups and (start < groups[-1][1] or re.fullmatch(r'[\s|·/\-]*', text[groups[-1][1]:start])):
            groups[-1][1] = max(groups[-1][1], end)
            groups[-1][2].append(name)
        else:
            groups.append([start, end, [name]])
    for group in groups:  # "India Operations" also contains "India": keep the most specific names
        group[2] = [n for n in dict.fromkeys(group[2]) if not any(n != m and n.lower() in m.lower() for m in group[2])]
    return groups


def misattributed_figures(text, pack, extra=()):
    """An amount written next to a name must belong to that name (or be a pack-wide total).

    Each amount is bound to the nearest name before it in the sentence (or the first one after it when
    the amount comes first), the way a reader would take it. Catches the right number attached to the
    wrong country, account, cost centre or owner.
    """
    kinds = entity_names(pack)
    groups = _name_groups(text, kinds)
    if not groups:
        return []
    t, s, th = pack['totals'], pack['summary'], pack['thresholds']
    global_amounts = {abs(_dec(v)) for v in (t['actual'], t['baseline'], t['variance'], s['explained'], s['unexplained'],
                                            th['amount'], th.get('total_amount')) if v is not None}
    outlook = pack.get('outlook') or {}
    # Full-year figures are pack-wide totals (all recurring items, all confirmed items), like the net variance.
    global_amounts |= {abs(_dec(outlook[k])) for k in ('one_off', 'timing', 'recurring_month', 'recurring_rest',
                                                       'recurring_fy', 'fy_effect') if k in outlook}
    allowed = allowed_figures(pack)
    gross = sum(abs(_dec(l['variance'])) for l in pack['lines'] if l['material'])
    global_pcts = {_dec(th[k]) for k in ('pct', 'total_pct') if th.get(k) is not None}
    global_pcts |= {abs(_dec(t['pct']))} if t['pct'] is not None else set()
    global_pcts |= {_dec(s['explained']) / gross * 100} if gross else set()
    every = figure_records(pack, extra)

    def records_for(names):
        structure = [n for n in names if kinds[n] in ('structure', 'dim')]
        standalone = [n for n in names if kinds[n] not in ('structure', 'dim')]
        if pack.get('grain') and structure and not any(kinds[n] == 'dim' for n in structure):
            # "India Distribution" without a cost centre means the account total, not one of its lines.
            candidates = every[len(pack['lines']):]
        else:
            candidates = every
        found = [r for r in candidates if structure and all(m.lower() in r[0].lower() for m in structure)]
        return found + [r for r in every if any(m.lower() in r[0].lower() for m in standalone)]

    problems = []
    for match in NUMBER.finditer(text):
        before = [g for g in groups if g[1] <= match.start()]
        names = (before[-1] if before else groups[0])[2]
        raw, unit = match.group(1), (match.group(2) or '').lower()
        value, decimals = _dec(raw), (len(raw.split('.')[1]) if '.' in raw else 0)
        if unit in POINTS:  # margin changes are P&L-wide figures, checked by unsupported_figures
            continue
        if unit in ('%', 'percent'):
            near = lambda targets: any(abs(value - p) <= Decimal(10) ** -decimals / 2 for p in targets)
            if not near(global_pcts) and not any(near(r[2]) for r in records_for(names)):
                problems.append((match.group(0).strip(), names))
            continue
        if not unit and value in allowed['plain']:
            continue
        scale = UNITS.get(unit, Decimal(1))
        if not _matches(value, decimals, global_amounts, scale) and not any(
                _matches(value, decimals, r[1], scale) for r in records_for(names)):
            problems.append((match.group(0).strip(), names))
    return [f'"{p}" does not belong to {" / ".join(names)}' for p, names in problems]

def internal_codes(text):
    """Line IDs such as L03 are for the machine, not the CFO."""
    return re.findall(r'\bL\d{2}\b', text)
