"""AI agents. Each one drafts words only; figures come from the review pack and are checked by checker.py."""
from decimal import Decimal


def inr(value):
    return f'INR {abs(Decimal(value)):,.2f}'


def pct_text(line):
    return f"{abs(Decimal(line['pct'])):.1f}%" if line['pct'] is not None else 'N/A (zero baseline)'


def material_lines(pack):
    """Stable IDs (L01, L02...) in the pack's order, largest movement first."""
    return [(f'L{i:02d}', line) for i, line in enumerate((l for l in pack['lines'] if l['material']), 1)]


def line_facts(line_id, line, basis):
    facts = {'line_id': line_id, 'country': line['country'], 'account': line['account'],
             'movement': f"{inr(line['variance'])} {'above' if Decimal(line['variance']) > 0 else 'below'} {basis}",
             'variance_pct': pct_text(line), 'outcome': line['direction'],
             'explained_confirmed': inr(line['explained']), 'unexplained': inr(line['unexplained'])}
    if line['note']:
        n = line['note']
        facts.update({'explanation': n['reason'], 'reference': n['reference'], 'amount_stated': inr(n['amount']),
                      'review_status': n['status'], 'timing': n['timing'], 'proposed_action': n['action'] or 'none',
                      'coverage': coverage(line)})
    return facts


def coverage(line):
    """The explained/unexplained split as a ready sentence, so the AI never has to work it out."""
    unexplained, variance = Decimal(line['unexplained']), inr(line['variance'])
    if unexplained == 0:
        return f'The explanation covers the full {variance}.'
    if line['note']['status'] != 'Confirmed by reviewer':
        return f'Not counted until resolved: {inr(unexplained)} remains unexplained.'
    return f"The explanation covers {inr(line['explained'])} of the {variance}; {inr(unexplained)} remains unexplained."
