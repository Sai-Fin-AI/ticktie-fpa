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

from finance import (SALES, aggregate, analyze, apply_master, load_cc_master, load_csv, merge_datasets,  # noqa: E402
                     sample_cc_master_csv, sample_csv, sample_pl_csv)
from evidence import make_note  # noqa: E402
from pack import build_pack, export_notes, lock_pack  # noqa: E402
from llm import Client  # noqa: E402
from agents.interviewer import interview  # noqa: E402
from agents.writer import write_commentary  # noqa: E402

# The app's default settings for the sample data: analysed by cost centre, budget basis.
LINE_THRESHOLDS = (Decimal('25000'), Decimal('10'))
TOTAL_THRESHOLDS = (Decimal('50000'), Decimal('5'))

# Fictional explanations on the budget basis, one per review situation.
DEMO_NOTES = [
    ('IN-OPS', 'Distribution', 'Urgent air freight for festive-season launch stock after a supplier delay.',
     'Fictional freight invoices FR-0812', '180000', 'Confirmed by reviewer', 'One-off',
     'Agree a lead-time buffer with the supplier.'),
    ('AU-SAL', 'Employee costs', 'Two open roles in the Sydney sales team were vacant for all of August.',
     'Fictional HR headcount report, August 2026', '150000', 'Confirmed by reviewer', 'Timing difference',
     'Confirm start dates; the saving reverses once roles are filled.'),
    ('SG-OPS', 'Employee costs', 'Two warehouse supervisors moved to the Singapore Sales cost centre in July; '
     'their pay is now charged to SG-SAL.', 'Fictional HR transfer forms', '90000', 'Confirmed by reviewer', 'Recurring',
     'Move the budget for both roles to SG-SAL in the reforecast.'),
    ('IN-SAL', 'Employee costs', 'Higher costs.', 'Email from HR', '60000', 'Unconfirmed', 'Unknown', ''),
    ('AU-OPS', 'Distribution', 'Logistics says carrier rates rose; commercial says volume grew with new stores.',
     'Fictional carrier notice and store opening plan', '70000', 'Conflicting explanations', 'Recurring', ''),
    ('IN-COR', 'Consulting', 'Unbudgeted tax advisory support for the GST audit.', 'Fictional engagement letter EL-22',
     '75000', 'Confirmed by reviewer', 'One-off', 'Add audit support to next year\'s budget.'),
]


def demo_rows():
    """The app's demo: the expense sample plus the sales and P&L sample, with the cost centre master."""
    combined = merge_datasets(load_csv(sample_csv()), load_csv(sample_pl_csv(), default_line=SALES))
    dataset, _ = apply_master(combined, load_cc_master(sample_cc_master_csv()))
    return aggregate(dataset, dataset.dims)


def demo_notes(rows, basis='budget'):
    by_line = {(r['cost_center'], r['account']): r for r in analyze(rows, basis)}
    notes = {}
    for cost_center, account, *fields in DEMO_NOTES:
        note = make_note(by_line[(cost_center, account)], basis, *fields)
        notes[note['key']] = note
    return notes


def demo_pack(notes=None):
    rows = demo_rows()
    return build_pack(rows, 'budget', *LINE_THRESHOLDS, demo_notes(rows) if notes is None else notes, TOTAL_THRESHOLDS)


def main():
    rows = demo_rows()
    notes = demo_notes(rows)
    (ROOT / 'demo').mkdir(exist_ok=True)
    (ROOT / 'demo' / 'explanations.json').write_text(export_notes(notes) + '\n', encoding='utf-8')
    recorder = {}
    client = Client('Ollama (this computer)', recordings={}, recorder=recorder)
    pack = demo_pack(notes)
    findings, message = interview(pack, client)
    print('Interviewer:', message or 'ok')
    for f in findings:
        print(f"  {f['country']} | {f['account']} [{f['quality']}]")
        for q in f['questions']:
            print('    -', q)
    result = write_commentary(lock_pack(pack), client)
    print('Writer: attempts', result['attempts'], '| issues', result['issues'] or 'none')
    print(result['markdown'])
    from agents.deck import make_deck
    deck = make_deck(lock_pack(pack), client)
    print('Deck:', deck['source'], '| issues', deck['issues'] or 'none')
    for kicker, headline in deck['outline']:
        print(f'  {kicker}: {headline}')
    print('  Summary:', deck['summary'])
    (ROOT / 'demo' / 'recordings.json').write_text(json.dumps(
        {'model': client.label, 'recorded_on': date.today().isoformat(), 'responses': recorder},
        indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
