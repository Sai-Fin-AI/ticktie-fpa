"""Streamlit screens for the AI agents, kept apart from app.py so the finance screens stay readable."""
import json
from pathlib import Path

import streamlit as st

from agents.interviewer import interview
from agents.writer import write_commentary
from llm import DEMO, PROVIDERS, AIError, Client
from pack import import_notes

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
                                                'conflicting and missing explanations. Budget comparison only.'):
        restored, _ = import_notes(DEMO_NOTES.read_text(encoding='utf-8'), rows)
        if restored:
            st.session_state.business_notes.update(restored)
            reset_note_widgets()
            st.success(f'Loaded {len(restored)} demo explanations for the budget comparison.')
        else:
            st.info('Demo explanations only match the sample data. Clear the uploaded file to use them.')


def interviewer_section(pack, client):
    st.subheader('AI interviewer')
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
    open_lines = [f for f in result['findings'] if f['questions']]
    if not open_lines:
        st.success('No follow-up questions. The explanations are ready to lock.')
    for finding in open_lines:
        st.markdown(f"**{finding['country']} | {finding['account']}** · {finding['movement']} · {QUALITY[finding['quality']]}\n"
                    + '\n'.join(f'- {q}' for q in finding['questions']))


def writer_section(locked, client):
    st.subheader('AI CFO commentary')
    st.caption('Drafts commentary from the locked pack only. Lines without explanations get fixed wording; '
               'open items and actions come straight from the pack. Drafts that fail the tick-and-tie cannot be downloaded.')
    if st.button('Draft CFO commentary'):
        try:
            with st.spinner('Drafting and checking. A local model can take a minute or two.'):
                result = write_commentary(locked, client)
            st.session_state.ai_commentary = {'hash': locked['content_hash'], **result}
        except (AIError, ValueError) as error:
            st.error(str(error))
    result = st.session_state.get('ai_commentary')
    if not result or result['hash'] != locked['content_hash']:
        return
    if result['issues']:
        st.error('Failed the tick-and-tie after a retry, so download is blocked:\n' + '\n'.join(f'- {i}' for i in result['issues']))
    else:
        retry = ' after one correction' if result['attempts'] > 1 else ''
        st.success(f"Every figure ties to the locked pack{retry}. Drafted by {result['model']}.")
    with st.container(border=True):
        # Smaller headings on screen; the download keeps document-sized ones.
        st.markdown(result['markdown'].replace('\n## ', '\n##### ').replace('# ', '#### ', 1))
    if not result['issues']:
        stem = f"cfo_commentary_{locked['period']}_{locked['basis']}"
        c1, c2 = st.columns(2)
        c1.download_button('CFO commentary (Markdown)', result['markdown'], stem + '.md', 'text/markdown')
        c2.download_button('CFO commentary (JSON)', json.dumps(result['reply'], indent=2, ensure_ascii=False),
                           stem + '.json', 'application/json')
