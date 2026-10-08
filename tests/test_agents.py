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
from agents.interviewer import interview  # noqa: E402
from agents.writer import write_commentary  # noqa: E402
from record_demo import demo_notes  # noqa: E402

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


def demo_pack():
    rows = load_csv(sample_csv())
    return build_pack(rows, 'budget', D('50000'), D('5'), demo_notes(rows))


def good_reply(pack):
    text = {
        'India|Distribution': 'Costs were INR 200,000.00 above budget. Urgent air freight explains INR 150,000.00; '
                              'INR 50,000.00 remains unexplained.',
        'Australia|Employee costs': 'Costs were INR 150,000.00 below budget, a favorable movement fully explained by two vacant roles.',
        'India|Employee costs': 'Costs were INR 100,000.00 above budget. The explanation is unconfirmed and INR 100,000.00 remains unexplained.',
        'Australia|Distribution': 'Costs were INR 100,000.00 above budget. The explanations conflict, so INR 100,000.00 remains unexplained.',
        'India|Consulting': 'Costs were INR 75,000.00 above budget, fully explained by unbudgeted GST audit support.'}
    return {'executive_summary': ['Expenses were INR 380,000.00 above budget, an unfavorable outcome.',
                                  'INR 495,000.00 remains unexplained across 7 open items.'],
            'lines': [{'line_id': i, 'commentary': text[f"{l['country']}|{l['account']}"]}
                      for i, l in material_lines(pack) if l['note']]}


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.pack = demo_pack()
        self.allowed = allowed_figures(self.pack)
        self.lines = {f"{l['country']}|{l['account']}": l for _, l in material_lines(self.pack)}

    def test_figures_must_trace_to_pack(self):
        for ok in ('INR 200,000.00 above budget (20.0%)', 'about 2 lakh', 'INR 1.24 crore of spend', '7.5%',
                   'invoice FR-0812', 'in FY27', 'August 2026', 'INR 2,00,000'):
            with self.subTest(ok=ok):
                self.assertEqual(unsupported_figures(ok, self.allowed), [])
        for bad in ('INR 213,000', '21%', '3 crore', 'INR 199,999.99'):
            with self.subTest(bad=bad):
                self.assertTrue(unsupported_figures(bad, self.allowed))

    def test_direction_status_and_hidden_gap(self):
        distribution = self.lines['India|Distribution']
        self.assertIn('hides the unexplained INR 50,000.00',
                      check_line_text('INR 200,000.00 above budget due to freight.', distribution)[0])
        self.assertEqual(check_line_text('INR 50,000.00 remains unexplained; unfavorable.', distribution), [])
        self.assertTrue(check_line_text('A favorable movement; INR 50,000.00 unexplained.', distribution))
        unconfirmed = self.lines['India|Employee costs']
        self.assertIn('unconfirmed', check_line_text('INR 100,000.00 above budget.', unconfirmed)[0])
        for wording in ('The explanation has not been confirmed; INR 100,000.00 is open.',
                        'No explanation confirmed; INR 100,000.00 is open.', 'Unconfirmed; INR 100,000.00 is open.'):
            self.assertEqual(check_line_text(wording, unconfirmed), [])
        self.assertTrue(check_line_text('The explanation is confirmed; INR 100,000.00 is open.', unconfirmed))
        self.assertTrue(check_line_text('INR 100,000.00 above budget.', self.lines['Australia|Distribution']))


class InterviewerTests(unittest.TestCase):
    def setUp(self):
        self.pack = demo_pack()
        self.ids = {f"{l['country']}|{l['account']}": i for i, l in material_lines(self.pack)}

    def findings(self, client):
        found, message = interview(self.pack, client)
        return {f"{f['country']}|{f['account']}": f for f in found}, message

    def test_rules_and_ai_questions_combine(self):
        vague = self.ids['India|Employee costs']
        client = FakeClient({'reviews': [
            {'line_id': vague, 'quality': 'vague', 'question': 'Which roles or pay changes increased costs?'},
            {'line_id': self.ids['India|Consulting'], 'quality': 'specific', 'question': ''},
            {'line_id': self.ids['Australia|Distribution'], 'quality': 'vague', 'question': 'Was it INR 999,999?'}]})
        found, message = self.findings(client)
        self.assertIsNone(message)
        self.assertEqual(found['India|Employee costs']['questions'][0], 'Which roles or pay changes increased costs?')
        self.assertTrue(any('Who can confirm' in q for q in found['India|Employee costs']['questions']))
        self.assertTrue(any('remaining INR 50,000.00' in q for q in found['India|Distribution']['questions']))
        self.assertEqual(found['India|Consulting']['questions'], [])
        self.assertFalse(any('999,999' in q for q in found['Australia|Distribution']['questions']))
        self.assertEqual(found['Singapore|Warehousing']['quality'], 'missing')
        sent = json.loads(client.calls[0][1])
        self.assertEqual(len(sent), 5)
        self.assertNotIn('amount_stated', sent[0])

    def test_ai_unavailable_still_gives_rule_questions(self):
        found, message = self.findings(FakeClient(AIError('Could not reach Ollama.')))
        self.assertIn('Could not reach Ollama', message)
        self.assertTrue(found['Singapore|Warehousing']['questions'])


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.pack = lock_pack(demo_pack())

    def test_good_draft_passes_first_time(self):
        result = write_commentary(self.pack, FakeClient(good_reply(self.pack)))
        self.assertEqual((result['issues'], result['attempts']), ([], 1))
        for expected in ('Explanation pending from the account owner', '## Open items',
                         'Agree a lead-time buffer', 'tick-and-tied to the pack: passed'):
            self.assertIn(expected, result['markdown'])

    def test_bad_draft_is_retried_with_the_problems(self):
        bad = good_reply(self.pack)
        bad['executive_summary'][0] = 'Expenses were INR 400,000.00 above budget.'
        client = FakeClient(bad, good_reply(self.pack))
        result = write_commentary(self.pack, client)
        self.assertEqual((result['issues'], result['attempts']), ([], 2))
        self.assertIn('"400,000.00" is not in the pack', client.calls[1][1])

    def test_failing_twice_blocks_the_draft(self):
        bad = good_reply(self.pack)
        bad['lines'] = bad['lines'][1:]
        bad['executive_summary'][1] = 'See L03 for details.'
        result = write_commentary(self.pack, FakeClient(bad, bad))
        self.assertTrue(any('missing commentary' in i for i in result['issues']))
        self.assertTrue(any('internal code L03' in i for i in result['issues']))
        self.assertIn('FAILED NUMBER CHECK', result['markdown'])

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
        with self.assertRaises(AIError):
            write_commentary(other, client)


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
        self.assertTrue(any('Loaded 5 demo explanations' in x.value for x in app.success))
        button('Ask the interviewer').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('vague wording' in x.value for x in app.markdown))
        button('Lock review pack').click().run()
        button('Draft CFO commentary').click().run()
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
                         '⚪ Explanation pending', '① One-off', 'Explained INR 150,000.00 (75%)', 'Fully explained',
                         'Not counted until resolved', 'Open items (7)', '☐', '✔ Tick-and-tie passed', 'INR 12,380,000'):
            self.assertIn(expected, page)
        self.assertEqual(page.count('border-left:5px solid'), 9)

    def test_failed_draft_and_escaping(self):
        bad = good_reply(self.pack)
        bad['executive_summary'][0] = '<script>alert(1)</script> INR 999.00'
        page = write_commentary(self.pack, FakeClient(bad, bad))['html']
        self.assertIn('✖ FAILED NUMBER CHECK', page)
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)


if __name__ == '__main__':
    unittest.main()
