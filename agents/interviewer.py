"""Interviewer: asks follow-up questions before the pack is locked.

Fixed rules catch the gaps a reviewer always chases (missing, unconfirmed, partial, timing).
The AI only judges whether the wording of an explanation is specific enough.
"""
import json
from decimal import Decimal

from agents import inr, line_facts, material_lines, owner
from checker import allowed_figures, unsupported_figures
from llm import AIError

SYSTEM = """You are a senior FP&A manager reviewing variance explanations before the CFO pack is locked.
For each explanation, decide whether it is specific (names a concrete business driver, such as volume, price,
an event, or a timing shift) or vague (generic wording such as "higher costs" or "more spend").
For vague ones, ask ONE short follow-up question that would make it specific. For specific ones, set question to "".
Do not ask about confirmation, timing classification or the amount covered; those are handled separately.
Never invent causes. Do not quote any figures."""

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['reviews'], 'properties': {
    'reviews': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                           'required': ['line_id', 'quality', 'question'], 'properties': {
            'line_id': {'type': 'string'}, 'quality': {'type': 'string', 'enum': ['specific', 'vague']},
            'question': {'type': 'string'}}}}}}


def rule_questions(line, basis):
    note, variance = line['note'], Decimal(line['variance'])
    if note is None and line.get('scope') == 'Account total':
        return [f"This {inr(variance)} is below the line threshold, but the {line['account']} total is material once "
                f"all lines are added up. What drove your share, and what document supports it?"]
    if note is None:
        return [f"No explanation yet. What drove this {inr(variance)} {'overspend' if variance > 0 else 'underspend'} "
                f"against {basis}, and what document supports it?"]
    questions = []
    if note['status'] == 'Unconfirmed':
        questions.append('Who can confirm this explanation, and is the supporting reference final?')
    if note['status'] == 'Conflicting explanations':
        questions.append(f"Which explanation is right, or how much of the {inr(variance)} belongs to each?")
    if note['status'] == 'Confirmed by reviewer' and Decimal(line['unexplained']) > 0:
        questions.append(f"The explanation covers {inr(note['amount'])} of {inr(variance)}. "
                         f"What explains the remaining {inr(line['unexplained'])}?")
    if note['timing'] == 'Unknown':
        questions.append('Is this one-off, recurring or a timing difference? It decides whether the full-year outlook changes.')
    if note['timing'] == 'Recurring' and not note['action']:
        questions.append('This is recurring. What action or forecast update is planned, and who owns it?')
    return questions


def interview(pack, client):
    """Return (findings, ai_message). Rule questions are always returned, even if the AI is unavailable."""
    basis, findings = pack['basis'], []
    for line_id, line in material_lines(pack):
        findings.append({'line_id': line_id, 'country': line['country'], 'account': line['account'],
                         'label': line.get('label', f"{line['country']} | {line['account']}"), 'owner': owner(line),
                         'movement': f"{inr(line['variance'])} {line['direction'].lower()}", 'open': inr(line['unexplained']),
                         'questions': rule_questions(line, basis), 'quality': None if line['note'] else 'missing'})
    explained = [(i, l) for i, l in material_lines(pack) if l['note']]
    if not explained:
        return findings, None
    facts = [{k: v for k, v in line_facts(i, l, basis).items()
              if k in ('line_id', 'country', 'cost_centre', 'account', 'outcome', 'explanation')} for i, l in explained]
    try:
        reply = client.complete('interview', pack['content_hash'], SYSTEM, json.dumps(facts, ensure_ascii=False), SCHEMA)
    except AIError as error:
        return findings, f'AI wording review skipped: {error}'
    by_id, allowed = {f['line_id']: f for f in findings}, allowed_figures(pack)
    for review in reply['reviews']:
        finding = by_id.get(review['line_id'])
        if finding is None or finding['quality'] == 'missing':
            continue
        finding['quality'] = review['quality']
        question = review['question'].strip()
        # Questions are shown to people, so they get the same tick-and-tie as commentary.
        if review['quality'] == 'vague' and question and not unsupported_figures(question, allowed):
            finding['questions'].insert(0, question)
    return findings, None


def by_owner(findings):
    """{owner: [findings with questions]}, owners with the most questions first."""
    groups = {}
    for f in findings:
        if f['questions']:
            groups.setdefault(f['owner'], []).append(f)
    return dict(sorted(groups.items(), key=lambda kv: (-sum(len(f['questions']) for f in kv[1]), kv[0])))


def query_list_markdown(findings, period, basis):
    out = [f'# Month-end query list: {period} vs {basis}', '']
    for name, items in by_owner(findings).items():
        out += [f"## {name}: {sum(len(f['questions']) for f in items)} question(s)", '']
        for f in items:
            out.append(f"**{f['label']}** · {f['movement']} · {f['open']} open")
            out += [f'- [ ] {q}' for q in f['questions']] + ['']
    return '\n'.join(out) + '\n'


def query_list_csv(findings):
    import csv
    import io
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    # Opened in Excel, a cell starting with = + - or @ would run as a formula; a leading ' keeps it as text.
    safe = lambda text: "'" + text if str(text).startswith(('=', '+', '-', '@', '\t', '\r')) else text
    writer.writerow(('owner', 'line', 'movement', 'open_amount', 'question'))
    for name, items in by_owner(findings).items():
        for f in items:
            for q in f['questions']:
                writer.writerow(tuple(safe(v) for v in (name, f['label'], f['movement'], f['open'], q)))
    return output.getvalue()
