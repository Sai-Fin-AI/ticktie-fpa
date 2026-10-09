"""Streamlit screens for the AI agents, kept apart from app.py so the finance screens stay readable."""
import json
from pathlib import Path

import streamlit as st

from concurrent.futures import ThreadPoolExecutor

from agents.deck import make_deck
from agents.interviewer import by_owner, interview, query_list_csv, query_list_markdown
from agents.writer import write_commentary
from llm import DEMO, PROVIDERS, AIError, Client
from pack import import_notes
from report_html import html_document

DEMO_NOTES = Path(__file__).resolve().parent / 'demo' / 'explanations.json'
SECRET_NAMES = {'Groq (free tier)': 'GROQ_API_KEY', 'Google Gemini (free tier)': 'GEMINI_API_KEY', 'OpenRouter': 'OPENROUTER_API_KEY'}
QUALITY = {'missing': '⚪ no explanation', 'vague': '🟠 vague wording', 'specific': '🟢 specific wording', None: ''}


def reset_note_widgets():
    """Drop cached form values so newly loaded notes are shown."""
    for key in list(st.session_state):
        if key.startswith('evidence_widget_') and not key.startswith('evidence_widget_account_'):
            del st.session_state[key]


def _secret(name):
    try:
        return st.secrets.get(name)
    except Exception:  # no secrets file locally
        return None


def ai_settings():
    with st.sidebar:
        st.header('AI assistant')
        provider = st.selectbox('AI provider', list(PROVIDERS),
                                help='Demo replays recorded answers. Ollama runs free on this computer. '
                                     'Groq, Gemini and OpenRouter are online services with free tiers.')
        config, model, key = PROVIDERS[provider], None, None
        if config is None:
            st.caption('Recorded answers from a local model. They match the sample data with the demo explanations loaded.')
        else:
            model = st.text_input('Model', value=config['model'], key='ai_model_' + provider)
            if config['key']:
                key = _secret(SECRET_NAMES[provider])
                if key:
                    st.caption("Using the app owner's free key; daily use is limited.")
                else:
                    key = st.text_input('API key', type='password', key='ai_key_' + provider,
                                        help='Used only for this session and never saved.')
            st.caption('AI drafts words only. Figures come from the engine and every AI figure is checked against the pack.')
    return Client(provider, model or None, key)


def demo_notes_button(rows):
    if st.button('Load demo explanations', help='Fictional notes covering confirmed, partial, vague, unconfirmed, '
                                                'conflicting and missing explanations. Sample data, budget comparison, '
                                                'analysed by cost centre.'):
        restored, _ = import_notes(DEMO_NOTES.read_text(encoding='utf-8'), rows)
        if restored:
            st.session_state.business_notes.update(restored)
            reset_note_widgets()
            st.success(f'Loaded {len(restored)} demo explanations for the budget comparison.')
        else:
            st.info('Demo explanations only match the sample data analysed by cost centre. '
                    'Clear any uploaded file and keep "Cost centre" under Analyse by.')


def interviewer_section(pack, client):
    st.subheader('🤖 AI interviewer')
    st.caption('Reviews explanations for all countries before you lock. Fixed rules flag missing, unconfirmed and '
               'partial explanations and unknown timing; the AI flags vague wording and suggests a follow-up question.')
    if st.button('Ask the interviewer'):
        with st.spinner('Reviewing explanations. A local model can take up to a minute the first time.'):
            findings, message = interview(pack, client)
        st.session_state.interview = {'hash': pack['content_hash'], 'findings': findings, 'message': message}
    result = st.session_state.get('interview')
    if not result:
        return
    if result['hash'] != pack['content_hash']:
        st.caption('Explanations or settings changed since the last review. Ask again for current questions.')
        return
    if result['message']:
        st.warning(result['message'] + ' Rule-based questions are still shown.')
    owners = by_owner(result['findings'])
    if not owners:
        st.success('No follow-up questions. The explanations are ready to lock.')
        return
    total = sum(len(f['questions']) for items in owners.values() for f in items)
    st.caption(f'{total} question(s) for {len(owners)} owner(s). Send each owner their list.')
    for name, items in owners.items():
        count = sum(len(f['questions']) for f in items)
        with st.expander(f'👤 {name} · {count} question(s) · {len(items)} line(s)', expanded=len(owners) <= 3):
            for finding in items:
                st.markdown(f"**{finding['label']}** · {finding['movement']} · {finding['open']} open · {QUALITY[finding['quality']]}\n"
                            + '\n'.join(f'- {q}' for q in finding['questions']))
    stem = f"query_list_{pack['period']}_{pack['basis']}"
    c1, c2 = st.columns(2)
    c1.download_button('Query list by owner (Markdown)', query_list_markdown(result['findings'], pack['period'], pack['basis']),
                       stem + '.md', 'text/markdown')
    c2.download_button('Query list by owner (CSV for Excel)', query_list_csv(result['findings']), stem + '.csv', 'text/csv')


HEADLINES = ('AI-written, tick-and-tied', 'Standard, written by the engine')


def run_agents(locked, client, deck_client=None):
    """Commentary writer and deck builder read the same locked pack, so they run side by side."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        commentary = pool.submit(write_commentary, locked, client)
        deck = pool.submit(make_deck, locked, deck_client)
        try:
            written = {'hash': locked['content_hash'], **commentary.result()}
        except (AIError, ValueError) as error:
            written = {'hash': locked['content_hash'], 'error': str(error)}
        return written, {'hash': locked['content_hash'], **deck.result()}


def writer_section(locked, client):
    st.subheader('📊 CFO pack: commentary and deck')
    st.caption('Two agents read the same locked pack in parallel. The writer drafts commentary; the deck agent draws every '
               'chart and table from the pack and writes only the headlines. Both are tick-and-tied; failing commentary '
               'cannot be downloaded, and failing headlines are replaced by standard wording.')
    style = st.radio('Deck headlines', HEADLINES, horizontal=True,
                     help='Charts, tables and numbers are always drawn by the engine. This only chooses who writes the slide titles.')
    b1, b2 = st.columns(2)
    if b1.button('Draft CFO commentary and deck', type='primary'):
        with st.spinner('Two agents at work. A local model can take a minute or two.'):
            st.session_state.ai_commentary, st.session_state.ai_deck = run_agents(
                locked, client, client if style == HEADLINES[0] else None)
    if b2.button('🖥️ CFO deck only (PowerPoint, no AI wait)',
                 help='Builds the deck straight away with standard headlines written by the engine.'):
        st.session_state.ai_deck = {'hash': locked['content_hash'], **make_deck(locked, None)}
    deck_section(locked)
    result = st.session_state.get('ai_commentary')
    if not result or result['hash'] != locked['content_hash']:
        return
    st.markdown('**CFO commentary**')
    if 'error' in result:
        st.error(result['error'])
        return
    if result['issues']:
        st.error('Failed the tick-and-tie after a retry, so download is blocked:\n' + '\n'.join(f'- {i}' for i in result['issues']))
    else:
        retry = ' after one correction' if result['attempts'] > 1 else ''
        st.success(f"Every figure ties to the locked pack{retry}. Drafted by {result['model']}.")
    st.html(result['html'])
    if not result['issues']:
        stem = f"cfo_commentary_{locked['period']}_{locked['basis']}"
        c1, c2, c3 = st.columns(3)
        c1.download_button('CFO commentary (HTML, print to PDF)', html_document(result['html'], stem), stem + '.html', 'text/html')
        c2.download_button('CFO commentary (Markdown)', result['markdown'], stem + '.md', 'text/markdown')
        c3.download_button('CFO commentary (JSON)', json.dumps(result['reply'], indent=2, ensure_ascii=False),
                           stem + '.json', 'application/json')


def deck_section(locked):
    deck = st.session_state.get('ai_deck')
    if not deck or deck['hash'] != locked['content_hash']:
        return
    with st.container(border=True, key='card_deck'):
        st.markdown(f"**🖥️ CFO deck** · {deck['slides']} slides · headlines: {deck['source']}")
        for issue in deck['issues']:
            st.warning(issue)
        st.markdown('\n'.join(f'{i}. **{kicker}**: {headline}' for i, (kicker, headline) in enumerate(deck['outline'], 1)))
        st.download_button('Download CFO deck (PowerPoint)', deck['pptx'],
                           f"cfo_deck_{locked['period']}_{locked['basis']}.pptx",
                           'application/vnd.openxmlformats-officedocument.presentationml.presentation', type='primary')
