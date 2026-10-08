"""Commentary writer: drafts CFO commentary from a locked pack.

The AI writes wording for explained lines only. Unexplained lines, open items and actions are
assembled by code from the pack, and every AI sentence is tick-and-tied before it can be downloaded.
"""
import json
from decimal import Decimal

from agents import cost_centre, grouped_lines, inr, line_facts, material_lines, offset_sentences, owner
from checker import allowed_figures, check_line_text, internal_codes, unsupported_figures
from llm import AIError
from pack import load_pack, pack_json, open_reason
from report_html import commentary_html

DOTS = {'Unfavorable': '🔴 ▲', 'Favorable': '🟢 ▼', 'On plan': '⚪ ●'}
STATUS_TAGS = {'Confirmed by reviewer': '✅ Confirmed', 'Unconfirmed': '🟡 Unconfirmed', 'Conflicting explanations': '🔴 Conflicting'}

SYSTEM = """You are an FP&A analyst drafting month-end expense variance commentary for a CFO review.
Rules, in order of importance:
1. Use ONLY the facts provided. Never add causes, drivers, events or details that are not in the explanation text.
2. Copy amounts and percentages exactly as written in the facts (for example "INR 200,000.00", "20.0%"). Do not calculate new numbers.
3. If review_status is "Unconfirmed", say the explanation is unconfirmed. If it is "Conflicting explanations", say the explanations conflict.
4. Every line has a "coverage" fact. Include it in the commentary, keeping its amounts exactly.
   Never attribute the whole movement to an explanation that covers only part of it.
5. Use the outcome word given (favorable or unfavorable). Expense above plan is unfavorable.
6. Plain, neutral CFO language. One to two sentences per line. No marketing words.
7. Each line already has a heading with its country, cost centre (if any) and account: do not repeat them at the start of the commentary.
8. line_id values (L01, L02...) are internal. Never write them in the text; name the country, cost centre or account instead.
9. A line marked why_in_scope is small on its own; say it is part of a wider account movement.
Executive summary: 2 to 4 short bullets covering the net result, the largest explained drivers and what remains open.
If "offsets" are given, one bullet must mention the largest one, copying its amounts."""

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['executive_summary', 'lines'], 'properties': {
    'executive_summary': {'type': 'array', 'minItems': 2, 'maxItems': 4, 'items': {'type': 'string'}},
    'lines': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                         'required': ['line_id', 'commentary'], 'properties': {
            'line_id': {'type': 'string'}, 'commentary': {'type': 'string'}}}}}}


def _facts(pack):
    t, s, basis = pack['totals'], pack['summary'], pack['basis']
    return {'period': pack['period'], 'comparison': basis, 'scope': pack['scope'],
            'total_actual': inr(t['actual']), f'total_{basis}': inr(t['baseline']),
            'net_movement': f"{inr(t['variance'])} {'above' if Decimal(t['variance']) > 0 else 'below'} {basis}",
            'net_outcome': t['direction'], 'material_movements': s['material'],
            'explained_confirmed': inr(s['explained']), 'unexplained': inr(s['unexplained']), 'open_items': s['open_items'],
            'analysed_by': ' x '.join(['country'] + [d.replace('_', ' ') for d in pack.get('grain', [])] + ['account']),
            'offsets': offset_sentences(pack),
            'explained_lines': [line_facts(i, l, basis) for i, l in material_lines(pack) if l['note']]}


def check_draft(pack, reply):
    """Return a list of plain-language problems. Empty means the draft ties out."""
    allowed, issues = allowed_figures(pack), []
    lines = dict(material_lines(pack))
    expected = {i for i, l in lines.items() if l['note']}
    seen = [entry['line_id'] for entry in reply['lines']]
    for line_id in sorted(expected - set(seen)):
        issues.append(f'{line_id}: missing commentary')
    for line_id in sorted(set(seen) - expected):
        issues.append(f'{line_id}: commentary for a line that has no explanation')
    for line_id in {i for i in seen if seen.count(i) > 1}:
        issues.append(f'{line_id}: written more than once')
    for i, bullet in enumerate(reply['executive_summary'], 1):
        for figure in unsupported_figures(bullet, allowed):
            issues.append(f'Summary bullet {i}: "{figure}" is not in the pack')
        for code in internal_codes(bullet):
            issues.append(f'Summary bullet {i}: uses the internal code {code}; name the country and account instead')
    for entry in reply['lines']:
        line = lines.get(entry['line_id'])
        if line is None or not line['note']:
            continue
        for figure in unsupported_figures(entry['commentary'], allowed):
            issues.append(f"{entry['line_id']}: \"{figure}\" is not in the pack")
        for code in internal_codes(entry['commentary']):
            issues.append(f"{entry['line_id']}: uses the internal code {code}")
        issues += [f"{entry['line_id']}: {problem}" for problem in check_line_text(entry['commentary'], line)]
    return issues


def write_commentary(pack, client):
    """Draft, check, retry once with the problems listed, check again. Raises AIError if the AI cannot run."""
    pack = load_pack(pack_json(pack))  # refuse a pack edited after locking
    facts = json.dumps(_facts(pack), ensure_ascii=False, indent=1)
    reply = client.complete('commentary', pack['content_hash'], SYSTEM, facts, SCHEMA)
    issues, attempts = check_draft(pack, reply), 1
    if issues:
        retry = (facts + '\n\nYour previous draft failed these checks. Fix them and keep to the facts:\n- ' + '\n- '.join(issues))
        reply = client.complete('commentary-retry', pack['content_hash'], SYSTEM, retry, SCHEMA)
        issues, attempts = check_draft(pack, reply), 2
    return {'reply': reply, 'issues': issues, 'attempts': attempts, 'model': client.label,
            'markdown': to_markdown(pack, reply, issues, client.label),
            'html': commentary_html(pack, reply, issues, client.label)}


def to_markdown(pack, reply, issues, model):
    basis, lines = pack['basis'], material_lines(pack)
    written = {e['line_id']: e['commentary'] for e in reply['lines']}
    out = [f"# CFO commentary: {pack['period']} expenses vs {basis}", '']
    if issues:
        out += ['> **FAILED NUMBER CHECK. Do not use.** ' + '; '.join(issues), '']
    out += ['## Executive summary', ''] + [f'- {b}' for b in reply['executive_summary']] + ['', '## Material movements', '']
    for group, members in grouped_lines(pack):
        if group:
            flags = ''.join(f" · {'⇅' if f.startswith('Offset') else 'Σ'} {f}" for f in group['flags'])
            out += [f"### {group['label']}: net {inr(group['net'])} {group['direction'].lower()} "
                    f"(gross {inr(group['gross'])}){flags}", '']
        for line_id, line in members:
            status = STATUS_TAGS[line['note']['status']] if line['note'] else '⚪ Pending'
            name = f"{cost_centre(line)} · {owner(line)}" if group and cost_centre(line) else line.get('label', f"{line['country']} | {line['account']}")
            head = f"{DOTS[line['direction']]} **{name}** ({inr(line['variance'])} {line['direction'].lower()}) · {status}"
            body = written.get(line_id) if line['note'] else 'Explanation pending from the owner. No cause is assumed.'
            out.append(f'{head}: {body or "Commentary missing."}\n')
    opens = [(l, open_reason(l)) for _, l in lines if open_reason(l)]
    if opens:
        out += ['## Open items', ''] + [f"- 🟠 {l.get('label', l['country'] + ' | ' + l['account'])}: {inr(l['unexplained'])} "
                                        f"unexplained. {why}." for l, why in opens] + ['']
    actions = [(l, l['note']['action']) for _, l in lines if l['note'] and l['note']['action']]
    if actions:
        out += ['## Actions proposed by reviewers', ''] + [f"- [ ] {l.get('label', l['country'] + ' | ' + l['account'])}: {a}"
                                                           for l, a in actions] + ['']
    status = 'passed' if not issues else 'FAILED'
    out += ['---', f"Drafted by AI ({model}) from locked pack `{pack['content_hash'][:12]}`. "
                   f"Figures tick-and-tied to the pack: {status}. Explanations are reviewer-supplied. Review before use."]
    return '\n'.join(out) + '\n'
