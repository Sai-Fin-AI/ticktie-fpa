"""Interviewer: asks follow-up questions before the pack is locked.

Fixed rules catch the gaps a reviewer always chases (missing, unconfirmed, partial, timing).
The AI only judges whether the wording of an explanation is specific enough.
"""
import json
from decimal import Decimal

from agents import inr, line_facts, material_lines
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
                         'movement': f"{inr(line['variance'])} {line['direction'].lower()}",
                         'questions': rule_questions(line, basis), 'quality': None if line['note'] else 'missing'})
    explained = [(i, l) for i, l in material_lines(pack) if l['note']]
    if not explained:
        return findings, None
    facts = [{k: v for k, v in line_facts(i, l, basis).items()
              if k in ('line_id', 'country', 'account', 'outcome', 'explanation')} for i, l in explained]
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
