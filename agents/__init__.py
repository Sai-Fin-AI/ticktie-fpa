"""AI agents. Each one drafts words only; figures come from the review pack and are checked by checker.py."""
from decimal import Decimal

import fmt
from checker import short_name


def inr(value):
    return f'INR {fmt.group(value, 2)}'


def inr_balance(value):
    """An actual, budget or profit: keeps the minus sign of a loss or a negative balance."""
    return f'INR {fmt.balance(value, 2)}'


def pct_text(line):
    return f"{abs(Decimal(line['pct'])):.1f}%" if line['pct'] is not None else 'N/A (zero baseline)'


def material_lines(pack):
    """Stable IDs (L01, L02...) in the pack's order, largest movement first."""
    return [(f'L{i:02d}', line) for i, line in enumerate((l for l in pack['lines'] if l['material']), 1)]


def owner(line):
    return line.get('attrs', {}).get('owner') or 'Unassigned owner'


def cost_centre(line):
    code = line.get('dims', {}).get('cost_center')
    return f"{code} {line['attrs'].get('cc_name', '')}".strip() if code else None


def account_group(pack, line):
    """The country x account roll-up a line belongs to, if lines are finer than that."""
    for entry in pack.get('rollups', {}).get('account', []):
        if entry['label'] == f"{line['country']} | {line['account']}":
            return entry
    return None


def line_facts(line_id, line, basis):
    facts = {'line_id': line_id, 'name': short_name(line), 'country': line['country'], 'account': line['account'],
             'movement': f"{inr(line['variance'])} {'above' if Decimal(line['variance']) > 0 else 'below'} {basis}",
             'variance_pct': pct_text(line), 'outcome': line['direction'],
             'explained_confirmed': inr(line['explained']), 'unexplained': inr(line['unexplained'])}
    for name, value in line.get('dims', {}).items():
        facts['cost_centre' if name == 'cost_center' else name] = cost_centre(line) if name == 'cost_center' else value
    if line.get('scope') == 'Account total':
        facts['why_in_scope'] = 'Below the line threshold, but part of an account total that is material only in aggregate.'
    if line.get('negative'):
        which = 'actual' if Decimal(line['actual']) < 0 else basis
        facts['negative_balance'] = (f"The {which} is {inr_balance(line['actual'] if which == 'actual' else line['baseline'])}: "
                                     'a negative balance, usually a reversal, reclass or misposting. Say so; do not call it a saving.')
        if line.get('scope') == 'Negative balance':
            facts['why_in_scope'] = 'Below the thresholds, but every line with a negative balance needs an explanation.'
    if line['note']:
        n = line['note']
        facts.update({'explanation': n['reason'], 'reference': n['reference'], 'amount_stated': inr(n['amount']),
                      'review_status': n['status'], 'timing': n['timing'], 'proposed_action': n['action'] or 'none',
                      'coverage': coverage(line)})
    return facts


def full_year_facts(outlook, money=None):
    """The pack's full-year view as ready phrases, so the AI never projects anything itself."""
    if not outlook:
        return 'not available'
    money = money or inr
    word = lambda v: f"{money(v)} {'above' if Decimal(v) > 0 else 'below'} plan" if Decimal(v) else 'none'
    return {'financial_year': outlook['fy'], 'months_left_after_this_one': outlook['months_left'],
            'recurring_for_full_year': word(outlook['recurring_fy']),
            'full_year_effect_of_confirmed_items': word(outlook['fy_effect']),
            'assumption': "recurring items continue at this month's amount; timing differences reverse within the year; "
                          'unexplained variance is not projected'}


def coverage(line):
    """The explained/unexplained split as a ready sentence, so the AI never has to work it out."""
    unexplained, variance = Decimal(line['unexplained']), inr(line['variance'])
    if unexplained == 0:
        return f'The explanation covers the full {variance}.'
    if line['note']['status'] != 'Confirmed by reviewer':
        return f'Not counted until resolved: {inr(unexplained)} remains unexplained.'
    return f"The explanation covers {inr(line['explained'])} of the {variance}; {inr(unexplained)} remains unexplained."


def grouped_lines(pack):
    """[(account roll-up or None, [(line_id, line), ...])] in order of each group's largest line.

    With account-level data there is one group with no roll-up, so the layout stays a flat list.
    """
    lines = material_lines(pack)
    if not pack.get('grain'):
        return [(None, lines)]
    groups = {}
    for line_id, line in lines:
        groups.setdefault((line['country'], line['account']), []).append((line_id, line))
    return [(account_group(pack, members[0][1]), members) for members in groups.values()]


def offset_sentences(pack):
    """Ready sentences for account totals whose net figure hides offsetting lines."""
    return [f"{e['label']}: net {inr(e['net'])} {e['direction'].lower()}, but gross movement is {inr(e['gross'])}; "
            f"offsetting lines hide {inr(e['hidden'])}."
            for e in pack.get('rollups', {}).get('account', []) if 'Offsetting movements' in e['flags']]
