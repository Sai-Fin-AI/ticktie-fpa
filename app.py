"""Run with: python -m streamlit run app.py"""
from decimal import Decimal
import hashlib
import streamlit as st
from finance import load_csv, sample_csv, analyze, compare
from evidence import STATUSES, TIMINGS, note_key, make_note, evidence_commentary

st.set_page_config(page_title='FP&A Variance Assistant', page_icon='📊', layout='wide')
st.title('FP&A Variance Assistant')
st.caption('Project 1 · Expense analysis · INR reporting currency · Version 0.2')
st.caption('Commentary uses fixed rules; AI is a later phase.')

with st.sidebar:
    st.header('Analysis settings')
    baseline = st.selectbox('Compare actuals against', ['budget', 'forecast'])
    amount = Decimal(str(st.number_input('Minimum variance (INR)', min_value=0, value=50000, step=10000)))
    pct = Decimal(str(st.number_input('Minimum variance (%)', min_value=0.0, value=5.0, step=1.0)))
    st.caption('Both thresholds must be met. With a zero baseline, only the INR threshold applies.')
    upload = st.file_uploader('Upload a one-month expense CSV', type=['csv'])
    st.download_button('Download sample input', sample_csv(), 'sample_expenses.csv', 'text/csv')

try:
    raw = upload.getvalue() if upload is not None else sample_csv().encode()
    source = ('upload:' + upload.name) if upload is not None else 'demo'
    fingerprint = hashlib.sha256(source.encode() + b'\0' + raw).hexdigest()
    if st.session_state.get('evidence_source') != fingerprint:
        had_notes = bool(st.session_state.get('business_notes'))
        st.session_state.business_notes = {}
        st.session_state.evidence_source = fingerprint
        for key in list(st.session_state):
            if key.startswith('evidence_widget_'):
                del st.session_state[key]
        if had_notes:
            st.warning('The input changed. Previous business explanations were cleared; enter notes for this data.')
    content = raw.decode('utf-8-sig')
    rows = load_csv(content)
except (ValueError, UnicodeError) as error:
    st.error(str(error))
    st.stop()

if upload is not None:
    st.info('Uploaded data is loaded. Review the figures and commentary before use.')
else:
    st.info('Synthetic demo data is loaded. All sample records are fictional.')

country = st.selectbox('Country', ['All countries'] + sorted({r['country'] for r in rows}))
selected = [r for r in rows if country == 'All countries' or r['country'] == country]
result = analyze(selected, baseline, amount, pct)
actual = sum(r['actual'] for r in selected)
plan = sum(r[baseline] for r in selected)
total = compare(actual, plan, amount, pct)
material = [r for r in result if r['material']]
c1, c2, c3, c4 = st.columns(4)
c1.metric('Actual expense · INR', f'{actual:,.0f}')
c2.metric(f'{baseline.title()} · INR', f'{plan:,.0f}')
c3.metric('Net variance · INR', f"{total['variance']:+,.0f}")
c4.metric('Material account movements', len(material))
st.caption(f"Period: {selected[0]['month']} · {len(selected)} validated rows · Net outcome: {total['direction']}. "
           'Individual favorable and unfavorable movements can offset in the total.')

st.subheader('Variance detail')
display = [{'Country': r['country'], 'Account': r['account'], 'Actual INR': float(r['actual']),
            f'{baseline.title()} INR': float(r[baseline]), 'Variance INR': float(r['variance']),
            'Variance %': f"{r['pct']:.1f}%" if r['pct'] is not None else 'N/A',
            'Outcome': r['direction'], 'Material': 'Yes' if r['material'] else 'No'} for r in result]
st.dataframe(display, width='stretch', hide_index=True)

st.subheader('Business explanations')
st.caption('Add one consolidated note per account and comparison. Notes stay in this browser session only. '
           'Changing the input clears them. Download commentary before leaving. Use fictional examples for this demo.')
st.caption('A confirmed note means you reviewed it; the app does not verify the source. '
           'For conflicting accounts, describe both explanations and select Conflicting explanations.')
if material:
    options = {note_key(r, baseline): r for r in material}
    chosen = st.selectbox('Account to explain', list(options),
                          format_func=lambda k: f'{k[1]} | {k[2]} | {k[0]} | vs {baseline}',
                          key='evidence_widget_account_' + baseline)
    target = options[chosen]
    saved = st.session_state.business_notes.get(chosen, {})
    widget_prefix = 'evidence_widget_' + hashlib.sha256(repr(chosen).encode()).hexdigest()
    st.caption(f"Variance to explain: INR {abs(target['variance']):,.2f} ({target['direction'].lower()}). "
               'Enter a positive amount, no larger than this variance. Offsetting drivers are a later feature.')
    with st.form(widget_prefix + '_form', enter_to_submit=False):
        reason = st.text_area('Business explanation', value=saved.get('reason', ''), max_chars=2000, key=widget_prefix + '_reason')
        reference = st.text_input('Supporting reference', value=saved.get('reference', ''), max_chars=300,
                                  help='For example: Fictional freight note, August 2026. References are labels; no documents are fetched.', key=widget_prefix + '_reference')
        covered = st.text_input('Amount explained (INR)', value=str(saved.get('amount', '0')), key=widget_prefix + '_amount')
        status = st.selectbox('Review status', STATUSES, index=STATUSES.index(saved.get('status', STATUSES[0])), key=widget_prefix + '_status')
        timing = st.selectbox('Timing', TIMINGS, index=TIMINGS.index(saved.get('timing', TIMINGS[0])), key=widget_prefix + '_timing')
        action = st.text_area('Proposed next action (optional)', value=saved.get('action', ''), max_chars=1000, key=widget_prefix + '_action')
        submitted = st.form_submit_button('Save explanation')
        removed = st.form_submit_button('Remove saved explanation')
    if removed:
        st.session_state.business_notes.pop(chosen, None)
        for key in list(st.session_state):
            if key.startswith(widget_prefix):
                del st.session_state[key]
        st.rerun()
    if submitted:
        try:
            st.session_state.business_notes[chosen] = make_note(target, baseline, reason, reference, covered, status, timing, action)
            st.success('Explanation saved. Review the updated draft below.')
        except ValueError as error:
            st.error(str(error) + ' The draft still uses the last saved note, if any.')

st.subheader('Draft commentary for review')
summary = (f"{selected[0]['month']} | {country} | Compared with {baseline}\n"
           f"Actual expense: INR {actual:,.2f}; {baseline}: INR {plan:,.2f}; "
           f"net variance: INR {total['variance']:+,.2f} ({total['direction'].lower()}).\n\n")
if material:
    for r in material:
        sentence = evidence_commentary(r, baseline, st.session_state.business_notes.get(note_key(r, baseline)))
        st.text(sentence)
        summary += sentence + '\n\n'
else:
    st.write('No account movements meet the current thresholds.')
    summary += 'No account movements meet the current thresholds.\n'
st.download_button('Download commentary', summary, 'management_commentary.txt', 'text/plain')

with st.expander('Finance rules and limitations'):
    st.write('Variance = actual minus comparison value. Positive expense variances are unfavorable. '
             'Zero baselines have no percentage variance. Missing, duplicate, invalid or negative amounts '
             'stop the analysis. Values are INR with at most two decimal places. '
             'This version accepts one month and expense accounts only; revenue, credits, '
             'FX conversion, automatic driver attribution and AI generation are not included.')
    st.write('The validation cannot detect an entirely omitted account without an expected-account roster. '
             'A CSV must contain the same seven columns as the sample input. '
             'All sample records are fictional. Commentary must be reviewed before use.')
