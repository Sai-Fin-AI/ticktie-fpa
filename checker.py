"""Tick-and-tie for AI text: every figure must exist in the locked pack. No AI is used here."""
import re
from decimal import Decimal, InvalidOperation

UNITS = {'lakh': Decimal('1e5'), 'lakhs': Decimal('1e5'), 'lac': Decimal('1e5'), 'crore': Decimal('1e7'),
         'crores': Decimal('1e7'), 'cr': Decimal('1e7'), 'million': Decimal('1e6'), 'mn': Decimal('1e6'),
         'k': Decimal('1e3'), 'thousand': Decimal('1e3')}
NUMBER = re.compile(r'(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(%|percent\b|lakhs?\b|lac\b|crores?\b|cr\b|million\b|mn\b|k\b|thousand\b)?', re.I)


def _dec(value):
    return Decimal(str(value).replace(',', ''))


def allowed_figures(pack):
    """Amounts and percentages the AI may quote, plus numbers the reviewer typed into notes."""
    amounts, pcts = set(), set()
    t, s = pack['totals'], pack['summary']
    for value in (t['actual'], t['baseline'], t['variance'], s['explained'], s['unexplained'],
                  pack['thresholds']['amount']):
        amounts.add(abs(_dec(value)))
    pcts.add(_dec(pack['thresholds']['pct']))
    if t['pct'] is not None:
        pcts.add(abs(_dec(t['pct'])))
    counts = {s['rows'], s['material'], s['open_items'], len({l['country'] for l in pack['lines']})}
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


def internal_codes(text):
    """Line IDs such as L03 are for the machine, not the CFO."""
    return re.findall(r'\bL\d{2}\b', text)
