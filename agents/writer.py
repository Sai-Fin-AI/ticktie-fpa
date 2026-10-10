"""Commentary writer: drafts CFO commentary from a locked pack.

The AI writes wording for explained lines only. Unexplained lines, open items and actions are
assembled by code from the pack, and every AI sentence is tick-and-tied before it can be downloaded.
"""
import json
import re
from decimal import Decimal

import fmt
from finance import MARGIN_NAMES
from agents import (cost_centre, coverage, full_year_facts, grouped_lines, inr, inr_balance, line_facts, material_lines, offset_sentences,
                    owner)
from checker import (account_total_confusion, short_name, allowed_figures, check_line_text, internal_codes, misattributed_figures,
                     unsupported_figures)
from llm import AIError
from pack import load_pack, pack_json, open_reason
from report_html import commentary_html

DOTS = {'Unfavorable': '🔴 ▲', 'Favorable': '🟢 ▼', 'On plan': '⚪ ●'}
STATUS_TAGS = {'Confirmed by reviewer': '✅ Confirmed', 'Unconfirmed': '🟡 Unconfirmed', 'Conflicting explanations': '🔴 Conflicting'}

SYSTEM = """You are an FP&A analyst drafting month-end variance commentary (expenses, or a full P&L) for a CFO review.
Rules, in order of importance:
1. Use ONLY the facts provided. Never add causes, drivers, events or details that are not in the explanation text.
2. Copy amounts and percentages exactly as written in the facts (for example "INR 200,000.00", "20.0%"). Do not calculate new numbers.
3. If review_status is "Unconfirmed", say the explanation is unconfirmed. If it is "Conflicting explanations", say the explanations conflict.
4. Every line has a "coverage" fact. Include it in the commentary, keeping its amounts exactly.
   Never attribute the whole movement to an explanation that covers only part of it.
5. Use the outcome word given (favorable or unfavorable). Costs above plan are unfavorable; sales and profit above plan are favorable.
6. Plain, neutral CFO language. One to two sentences per line. No marketing words.
7. Each line already has a heading with its country, cost centre (if any) and account: do not repeat them at the start of the commentary.
   If you do name a line, in its commentary or in a summary bullet, use its "name" fact exactly (for example
   "India Operations Distribution"). With cost centres, a country plus an account ("India Distribution") means the
   account total across all cost centres, so never use it for a single line.
8. line_id values (L01, L02...) are internal. Never write them in the text; name the country, cost centre or account instead.
9. A line marked why_in_scope is small on its own; say why it is in scope, using that fact.
10. A line with a negative_balance fact: say it is a negative balance, as that fact says. Never present it as a saving.
Executive summary: 2 to 4 short bullets covering the net result, the largest explained drivers and what remains open.
For drivers, copy phrases from "largest_explained_drivers"; never pair a name with an amount from another fact.
When a sentence quotes amounts, it names only one line or account (use separate sentences for separate lines).
If "offsets" are given, one bullet must mention the largest one, copying its sentence's amounts (net, gross, hidden).
If "full_year" is given, you may add one bullet copying its full-year effect and assumption.
If "profit_and_loss" is given, the first bullet states the net profit result (and EBITDA if given), copying its phrases."""

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['executive_summary', 'lines'], 'properties': {
    'executive_summary': {'type': 'array', 'minItems': 2, 'maxItems': 4, 'items': {'type': 'string'}},
    'lines': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                         'required': ['line_id', 'commentary'], 'properties': {
            'line_id': {'type': 'string'}, 'commentary': {'type': 'string'}}}}}}


def _facts(pack):
    t, s, basis = pack['totals'], pack['summary'], pack['basis']
    facts = {'period': pack['period'], 'comparison': basis, 'scope': pack['scope'], 'review_total': t.get('name', 'Expenses'),
            'total_actual': inr_balance(t['actual']), f'total_{basis}': inr_balance(t['baseline']),
            'net_movement': f"{inr(t['variance'])} {'above' if Decimal(t['variance']) > 0 else 'below'} {basis}",
            'net_outcome': t['direction'], 'material_movements': s['material'],
            'explained_confirmed': inr(s['explained']), 'unexplained': inr(s['unexplained']), 'open_items': s['open_items'],
            'analysed_by': ' x '.join(['country'] + [d.replace('_', ' ') for d in pack.get('grain', [])] + ['account']),
            'offsets': offset_sentences(pack),
            'largest_explained_drivers': driver_sentences(pack),
            'full_year': full_year_facts(pack.get('outlook')),
            'explained_lines': [line_facts(i, l, basis) for i, l in material_lines(pack) if l['note']]}
    if pack.get('kind') == 'pl':
        facts['profit_and_loss'] = pl_facts(pack)
    return facts


def pl_facts(pack):
    """Sales, subtotals and margins as ready phrases, so the AI never computes a margin."""
    out = {}
    for e in pack.get('pl') or []:
        if e['kind'] == 'subtotal' or (e['kind'] == 'line' and e['label'] == 'Sales'):
            v = Decimal(e['variance'])
            out[e['label']] = (f"{inr_balance(e['actual'])} against {inr_balance(e['baseline'])}: {inr(v)} {'above' if v > 0 else 'below'} plan "
                               f"({e['direction'].lower()})" if v else f"{inr_balance(e['actual'])}, on plan")
        elif e['kind'] == 'margin':
            pts = Decimal(e['pts'])
            out[MARGIN_NAMES.get(e['label'], e['label'])] = (f"{Decimal(e['actual_pct']):.1f}% against {Decimal(e['baseline_pct']):.1f}% "
                                                             f"({'+' if pts > 0 else '-' if pts < 0 else ''}{abs(pts):.1f} pts)")
    return out


def driver_sentences(pack, limit=3):
    """Ready phrases for the largest confirmed explanations, so summary bullets never pair a name with the wrong figure."""
    lines = sorted((l for _, l in material_lines(pack) if l['note'] and Decimal(l['explained'])),
                   key=lambda l: Decimal(l['explained']), reverse=True)[:limit]
    return [f"{short_name(l)}: {inr(l['explained'])} explained ({l['note']['reason'].rstrip('.')}) out of a "
            f"{inr(l['variance'])} {l['direction'].lower()} variance" for l in lines]


def _findings(pack, reply):
    """Problems per item: {('summary', 1): [...], ('line', 'L03'): [...], ('draft', None): [...]}."""
    allowed, found = allowed_figures(pack), {}
    add = lambda key, problems: found.setdefault(key, []).extend(problems) if problems else None
    lines = dict(material_lines(pack))
    expected = {i for i, l in lines.items() if l['note']}
    seen = [entry['line_id'] for entry in reply['lines']]
    for line_id in sorted(expected - set(seen)):
        add(('line', line_id), ['missing commentary'])
    for line_id in sorted(set(seen) - expected):
        add(('draft', None), [f'{line_id}: commentary for a line that has no explanation'])
    for line_id in sorted({i for i in seen if seen.count(i) > 1}):
        add(('draft', None), [f'{line_id}: written more than once'])
    for i, bullet in enumerate(reply['executive_summary'], 1):
        add(('summary', i), [f'"{figure}" is not in the pack' for figure in unsupported_figures(bullet, allowed)])
        add(('summary', i), [f'uses the internal code {code}; name the country and account instead' for code in internal_codes(bullet)])
        add(('summary', i), misattributed_figures(bullet, pack))
    for entry in reply['lines']:
        line = lines.get(entry['line_id'])
        if line is None or not line['note']:
            continue
        text, key = entry['commentary'], ('line', entry['line_id'])
        add(key, [f'"{figure}" is not in the pack' for figure in unsupported_figures(text, allowed)])
        add(key, [f'uses the internal code {code}' for code in internal_codes(text)])
        add(key, check_line_text(text, line))
        add(key, account_total_confusion(text, line, pack))
    return found


def check_draft(pack, reply):
    """Return a list of plain-language problems. Empty means the draft ties out."""
    out = []
    for (kind, item), problems in _findings(pack, reply).items():
        prefix = f'Summary bullet {item}: ' if kind == 'summary' else f'{item}: ' if kind == 'line' else ''
        out += [prefix + p for p in problems]
    return out


STATUS_LEADS = {'Confirmed by reviewer': 'Reason confirmed by the reviewer',
                'Unconfirmed': "The owner's explanation is unconfirmed",
                'Conflicting explanations': 'The explanations conflict'}


def standard_line(pack, line):
    """Engine-written commentary for one explained line: the reviewer's words plus the pack's figures."""
    variance = Decimal(line['variance'])
    noun = 'Revenue' if line.get('pl_line') == 'Sales' else 'Expense'
    move = f"{noun} is {inr(variance)} {'above' if variance > 0 else 'below'} {pack['basis']} ({line['direction'].lower()})."
    if line.get('negative'):
        which = 'actual' if Decimal(line['actual']) < 0 else pack['basis']
        value = line['actual'] if which == 'actual' else line['baseline']
        move += f' The {which} is a negative balance of {inr_balance(value)}, usually a reversal, reclass or misposting.'
    lead = STATUS_LEADS[line['note']['status']]
    reason = ' '.join(line['note']['reason'].split()).rstrip('.')
    allowed = allowed_figures(pack)
    for text in (f'{move} {lead}: {reason}. {coverage(line)}', f'{move} {lead}. {coverage(line)}'):
        if not (unsupported_figures(text, allowed) or check_line_text(text, line) or account_total_confusion(text, line, pack)):
            return text
    return f'{move} {lead}. {coverage(line)}'


def standard_summary(pack):
    t, s, basis = pack['totals'], pack['summary'], pack['basis']
    word = 'above' if Decimal(t['variance']) > 0 else 'below'
    name = t.get('name', 'Expenses')
    bullets = [f"{name} {'were' if name == 'Expenses' else 'was'} {inr(t['variance'])} {word} {basis} ({t['direction'].lower()}): "
               f"actual {inr_balance(t['actual'])} against {inr_balance(t['baseline'])}.",
               f"Confirmed explanations cover {inr(s['explained'])}; {inr(s['unexplained'])} remains unexplained "
               f"across {s['open_items']} open item(s)."]
    offsets = offset_sentences(pack)
    if offsets:
        bullets.append(offsets[0])
    return bullets


def _plain_problem(problem, labels):
    """Reviewer-facing wording: line names instead of internal IDs."""
    for line_id, label in labels.items():
        problem = re.sub(rf'\b{line_id}\b', label, problem)
    return problem


def clean_draft(pack, reply):
    """Keep AI sentences that pass every check; replace the rest with engine-written wording.

    Returns (clean reply, notes for the reviewer: where, action, problems, the AI's original text).
    Nothing that failed a check is ever published.
    """
    found = _findings(pack, reply)
    lines = dict(material_lines(pack))
    labels = {i: l.get('label', f"{l['country']} | {l['account']}") for i, l in lines.items()}
    expected = [i for i, l in lines.items() if l['note']]
    written, notes = {}, []
    for entry in reply['lines']:
        if entry['line_id'] in expected and entry['line_id'] not in written:
            written[entry['line_id']] = entry['commentary'].strip()
    for line_id in expected:
        problems = found.get(('line', line_id))
        if problems:
            notes.append({'where': labels[line_id], 'action': 'Replaced with standard wording',
                          'problems': [_plain_problem(p, labels) for p in problems], 'ai_text': written.get(line_id, '')})
            written[line_id] = standard_line(pack, lines[line_id])
    bullets = []
    for i, bullet in enumerate(reply['executive_summary'], 1):
        problems = found.get(('summary', i))
        if problems:
            notes.append({'where': f'Executive summary, bullet {i}', 'action': 'Removed',
                          'problems': [_plain_problem(p, labels) for p in problems], 'ai_text': bullet.strip()})
        else:
            bullets.append(bullet.strip())
    if len(bullets) < 2:
        bullets = standard_summary(pack)[:max(2, 3 - len(bullets))] + bullets
    extra = found.get(('draft', None), [])
    if extra:
        notes.append({'where': 'Draft', 'action': 'Ignored', 'problems': [_plain_problem(p, labels) for p in extra], 'ai_text': ''})
    return {'executive_summary': bullets, 'lines': [{'line_id': i, 'commentary': written[i]} for i in expected]}, notes


def write_commentary(pack, client, style=None):
    """Draft, check, retry once with the problems listed, check again, then publish only what passes.

    AI sentences that still fail are replaced with engine-written wording. If the AI cannot run at all,
    the whole commentary uses engine-written wording, so there is always a clean, downloadable draft.
    """
    fmt.use(style)
    pack = load_pack(pack_json(pack))  # refuse a pack edited after locking
    facts = json.dumps(_facts(pack), ensure_ascii=False, indent=1)
    ai_error, attempts = None, 0
    try:
        reply = client.complete('commentary', pack['content_hash'], SYSTEM, facts, SCHEMA)
        issues, attempts = check_draft(pack, reply), 1
        if issues:
            retry = (facts + '\n\nYour previous draft failed these checks. Fix them and keep to the facts:\n- ' + '\n- '.join(issues))
            reply = client.complete('commentary-retry', pack['content_hash'], SYSTEM, retry, SCHEMA)
            attempts = 2
        draft = reply
        reply, notes = clean_draft(pack, reply)
        source = f'AI ({client.label})'
    except AIError as error:
        ai_error, draft, notes = str(error), None, []
        reply = {'executive_summary': standard_summary(pack),
                 'lines': [{'line_id': i, 'commentary': standard_line(pack, l)} for i, l in material_lines(pack) if l['note']]}
        source = 'standard wording (AI unavailable)'
    issues = check_draft(pack, reply)  # always empty unless the engine's own wording is wrong: a bug, shown as such
    replaced = sum(n['where'] != 'Draft' for n in notes)
    return {'reply': reply, 'draft': draft, 'issues': issues, 'notes': notes, 'replaced': replaced,
            'attempts': attempts, 'model': source, 'ai_error': ai_error,
            'markdown': to_markdown(pack, reply, issues, source, replaced),
            'html': commentary_html(pack, reply, issues, source, replaced)}

def source_note(model, replaced):
    """Footer wording: who wrote the text and how many AI sentences were swapped for standard wording."""
    if model.startswith('standard'):
        return 'Written with standard wording by the engine (AI unavailable).'
    swap = f' {replaced} AI item(s) that failed the check use standard wording instead.' if replaced else ''
    return f'Drafted by {model}.{swap}'


def to_markdown(pack, reply, issues, model, replaced=0):
    basis, lines = pack['basis'], material_lines(pack)
    written = {e['line_id']: e['commentary'] for e in reply['lines']}
    out = [f"# CFO commentary: {pack['period']} {'P&L' if pack.get('kind') == 'pl' else 'expenses'} vs {basis}", '']
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
    out += ['---', f"{source_note(model, replaced)} Locked pack `{pack['content_hash'][:12]}`. "
                   f"Figures tick-and-tied to the pack: {status}. Explanations are reviewer-supplied. Review before use."]
    return '\n'.join(out) + '\n'
