"""Rebuild demo/explanations.json and record live AI answers into demo/recordings.json.

Run from the project folder with Ollama running:
    .venv\\Scripts\\python scripts\\record_demo.py
"""
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finance import analyze, load_csv, sample_csv  # noqa: E402
from evidence import make_note  # noqa: E402
from pack import build_pack, export_notes, lock_pack  # noqa: E402
from llm import Client  # noqa: E402
from agents.interviewer import interview  # noqa: E402
from agents.writer import write_commentary  # noqa: E402

# Fictional explanations on the budget basis, one per review situation.
DEMO_NOTES = [
    ('India', 'Distribution', 'Urgent air freight for festive-season launch stock after a supplier delay.',
     'Fictional freight invoices FR-0812', '150000', 'Confirmed by reviewer', 'One-off',
     'Agree a lead-time buffer with the supplier.'),
    ('Australia', 'Employee costs', 'Two open roles in the Sydney sales team were vacant for all of August.',
     'Fictional HR headcount report, August 2026', '150000', 'Confirmed by reviewer', 'Timing difference',
     'Confirm start dates; the saving reverses once roles are filled.'),
    ('India', 'Employee costs', 'Higher costs.', 'Email from HR', '100000', 'Unconfirmed', 'Unknown', ''),
    ('Australia', 'Distribution', 'Logistics says carrier rates rose; commercial says volume grew with new stores.',
     'Fictional carrier notice and store opening plan', '100000', 'Conflicting explanations', 'Recurring', ''),
    ('India', 'Consulting', 'Unbudgeted tax advisory support for the GST audit.', 'Fictional engagement letter EL-22',
     '75000', 'Confirmed by reviewer', 'One-off', 'Add audit support to next year\'s budget.'),
]


def demo_notes(rows, basis='budget'):
    by_line = {(r['country'], r['account']): r for r in analyze(rows, basis)}
    notes = {}
    for country, account, *fields in DEMO_NOTES:
        note = make_note(by_line[(country, account)], basis, *fields)
        notes[note['key']] = note
    return notes


def main():
    rows = load_csv(sample_csv())
    notes = demo_notes(rows)
    (ROOT / 'demo').mkdir(exist_ok=True)
    (ROOT / 'demo' / 'explanations.json').write_text(export_notes(notes) + '\n', encoding='utf-8')
    recorder = {}
    client = Client('Ollama (this computer)', recordings={}, recorder=recorder)
    pack = build_pack(rows, 'budget', Decimal('50000'), Decimal('5'), notes)
    findings, message = interview(pack, client)
    print('Interviewer:', message or 'ok')
    for f in findings:
        print(f"  {f['country']} | {f['account']} [{f['quality']}]")
        for q in f['questions']:
            print('    -', q)
    result = write_commentary(lock_pack(pack), client)
    print('Writer: attempts', result['attempts'], '| issues', result['issues'] or 'none')
    print(result['markdown'])
    (ROOT / 'demo' / 'recordings.json').write_text(json.dumps(
        {'model': client.label, 'recorded_on': date.today().isoformat(), 'responses': recorder},
        indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
