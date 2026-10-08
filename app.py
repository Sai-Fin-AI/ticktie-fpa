"""Run with: python -m streamlit run app.py"""
from decimal import Decimal
import hashlib
import streamlit as st
from finance import (CC, aggregate, apply_master, compare, dim_label, line_label, load_cc_master, load_csv,
                     review_lines, sample_cc_master_csv)
from evidence import STATUSES, TIMINGS, note_key, make_note, evidence_commentary
from pack import (build_pack, lock_pack, pack_json, pack_excel, pack_markdown, export_notes, import_notes,
                  rollup_views)
from report_html import heatmap_html
from ai_panel import ai_settings, demo_notes_button, interviewer_section, reset_note_widgets, writer_section
from data_panel import load_inputs, show_reconciliation, source_uploads

st.set_page_config(page_title='FP&A Variance Assistant', page_icon='📊', layout='wide')
st.title('FP&A Variance Assistant')
st.caption('Project 1 · Expense analysis · INR reporting currency · Version 0.5')
st.caption('Figures come from fixed rules. AI drafts wording only, and every AI figure is checked against the locked pack.')

with st.sidebar:
    st.header('Analysis settings')
    baseline = st.selectbox('Compare actuals against', ['budget', 'forecast'])
    level_box = st.container()
    total_caption = st.empty()
    amount = Decimal(str(st.number_input('Minimum variance (INR)', min_value=0, value=50000, step=10000)))
    pct = Decimal(str(st.number_input('Minimum variance (%)', min_value=0.0, value=5.0, step=1.0)))
    line_box = st.container()
    st.caption('Both thresholds must be met. With a zero baseline, only the INR threshold applies.')
    st.header('Data')
    mode, uploads = source_uploads()
    master_upload = st.file_uploader('Cost centre master (optional CSV)', type=['csv'],
                                     help='Columns: cost_center, plus any of country, cc_name, function, owner. '
                                          'Checks every cost centre is known and lists any with no data.')
    st.download_button('Download sample cost centre master', sample_cc_master_csv(), 'sample_cost_centres.csv', 'text/csv')
    notes_upload = st.file_uploader('Restore saved explanations (JSON)', type=['json'],
                                    help='A file from "Download explanations". Notes are re-checked against the current data.')
client = ai_settings()

try:
    inputs = load_inputs(mode, uploads)
    master_raw = master_upload.getvalue() if master_upload is not None else b''
    fingerprint = hashlib.sha256(inputs['fingerprint'].encode() + b'\0' + master_raw).hexdigest()
    if st.session_state.get('evidence_source') != fingerprint:
        had_notes = bool(st.session_state.get('business_notes'))
        st.session_state.business_notes = {}
        st.session_state.evidence_source = fingerprint
        for key in ('locked_pack', 'notes_import', 'interview', 'ai_commentary'):
            st.session_state.pop(key, None)
        for key in list(st.session_state):
            if key.startswith('evidence_widget_'):
                del st.session_state[key]
        if had_notes:
            st.warning('The input changed. Previous business explanations were cleared; enter notes for this data.')
    dataset, checks = load_csv(inputs['text']), []
    for code, details in inputs['attrs'].items():  # cost centre details found in a separate actuals file
        if code in dataset.attrs:
            dataset.attrs[code].update(details)
    if master_raw:
        dataset, checks = apply_master(dataset, load_cc_master(master_raw.decode('utf-8-sig')))
    elif inputs['demo']:
        dataset, checks = apply_master(dataset, load_cc_master(sample_cc_master_csv()))
    if baseline == 'forecast' and not inputs['has_forecast']:
        raise ValueError('No forecast file was uploaded. Compare against budget, or add a forecast file.')
except (ValueError, UnicodeError) as error:
    st.error(str(error))
    st.stop()

with level_box:
    chosen_dims = st.multiselect('Analyse by', list(dataset.dims), default=list(dataset.dims), format_func=dim_label,
                                 help='Country and account are always included. Remove a level to add it up; '
                                      'explanations are given at the level you choose.') if dataset.dims else []
grain = [d for d in dataset.dims if d in chosen_dims]
level_name = ' × '.join(['Country'] + [dim_label(d) for d in grain] + ['Account'])
rows = aggregate(dataset, grain)
if grain:
    total_caption.markdown('**Account totals** (country × account)')
    with line_box:
        st.markdown(f'**Each line** ({level_name})')
        line_amount = Decimal(str(st.number_input('Line minimum variance (INR)', min_value=0, value=25000, step=5000)))
        line_pct = Decimal(str(st.number_input('Line minimum variance (%)', min_value=0.0, value=10.0, step=1.0)))
        st.caption('A line is explained if it meets the line thresholds, or if its account total is material '
                   'while no single line is (spread variance).')
    line_thresholds, total_thresholds = (line_amount, line_pct), (amount, pct)
else:
    total_caption.markdown('**Each line** (country × account)')
    line_thresholds, total_thresholds = (amount, pct), None

if notes_upload is not None:
    notes_id = hashlib.sha256(notes_upload.getvalue()).hexdigest()
    if st.session_state.get('notes_import') != notes_id:
        st.session_state.notes_import = notes_id
        try:
            restored, skipped = import_notes(notes_upload.getvalue().decode('utf-8-sig'), rows)
            st.session_state.business_notes.update(restored)
            reset_note_widgets()
            st.success(f'Restored {len(restored)} explanation(s).' +
                       (f' {skipped} skipped because they do not match the current figures or level.' if skipped else ''))
        except (ValueError, UnicodeError) as error:
            st.error(str(error))

if not inputs['demo']:
    st.info('Uploaded data is loaded. Review the figures and commentary before use.')
else:
    st.info('Synthetic demo data is loaded. All sample records, cost centres and owners are fictional.')
for check in checks:
    st.warning(check)
show_reconciliation(inputs['report'], rows)
if 'flash' in st.session_state:
    st.success(st.session_state.pop('flash'))

country = st.selectbox('Country', ['All countries'] + sorted({r['country'] for r in rows}))
in_view = lambda r: country == 'All countries' or r['country'] == country
selected = [r for r in rows if in_view(r)]
result = [r for r in review_lines(rows, baseline, line_thresholds, total_thresholds) if in_view(r)]
actual = sum(r['actual'] for r in selected)
plan = sum(r[baseline] for r in selected)
total = compare(actual, plan, *(total_thresholds or line_thresholds))
material = [r for r in result if r['material']]
current_pack = build_pack(rows, baseline, *line_thresholds, st.session_state.business_notes, total_thresholds)
c1, c2, c3, c4 = st.columns(4)
c1.metric('Actual expense · INR', f'{actual:,.0f}')
c2.metric(f'{baseline.title()} · INR', f'{plan:,.0f}')
c3.metric('Net variance · INR', f"{total['variance']:+,.0f}")
c4.metric('Lines to explain', len(material))
st.caption(f"Period: {selected[0]['month']} · {len(selected)} lines analysed by {level_name} · "
           f"Net outcome: {total['direction']}. Individual favorable and unfavorable movements can offset in the total.")

if grain:
    st.subheader('Where the variance sits')
    st.html(heatmap_html(current_pack, None if country == 'All countries' else country))

st.subheader('Variance detail')
views = rollup_views(grain, current_pack['lines'])
view = st.radio('View by', ['Lines'] + [label for _, label, _ in views], horizontal=True) if views else 'Lines'
has_owner = any(r.get('owner') for r in result)
if view == 'Lines':
    display = [{'Country': r['country'], **{dim_label(d): r[d] for d in grain}, 'Account': r['account'],
                **({'Owner': r.get('owner', '')} if has_owner else {}),
                'Actual INR': float(r['actual']), f'{baseline.title()} INR': float(r[baseline]),
                'Variance INR': float(r['variance']),
                'Variance %': f"{r['pct']:.1f}%" if r['pct'] is not None else 'N/A', 'Outcome': r['direction'],
                'Material': ('Yes (account total)' if r['scope'] == 'Account total' else 'Yes') if r['material'] else 'No'}
               for r in result]
else:
    name = next(v for v, label, _ in views if label == view)
    country_scoped = name in ('account', CC)
    display = [{view: e['label'], 'Actual INR': float(e['actual']), f'{baseline.title()} INR': float(e['baseline']),
                'Net variance INR': float(e['net']), 'Gross movement INR': float(e['gross']),
                'Hidden by offsets INR': float(e['hidden']), 'Lines to explain': e['to_explain'],
                'Explained INR': float(e['explained']), 'Unexplained INR': float(e['unexplained']),
                'Flags': ', '.join(('⇅ ' if f.startswith('Offset') else 'Σ ') + f for f in e['flags'])}
               for e in current_pack['rollups'][name]
               if not country_scoped or country == 'All countries' or e['label'].startswith(country + ' | ')]
    st.caption('Gross movement adds up the size of every line, so it shows what offsetting lines hide inside the net figure.'
               + ('' if country_scoped else ' This view covers all countries.'))
st.dataframe(display, width='stretch', hide_index=True)

st.subheader('Business explanations')
st.caption(f'Add one consolidated note per line ({level_name}) and comparison. Notes stay in this browser session only. '
           'Changing the input clears them. Download your explanations before leaving. Use fictional examples for this demo.')
st.caption('A confirmed note means you reviewed it; the app does not verify the source. '
           'For conflicting accounts, describe both explanations and select Conflicting explanations.')
demo_notes_button(rows)
if material:
    options = {note_key(r, baseline): r for r in material}
    chosen = st.selectbox('Account to explain', list(options),
                          format_func=lambda k: f"{line_label(options[k])} | {options[k]['month']} | vs {baseline}"
                                                + (' · part of a material account total' if options[k]['scope'] == 'Account total' else ''),
                          key='evidence_widget_account_' + baseline)
    target = options[chosen]
    saved = st.session_state.business_notes.get(chosen, {})
    widget_prefix = 'evidence_widget_' + hashlib.sha256(repr(chosen).encode()).hexdigest()
    who = f" Owner: {target['owner']}." if target.get('owner') else ''
    st.caption(f"Variance to explain: INR {abs(target['variance']):,.2f} ({target['direction'].lower()}).{who} "
               'Enter a positive amount, no larger than this variance.')
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
        except ValueError as error:
            st.error(str(error) + ' The draft still uses the last saved note, if any.')
        else:
            # Rerun so the summaries above include the new note.
            st.session_state.flash = 'Explanation saved. Review the updated draft below.'
            st.rerun()

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
if st.session_state.business_notes:
    st.download_button('Download explanations (JSON)', export_notes(st.session_state.business_notes),
                       f"explanations_{selected[0]['month']}.json", 'application/json',
                       help='Back up your notes. Restore them later from the sidebar.')

interviewer_section(current_pack, client)

st.subheader('Review pack')
st.caption(f'Locking freezes the figures and saved explanations for all countries, compared with {baseline} and '
           f'analysed by {level_name}, into one fingerprinted pack. Exports and AI commentary use only the locked pack.')
if st.button('Lock review pack'):
    st.session_state.locked_pack = lock_pack(current_pack)
locked = st.session_state.get('locked_pack')
if locked and locked['content_hash'] != current_pack['content_hash']:
    st.warning('Figures, settings or explanations changed since the pack was locked. Lock again to export the current state.')
elif locked:
    s = locked['summary']
    st.success(f"Pack locked {locked['locked_at']} · fingerprint {locked['content_hash'][:12]} · "
               f"{s['material']} material movements · {s['open_items']} open items · "
               f"INR {Decimal(s['unexplained']):,.2f} unexplained")
    stem = f"review_pack_{locked['period']}_{locked['basis']}"
    d1, d2, d3 = st.columns(3)
    d1.download_button('Excel workbook', pack_excel(locked), stem + '.xlsx',
                       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    d2.download_button('Commentary (Markdown)', pack_markdown(locked), stem + '.md', 'text/markdown')
    d3.download_button('Pack data (JSON)', pack_json(locked), stem + '.json', 'application/json')
    writer_section(locked, client)
else:
    st.write('Not locked yet. Save explanations first, then lock the pack.')

with st.expander('Finance rules and limitations'):
    st.write('Variance = actual minus comparison value. Positive expense variances are unfavorable. '
             'Zero baselines have no percentage variance. Missing, duplicate, invalid or negative amounts '
             'stop the analysis. Values are INR with at most two decimal places. '
             'This version accepts one month and expense accounts only; revenue, credits, '
             'FX conversion and automatic driver attribution are not included. '
             'AI never calculates or infers causes; it rewords reviewer explanations and its figures are checked.')
    st.write('Lines are analysed at the level chosen under "Analyse by"; removing a level adds its rows up. '
             'When lines are finer than country × account, an account total that is material while no single line is '
             'brings all its moving lines into scope. Gross movement shows what offsetting lines hide inside a net figure. '
             'A cost centre master detects unknown cost centres and lists cost centres with no data; without a master, '
             'an entirely omitted account cannot be detected. All sample records are fictional. Commentary must be reviewed before use.')
