"""AI agents, tick-and-tie checker and provider connection. No network or AI model is needed."""
import json
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

import requests
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from finance import load_csv, sample_csv  # noqa: E402
from pack import build_pack, lock_pack, pack_json  # noqa: E402
from checker import allowed_figures, check_line_text, unsupported_figures  # noqa: E402
from llm import DEMO, AIError, Client  # noqa: E402
from agents import material_lines  # noqa: E402
from agents.interviewer import by_owner, interview, query_list_csv, query_list_markdown  # noqa: E402
from agents.writer import write_commentary  # noqa: E402
from record_demo import demo_pack  # noqa: E402

APP = ROOT / 'app.py'


class FakeClient:
    """Scripted answers in order; raises AIError when told to."""
    label = 'Fake model'

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def complete(self, task, cache_key, system, user, schema):
        self.calls.append((task, user))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def key(line):
    brand = line['dims'].get('brand')
    return f"{line['dims']['cost_center']}{'/' + brand if brand else ''}|{line['account']}"


def good_reply(pack):
    text = {
        'PC-IN/Nimbus|Product sales': 'Sales were INR 360,000.00 below budget, an unfavorable movement. A retailer delisting '
                                      'explains INR 300,000.00; INR 60,000.00 remains unexplained.',
        'PC-IN/Aurora|Cost of goods sold': 'Costs were INR 300,000.00 above budget, fully explained by higher input costs.',
        'IN-OPS|Distribution': 'Costs were INR 240,000.00 above budget. Urgent air freight explains INR 180,000.00; '
                               'INR 60,000.00 remains unexplained.',
        'AU-SAL|Employee costs': 'Costs were INR 150,000.00 below budget, a favorable movement fully explained by two vacant roles.',
        'SG-OPS|Employee costs': 'Costs were INR 90,000.00 below budget because two supervisors moved to Singapore Sales.',
        'IN-SAL|Employee costs': 'Costs were INR 60,000.00 above budget, part of a wider account movement. '
                                 'The explanation is unconfirmed and INR 60,000.00 remains unexplained.',
        'AU-OPS|Distribution': 'Costs were INR 70,000.00 above budget. The explanations conflict, so INR 70,000.00 remains unexplained.',
        'IN-COR|Consulting': 'Costs were INR 75,000.00 above budget, fully explained by unbudgeted GST audit support.',
        'IN-COR|Recruitment': 'Costs were INR 15,000.00 below budget, fully explained by an agency credit note. '
                              'The actual is a negative balance of INR −5,000.00.'}
    return {'executive_summary': ['Expenses were INR 365,000.00 above budget, an unfavorable outcome.',
                                  'Singapore Employee costs net INR 0.00 hides INR 180,000.00 of offsetting movements.',
                                  'INR 575,000.00 remains unexplained across 11 open items.'],
            'lines': [{'line_id': i, 'commentary': text[key(l)]} for i, l in material_lines(pack) if l['note']]}


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.pack = demo_pack()
        self.allowed = allowed_figures(self.pack)
        self.lines = {key(l): l for _, l in material_lines(self.pack)}

    def test_figures_must_trace_to_pack(self):
        for ok in ('INR 200,000.00 above budget (20.0%)', 'gross INR 280,000', 'about 2 lakh', 'INR 1.24 crore of spend',
                   '7.5%', 'invoice FR-0812', 'in FY27', 'cost centre CC-1001', 'August 2026', 'INR 2,40,000'):
            with self.subTest(ok=ok):
                self.assertEqual(unsupported_figures(ok, self.allowed), [])
        for bad in ('INR 213,000', '23%', '4 crore', 'INR 199,999.99'):  # 3 crore is now the sales budget
            with self.subTest(bad=bad):
                self.assertTrue(unsupported_figures(bad, self.allowed))

    def test_direction_status_and_hidden_gap(self):
        distribution = self.lines['IN-OPS|Distribution']
        self.assertIn('hides the unexplained INR 60,000.00',
                      check_line_text('INR 240,000.00 above budget due to freight.', distribution)[0])
        self.assertEqual(check_line_text('INR 60,000.00 remains unexplained; unfavorable.', distribution), [])
        self.assertTrue(check_line_text('A favorable movement; INR 60,000.00 unexplained.', distribution))
        unconfirmed = self.lines['IN-SAL|Employee costs']
        self.assertIn('unconfirmed', check_line_text('INR 60,000.00 above budget.', unconfirmed)[0])
        for wording in ('The explanation has not been confirmed; INR 60,000.00 is open.',
                        'No explanation confirmed; INR 60,000.00 is open.', 'Unconfirmed; INR 60,000.00 is open.'):
            self.assertEqual(check_line_text(wording, unconfirmed), [])
        self.assertTrue(check_line_text('The explanation is confirmed; INR 60,000.00 is open.', unconfirmed))
        self.assertTrue(check_line_text('INR 70,000.00 above budget.', self.lines['AU-OPS|Distribution']))


class InterviewerTests(unittest.TestCase):
    def setUp(self):
        self.pack = demo_pack()
        self.ids = {key(l): i for i, l in material_lines(self.pack)}

    def findings(self, client):
        found, message = interview(self.pack, client)
        by_line = {}
        for f, (_, line) in zip(found, material_lines(self.pack)):
            by_line[key(line)] = f
        return by_line, message, found

    def test_rules_and_ai_questions_combine(self):
        client = FakeClient({'reviews': [
            {'line_id': self.ids['IN-SAL|Employee costs'], 'quality': 'vague', 'question': 'Which roles or pay changes increased costs?'},
            {'line_id': self.ids['IN-COR|Consulting'], 'quality': 'specific', 'question': ''},
            {'line_id': self.ids['AU-OPS|Distribution'], 'quality': 'vague', 'question': 'Was it INR 999,999?'}]})
        found, message, _ = self.findings(client)
        self.assertIsNone(message)
        self.assertEqual(found['IN-SAL|Employee costs']['questions'][0], 'Which roles or pay changes increased costs?')
        self.assertTrue(any('Who can confirm' in q for q in found['IN-SAL|Employee costs']['questions']))
        self.assertTrue(any('remaining INR 60,000.00' in q for q in found['IN-OPS|Distribution']['questions']))
        self.assertEqual(found['IN-COR|Consulting']['questions'], [])
        self.assertFalse(any('999,999' in q for q in found['AU-OPS|Distribution']['questions']))
        self.assertEqual(found['SG-OPS|Warehousing']['quality'], 'missing')
        self.assertIn('below the line threshold', found['IN-OPS|Employee costs']['questions'][0])
        sent = json.loads(client.calls[0][1])
        self.assertEqual(len(sent), 9)
        self.assertIn('cost_centre', sent[0])
        self.assertNotIn('amount_stated', sent[0])

    def test_query_list_groups_questions_by_owner(self):
        _, _, found = self.findings(FakeClient(AIError('offline')))
        owners = by_owner(found)
        self.assertEqual(list(owners)[0], 'Priya Nair')
        self.assertNotIn('Olivia Brown', [n for n, items in owners.items() if not items])
        text = query_list_markdown(found, '2026-08', 'budget')
        self.assertIn('## Priya Nair: 4 question(s)', text)
        self.assertIn('- [ ] ', text)
        self.assertTrue(query_list_csv(found).startswith('owner,line,movement,open_amount,question'))

    def test_ai_unavailable_still_gives_rule_questions(self):
        found, message, _ = self.findings(FakeClient(AIError('Could not reach Ollama.')))
        self.assertIn('Could not reach Ollama', message)
        self.assertTrue(found['SG-OPS|Warehousing']['questions'])


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.pack = lock_pack(demo_pack())

    def test_good_draft_passes_first_time(self):
        result = write_commentary(self.pack, FakeClient(good_reply(self.pack)))
        self.assertEqual((result['issues'], result['attempts']), ([], 1))
        for expected in ('Explanation pending from the owner', '## Open items', 'Agree a lead-time buffer',
                         '### India | Distribution: net INR 200,000.00 unfavorable', '⇅ Offsetting movements',
                         'Σ Material in total only', 'IN-OPS India Operations · Arjun Mehta', 'tick-and-tied to the pack: passed'):
            self.assertIn(expected, result['markdown'])

    def test_offsets_are_given_to_the_ai(self):
        client = FakeClient(good_reply(self.pack))
        write_commentary(self.pack, client)
        facts = json.loads(client.calls[0][1])
        self.assertTrue(any('Singapore | Employee costs' in o and 'INR 180,000.00' in o for o in facts['offsets']))
        self.assertEqual(facts['analysed_by'], 'country x cost center x brand x account')

    def test_bad_draft_is_retried_with_the_problems(self):
        bad = good_reply(self.pack)
        bad['executive_summary'][0] = 'Expenses were INR 413,000.00 above budget.'
        client = FakeClient(bad, good_reply(self.pack))
        result = write_commentary(self.pack, client)
        self.assertEqual((result['issues'], result['attempts']), ([], 2))
        self.assertIn('"413,000.00" is not in the pack', client.calls[1][1])

    def test_failing_twice_publishes_only_what_passes(self):
        bad = good_reply(self.pack)
        dropped = bad['lines'][0]['line_id']
        bad['lines'] = bad['lines'][1:]
        bad['executive_summary'][1] = 'See L03 for details.'
        client = FakeClient(bad, bad)
        result = write_commentary(self.pack, client)
        self.assertEqual(len(client.calls), 2)  # one retry with the problems listed
        self.assertEqual(result['issues'], [])  # what is published ties out
        self.assertEqual(result['replaced'], 2)
        notes = {n['where']: n for n in result['notes']}
        self.assertIn('missing commentary', notes[dict(material_lines(self.pack))[dropped]['label']]['problems'][0])
        self.assertEqual(notes['Executive summary, bullet 2']['ai_text'], 'See L03 for details.')
        for output in (result['markdown'], result['html']):
            self.assertNotIn('See L03', output)
            self.assertNotIn('FAILED', output)
        self.assertIn('2 AI item(s) that failed the check use standard wording instead', result['markdown'])
        self.assertTrue(result['reply']['lines'][0]['commentary'].startswith('Revenue is INR'))  # the Nimbus sales line

    def test_ai_down_gives_standard_commentary(self):
        result = write_commentary(self.pack, FakeClient(AIError('Could not reach Ollama.')))
        self.assertEqual((result['issues'], result['ai_error']), ([], 'Could not reach Ollama.'))
        explained = [l for _, l in material_lines(self.pack) if l['note']]
        self.assertEqual(len(result['reply']['lines']), len(explained))
        conflicting = next(e['commentary'] for e, (_, l) in zip(result['reply']['lines'],
                                                                [x for x in material_lines(self.pack) if x[1]['note']])
                           if l['note']['status'] == 'Conflicting explanations')
        self.assertIn('The explanations conflict', conflicting)

    def test_edited_pack_is_refused(self):
        tampered = json.loads(pack_json(self.pack))
        tampered['totals']['variance'] = '1'
        with self.assertRaises(ValueError):
            write_commentary(tampered, FakeClient(good_reply(self.pack)))

    def test_recorded_demo_still_matches_and_passes(self):
        client = Client(DEMO)
        findings, message = interview(self.pack, client)
        self.assertIsNone(message, 'Recordings are stale: run scripts/record_demo.py')
        self.assertTrue(any(f['quality'] == 'vague' for f in findings))
        self.assertEqual(write_commentary(self.pack, client)['issues'], [])
        other = lock_pack(build_pack(load_csv(sample_csv()), 'forecast', D('50000'), D('5'), {}))
        fallback = write_commentary(other, client)  # no recording for this pack: standard wording, still clean
        self.assertIn('Demo mode only has recorded answers', fallback['ai_error'])
        self.assertEqual((fallback['issues'], fallback['model']), ([], 'standard wording (AI unavailable)'))
        self.assertIn('Written with standard wording by the engine', fallback['markdown'])



class ConnectionTests(unittest.TestCase):
    SCHEMA = {'type': 'object', 'required': ['answer'], 'properties': {'answer': {'type': 'string'}}}

    def reply(self, status, content='{"answer": "ok"}'):
        response = mock.Mock(status_code=status)
        response.json.return_value = {'choices': [{'message': {'content': content}}]}
        return response

    def ask(self, *responses, provider='Ollama (this computer)', key=None):
        with mock.patch('llm.requests.post', side_effect=list(responses)) as post:
            result = Client(provider, api_key=key, recordings={}).complete('t', 'k', 'system', 'user', self.SCHEMA)
        return result, post

    def test_structured_reply_and_plain_json_fallback(self):
        result, post = self.ask(self.reply(400), self.reply(200))
        self.assertEqual(result, {'answer': 'ok'})
        self.assertEqual(post.call_args_list[0].kwargs['json']['response_format']['type'], 'json_schema')
        self.assertEqual(post.call_args_list[1].kwargs['json']['response_format']['type'], 'json_object')
        self.assertEqual(post.call_args_list[0].kwargs['json']['reasoning_effort'], 'none')

    def test_errors_become_plain_messages(self):
        cases = ((self.reply(429), 'usage limit'), (self.reply(401), 'rejected the API key'),
                 (self.reply(200, 'not json'), 'required format'), (self.reply(200, '{"other": 1}'), 'required format'),
                 (requests.ConnectionError(), 'Is Ollama running?'))
        for response, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(AIError, message):
                self.ask(response)
        with self.assertRaisesRegex(AIError, 'needs an API key'):
            self.ask(provider='Groq (free tier)')
        _, post = self.ask(self.reply(200), provider='Groq (free tier)', key='test-key')
        self.assertEqual(post.call_args.kwargs['headers'], {'Authorization': 'Bearer test-key'})


class AIAppTests(unittest.TestCase):
    def test_demo_mode_end_to_end(self):
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        button = lambda label: next(b for b in app.button if b.label == label)
        button('Load demo explanations').click().run()
        self.assertTrue(any('Loaded 9 demo explanations' in x.value for x in app.success))
        button('Ask the interviewer').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('vague wording' in x.value for x in app.markdown))
        self.assertTrue(any(x.label.startswith('👤 Priya Nair') for x in app.expander))
        button('Lock review pack').click().run()
        button('Draft CFO commentary and deck').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('Every figure ties to the locked pack' in x.value for x in app.success))
        shown = app.session_state['ai_commentary']
        self.assertIn('✔ Tick-and-tie passed', shown['html'])
        self.assertIn('# CFO commentary: 2026-08', shown['markdown'])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.pack = lock_pack(demo_pack())

    def test_indicators_tiles_and_sections(self):
        page = write_commentary(self.pack, FakeClient(good_reply(self.pack)))['html']
        for expected in ('▲ Unfavorable', '▼ Favorable', '✅ Confirmed', '🟡 Unconfirmed', '🔴 Conflicting',
                         '⚪ Explanation pending', '① One-off', 'Explained INR 180,000.00 (75%)', 'Fully explained',
                         'Not counted until resolved', 'Open items (11)', '☐', '✔ Tick-and-tie passed', 'INR 2,270,000', '⚠ Negative balance',
                         'Profit and loss', 'Gross margin', 'EBITDA margin',
                         'Where the variance sits', '👤 Arjun Mehta', '⇅ Offsetting movements',
                         'Σ Part of a material account total', 'offsets hide INR 80,000.00'):
            self.assertIn(expected, page)
        self.assertEqual(page.count('border-left:5px solid'), 16)

    def test_heatmap_cells_totals_and_filter(self):
        from report_html import heatmap_html
        grid = heatmap_html(self.pack)
        self.assertIn('+240k', grid)
        self.assertIn('−90k', grid)
        self.assertEqual(grid.count('outline:2px solid'), 16)
        self.assertIn('−195k', grid)  # P&L demo: the grand net is profit impact (net profit 195k below budget)
        india = heatmap_html(self.pack, 'India')
        self.assertNotIn('Singapore', india)
        self.assertIn('IN-OPS India Operations', india)

    def test_failed_draft_and_escaping(self):
        bad = good_reply(self.pack)
        bad['executive_summary'][0] = '<script>alert(1)</script> INR 999.00'
        bad['executive_summary'][1] = '<b>Net result</b> as planned.'
        page = write_commentary(self.pack, FakeClient(bad, bad))['html']
        self.assertNotIn('FAILED NUMBER CHECK', page)
        self.assertNotIn('999.00', page)  # the failing bullet is removed, not shown
        self.assertNotIn('<b>Net result', page)
        self.assertIn('&lt;b&gt;Net result&lt;/b&gt;', page)  # passing text is still HTML-escaped


if __name__ == '__main__':
    unittest.main()
