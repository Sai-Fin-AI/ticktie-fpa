"""Run with: python -m streamlit run app.py"""
from decimal import Decimal
import hashlib
from pathlib import Path

import streamlit as st
import fmt
from finance import (CC, NEGATIVE_NOTE, PRODUCT_LEVELS, SALES, UnmappedGL, UnmappedSKU, accounts_in, aggregate, apply_gl_map,
                     apply_master, apply_products, bridge, bridge_options, check_products, compare, dim_label, line_label,
                     load_cc_master, load_csv, load_gl_map, load_product_master, looks_like_gl_codes, merge_datasets,
                     product_levels, product_margins, review_lines, sample_cc_master_csv, sample_gl_map_csv,
                     sample_product_master_csv, statement)
from ingest import table_csv
from gl_panel import (XLSX, gl_breakdown, gl_codes_view, mapping_workbook, margins_table, product_breakdown,
                      product_workbook)
from evidence import STATUSES, TIMINGS, note_key, make_note, evidence_commentary
from pack import (MONTHS, build_pack, lock_pack, pack_json, pack_excel, pack_markdown, export_notes, import_notes,
                  rollup_views)
from report_html import heatmap_html, pl_table_html
from charts import bridge_chart
from ai_panel import ai_settings, demo_notes_button, hosted, interviewer_section, reset_note_widgets, writer_section
from data_panel import load_inputs, show_reconciliation, source_uploads
from notes_panel import excel_roundtrip, notes_table, show_bulk_errors
import pandas as pd
from style import apply_style, colour_table, hero, money_columns, section

st.set_page_config(page_title='TickTie · FP&A variance review', page_icon='📊', layout='wide')
DEMO_VIDEO = Path(__file__).resolve().parent / 'docs' / 'video' / 'ticktie_demo.mp4'
apply_style()
hero_box = st.container()  # filled once the data is known

with st.sidebar:
    st.header('Analysis settings')
    baseline = st.selectbox('Compare actuals against', ['budget', 'forecast'])
    number_style = st.selectbox('Number style', fmt.STYLES,
                                help='How amounts are written on screen, in the commentary and in the deck. '
                                     'Indian style: 1,23,80,000 · 3.40 L · 1.24 Cr. The figures themselves do not change.')
    fmt.use(number_style)
    fy_start = MONTHS.index(st.selectbox('Financial year starts in', MONTHS, index=3,
                                         help='Used for the full-year view: recurring items are rolled forward to '
                                              'the end of this financial year.')) + 1
    level_box = st.container()
    total_caption = st.empty()
    amount = Decimal(str(st.number_input('Minimum variance (INR)', min_value=0, value=50000, step=10000)))
    pct = Decimal(str(st.number_input('Minimum variance (%)', min_value=0.0, value=5.0, step=1.0)))
    line_box = st.container()
    st.caption('Both thresholds must be met. With a zero baseline, only the INR threshold applies.')
    st.header('Data')
    mode, uploads = source_uploads()
    with st.expander('Master files (optional)', expanded=False):
        master_upload = st.file_uploader('Cost / profit centre master (CSV)', type=['csv'],
                                         help='Columns: cost_center (or profit_center), plus any of country, cc_name, function, '
                                              'owner. List cost centres (costs) and profit centres (sales, cost of sales) '
                                              'together. Checks every centre is known and lists any with no data.')
        st.download_button('Sample centre master', sample_cc_master_csv(), 'sample_centres.csv', 'text/csv')
        gl_upload = st.file_uploader('GL mapping (CSV or Excel)', type=['csv', 'xlsx'],
                                     help='For data posted to GL codes. Columns: gl_code and account (the reporting account '
                                          'it rolls up to), plus optional gl_name and pl_line. Many GL codes can share one account.')
        gl_box = st.container()  # the mapping download, once the data is known
        product_upload = st.file_uploader('Product master (CSV or Excel)', type=['csv', 'xlsx'],
                                          help='For sales by SKU. Columns: sku, plus brand and/or category, optional sku_name. '
                                               'Lets SKU-level sales be reviewed by brand or category. Not needed when the '
                                               'sales file already has a brand column.')
        product_box = st.container()
    notes_upload = st.file_uploader('Restore saved explanations (JSON)', type=['json'],
                                    help='A file from "Download explanations". Notes are re-checked against the current data.')
client = ai_settings()

def gl_template_hint(codes):
    """Data that posts to GL codes but has no mapping: say so and offer a mapping template with every code."""
    st.info('The account column looks like GL codes (for example ' + codes[0] + '). To roll them up to reporting '
            'accounts, download the GL mapping template in the sidebar, fill in the account for each code and upload it.')
    with gl_box:
        st.download_button(f'Download GL mapping template ({len(codes)} codes)', mapping_workbook({}, codes, codes),
                           'gl_mapping.xlsx', XLSX, type='primary',
                           help='Every GL code in your data, with the account left blank to fill in.')


gl_map = gl_info = inputs = product_master = product_info = None
try:
    gl_raw = gl_upload.getvalue() if gl_upload is not None else b''
    gl_map = load_gl_map(table_csv(gl_raw, gl_upload.name)) if gl_raw else None
    product_raw = product_upload.getvalue() if product_upload is not None else b''
    product_master = load_product_master(table_csv(product_raw, product_upload.name)) if product_raw else None
    inputs = load_inputs(mode, uploads, gl_map)
    if inputs['demo']:  # the demo posts to GL codes and SKUs, and comes with its own mapping and product master
        if gl_raw or product_raw:
            st.info('Your GL mapping and product master are used once you upload your own data. The demo uses its samples.')
        gl_map = load_gl_map(sample_gl_map_csv())
        product_master = load_product_master(sample_product_master_csv())
    master_raw = master_upload.getvalue() if master_upload is not None else b''
    fingerprint = hashlib.sha256(b'\0'.join((inputs['fingerprint'].encode(), master_raw, gl_raw, product_raw))).hexdigest()
    if st.session_state.get('evidence_source') != fingerprint:
        had_notes = bool(st.session_state.get('business_notes'))
        st.session_state.business_notes = {}
        st.session_state.evidence_source = fingerprint
        for key in ('locked_pack', 'notes_import', 'interview', 'ai_commentary', 'ai_deck'):
            st.session_state.pop(key, None)
        for key in list(st.session_state):
            if key.startswith('evidence_widget_'):
                del st.session_state[key]
        if had_notes:
            st.warning('The input changed. Previous business explanations were cleared; enter notes for this data.')
    dataset, checks = load_csv(inputs['text']), []
    if inputs.get('pl_text'):  # sales and other P&L lines: accounts the name does not place are read as sales
        dataset = merge_datasets(dataset, load_csv(inputs['pl_text'], default_line=SALES))
    for code, details in inputs['attrs'].items():  # cost centre details found in a separate actuals file
        if code in dataset.attrs:
            dataset.attrs[code].update(details)
    if gl_map is not None:
        dataset, gl_info = apply_gl_map(dataset, gl_map)
    if product_master is not None:
        dataset, product_info = apply_products(dataset, product_master)
    else:
        check_products(dataset)
    if master_raw:
        dataset, checks = apply_master(dataset, load_cc_master(master_raw.decode('utf-8-sig')))
    elif inputs['demo']:
        dataset, checks = apply_master(dataset, load_cc_master(sample_cc_master_csv()))
    if baseline == 'forecast' and not inputs['has_forecast']:
        raise ValueError('No forecast file was uploaded. Compare against budget, or add a forecast file.')
except UnmappedGL as error:
    st.error(str(error))
    st.download_button('Download GL mapping with the new codes', mapping_workbook(gl_map, error.present, error.codes),
                       'gl_mapping.xlsx', XLSX, type='primary',
                       help='Your mapping plus the new codes, highlighted, with the account left blank to fill in.')
    st.stop()
except UnmappedSKU as error:
    st.error(str(error))
    st.download_button('Download product master with the new SKUs', product_workbook(product_master, error.present, error.codes),
                       'product_master.xlsx', XLSX, type='primary',
                       help='Your product master plus the new SKUs, highlighted, with brand and category left blank to fill in.')
    st.stop()
except (ValueError, UnicodeError) as error:
    st.error(str(error))
    if inputs and gl_map is None:  # a file that posts to GL codes may need a GL mapping to make sense
        codes = accounts_in(inputs['text'] + '\n'.join((inputs.get('pl_text') or '').splitlines()[1:]))
        if looks_like_gl_codes(codes):
            gl_template_hint(codes)
    st.stop()

if gl_info:
    with gl_box:
        st.download_button('Download GL mapping master', mapping_workbook(gl_map, [r['gl_code'] for r in dataset]),
                           'gl_mapping.xlsx', XLSX, help='The mapping in use, as Excel: edit it and upload it again.')
elif looks_like_gl_codes({r['account'] for r in dataset}):
    gl_template_hint(sorted({r['account'] for r in dataset}))
else:
    with gl_box:
        st.download_button('Download sample GL mapping', sample_gl_map_csv(), 'sample_gl_mapping.csv', 'text/csv')
with product_box:
    skus = sorted({r['sku'] for r in dataset if 'sku' in r.get('dims', ())})
    if product_info:
        st.download_button('Download product master', product_workbook(product_master, skus), 'product_master.xlsx', XLSX,
                           help='The product master in use, as Excel: edit it and upload it again.')
    elif skus and 'brand' not in dataset.dims:
        st.download_button(f'Download product master template ({len(skus)} SKUs)', product_workbook({}, skus, skus),
                           'product_master.xlsx', XLSX, type='primary', help='Every SKU in your data, brand left blank to fill in.')
    else:
        st.download_button('Sample product master', sample_product_master_csv(), 'sample_products.csv', 'text/csv')

org_dims = [d for d in dataset.dims if d not in PRODUCT_LEVELS]
levels = product_levels(dataset.dims)  # coarse to fine, as available
with level_box:
    chosen_dims = st.multiselect('Analyse by', org_dims, default=org_dims, format_func=dim_label,
                                 help='Country and account are always included. Remove a level to add it up; '
                                      'explanations are given at the level you choose. Costs sit on cost centres, '
                                      'sales and cost of sales on profit centres.') if org_dims else []
    product_level = None
    if levels:
        choices = levels[::-1] + ['total']  # finest first; 'total' adds products up to the account
        product_level = st.selectbox('Sales and cost of sales by', choices,
                                     index=choices.index('brand') if 'brand' in choices else 0,
                                     format_func=lambda d: 'No product split' if d == 'total' else dim_label(d),
                                     help='SKU is the finest level; brand and category add SKUs up. Explanations are '
                                          'given at this level, with the finer items shown inside each line. Lines '
                                          'without a product (tax, corporate costs) are not affected.')
        product_level = None if product_level == 'total' else product_level
grain = [d for d in org_dims if d in chosen_dims] + ([product_level] if product_level else [])
finer = levels[levels.index(product_level) + 1:] if product_level else []  # for the drill-down
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
if hosted():
    st.caption('🌐 Online demo: upload synthetic or public data only, never company data. Files are read in memory for '
               'your session and are not stored. Run the app on your own computer for private data.')
if DEMO_VIDEO.exists():
    with st.expander('▶ Watch the 2-minute demo', expanded=False):
        st.video(str(DEMO_VIDEO))
        st.caption('A guided tour of this demo: the month at a glance, what the totals hide, owners explaining, the '
                   'locked pack, code checking the AI, and the CFO deck. Recorded automatically by scripts/make_demo_video.py.')
if gl_info:
    unused = len(gl_info['unused'])
    st.caption(f"🔗 GL mapping: {gl_info['gl_codes']} GL codes roll up into {gl_info['accounts']} accounts."
               + (f' {unused} mapped GL code(s) have no rows this month.' if unused else '')
               + ' See them under Variance detail → GL codes, and in each line you explain.')
for check in checks:
    st.warning(check)
show_reconciliation(inputs['report'], rows)
if 'flash' in st.session_state:
    st.success(st.session_state.pop('flash'))

country = st.selectbox('Country', ['All countries'] + sorted({r['country'] for r in rows}))
in_view = lambda r: country == 'All countries' or r['country'] == country
selected = [r for r in rows if in_view(r)]
result = [r for r in review_lines(rows, baseline, line_thresholds, total_thresholds) if in_view(r)]
pl = statement(selected, baseline)  # [] for an expense-only review
subtotal = {e['label']: e for e in pl}
if pl:  # the headline total of a P&L review is net profit; more is better
    actual, plan = subtotal['Net profit']['actual'], subtotal['Net profit']['baseline']
    total = compare(actual, plan, *(total_thresholds or line_thresholds), True)
else:
    actual = sum(r['actual'] for r in selected)
    plan = sum(r[baseline] for r in selected)
    total = compare(actual, plan, *(total_thresholds or line_thresholds))
total_name = 'Net profit' if pl else 'Expenses'
material = [r for r in result if r['material']]
current_pack = build_pack(rows, baseline, *line_thresholds, st.session_state.business_notes, total_thresholds, fy_start,
                          inputs.get('data_log'))
with hero_box:
    hero('TickTie · FP&A variance review',
         'Month-end variance review a CFO can sign off. Figures come from fixed rules, causes from reviewers; AI drafts '
         'wording only, and every AI figure is tick-and-tied to the locked pack.',
         [f"📅 {rows[0]['month']}", f'⚖️ Actual vs {baseline}', f'🧭 {level_name}',
          '🧪 Demo data' if inputs['demo'] else '📂 Uploaded data', '💱 INR', 'v1.0'])
in_scope = [l for l in current_pack['lines'] if l['material'] and in_view(l)]
gap = sum(abs(Decimal(l['variance'])) for l in in_scope)
explained = sum(Decimal(l['explained']) for l in in_scope)
c1, c2, c3, c4, c5 = st.columns(5)
amount = lambda v: ('−' if v < 0 else '') + fmt.group(v)
if pl:
    for col, name in ((c1, 'Sales'), (c2, 'EBITDA'), (c3, 'Net profit')):
        e, margin = subtotal[name], subtotal.get(f'{name} %')
        col.metric(f'{name} · INR', amount(e['actual']), delta=f"{fmt.signed(e['impact'])} vs {baseline}" if e['impact'] else None,
                   delta_color='normal',
                   help=(f"{margin['actual_pct']:.1f}% margin vs {margin['baseline_pct']:.1f}% in the {baseline}. " if margin else '')
                        + 'Green arrow up = helps profit; red arrow down = hurts it.')
else:
    c1.metric('Actual expense · INR', fmt.balance(actual))
    c2.metric(f'{baseline.title()} · INR', fmt.balance(plan))
    c3.metric('Net variance · INR', fmt.signed(total['variance']),
              delta=f"{total['pct']:+.1f}% vs {baseline}" if total['pct'] is not None else None, delta_color='inverse',
              help='Red arrow up = spend above plan (unfavorable); green arrow down = below plan.')
c4.metric('Lines to explain', len(material))
c5.metric('Explained (confirmed)', f'{explained / gap * 100:.0f}%' if gap else '–',
          help='Confirmed explanations as a share of the lines that need one.')
MARGIN_NAMES = {'Gross margin %': 'Gross margin', 'EBITDA %': 'EBITDA margin', 'Net profit %': 'Net margin'}
margins = ' · '.join(f"{MARGIN_NAMES.get(m['label'], m['label'])} {m['actual_pct']:.1f}% ({m['pts']:+.1f} pts)"
                     for m in pl if m['kind'] == 'margin')
st.caption(f"Period: {selected[0]['month']} · {len(selected)} lines analysed by {level_name} · "
           + (f'{margins} · ' if margins else '')
           + f"{total_name}: {total['direction'].lower()}. Individual favorable and unfavorable movements can offset in the total.")
outlook = current_pack['outlook']
if Decimal(outlook['fy_effect']):
    st.caption(f"📅 **Full-year view ({outlook['fy']}, {outlook['months_left']} months left):** confirmed explanations point to "
               f"INR {fmt.signed(outlook['fy_effect'])} vs plan, if recurring items continue at this month's amount and "
               'timing differences reverse. Unexplained variance is not projected. All countries.')

if pl:
    with st.container(border=True, key='card_pl'):
        section('📒', 'Profit and loss', f'Sales to net profit, actual vs {baseline}. Profit impact: + helps profit '
                '(sales above plan, costs below), − hurts it.' + ('' if country == 'All countries' else f' {country} only.'))
        left, right = st.columns([11, 10])
        with left:
            st.html(pl_table_html(pl, baseline))
        with right:
            options = bridge_options(selected)
            measure = st.selectbox('Waterfall for', options, key='bridge_measure',
                                   help='Sales and Costs walk in their own terms. Gross margin, EBITDA and Net profit '
                                        'walk in profit terms: every step is its effect on that margin. '
                                        'The CFO deck uses the same choice.')
            st.altair_chart(bridge_chart(bridge(selected, baseline, measure), baseline), width='stretch')
            st.caption('The axis does not start at zero, so the steps are visible. Hover a bar for its amount.')

product_rows = product_margins(selected, baseline, product_level) if pl and product_level else []
if product_rows:
    with st.container(border=True, key='card_products'):
        name = dim_label(product_level)
        section('🏷️', f'Sales and gross margin by {name.lower()}',
                f'Sales, cost of sales and gross margin per {name.lower()}, actual vs {baseline}. Mix shows here: a calm '
                'country total can hide one brand gaining and another losing.' + ('' if country == 'All countries' else f' {country} only.'))
        margins_table(product_rows, name, baseline)

if grain:
    with st.container(border=True, key='card_1'):
        section('🗺️', 'Where the variance sits', 'Each row is one cost or profit centre' + (f' (and {dim_label(product_level).lower()} for sales)' if product_level else '') + ', each column an account. Outlined cells need an explanation.')
        st.html(heatmap_html(current_pack, None if country == 'All countries' else country))

with st.container(border=True, key='card_2'):
    section('📋', 'Variance detail')
    views = rollup_views(grain, current_pack['lines'])
    choices = ['Lines'] + (['GL codes'] if gl_info else []) + [label for _, label, _ in views]
    view = st.radio('View by', choices, horizontal=True) if len(choices) > 1 else 'Lines'
    has_owner = any(r.get('owner') for r in result)
    any_negative = any(r['negative'] for r in result)
    if view == 'GL codes':
        gl_codes_view(dataset, grain, baseline, in_view)
    elif view == 'Lines':
        display = [{'Country': r['country'], **{dim_label(d): r.get(d, '') for d in grain}, 'Account': r['account'],
                    **({'Owner': r.get('owner', '')} if has_owner else {}),
                    'Actual INR': float(r['actual']), f'{baseline.title()} INR': float(r[baseline]),
                    'Variance INR': float(r['variance']),
                    'Variance %': f"{r['pct']:.1f}%" if r['pct'] is not None else 'N/A', 'Outcome': r['direction'],
                    'Material': f"Yes ({r['scope'].lower()})" if r['scope'] in ('Account total', 'Negative balance')
                                else 'Yes' if r['material'] else 'No',
                    **({'Flag': '⚠️ Negative balance' if r['negative'] else ''} if any_negative else {})}
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
    if view != 'GL codes':
        frame = pd.DataFrame(display)
        st.dataframe(colour_table(frame) if len(frame) else frame, width='stretch', hide_index=True,
                     column_config=money_columns(frame))

section('✍️', 'Business explanations')
st.caption(f'Add one consolidated note per line ({level_name}) and comparison. Notes stay in this browser session only. '
           'Changing the input clears them. Download your explanations before leaving. Use fictional examples for this demo.')
st.caption('A confirmed note means you reviewed it; the app does not verify the source. '
           'For conflicting accounts, describe both explanations and select Conflicting explanations.')
demo_notes_button(rows)
if material:
    options = {note_key(r, baseline): r for r in material}
    all_material = [r for r in review_lines(rows, baseline, line_thresholds, total_thresholds) if r['material']]
    every = {note_key(r, baseline): r for r in all_material}
    show_bulk_errors()
    one, table, excel = st.tabs(['📝 One line at a time', f'📋 All lines ({len(every)}, all countries)', '📤 Excel round-trip'])
    with table:
        notes_table(every, baseline)
    with excel:
        excel_roundtrip(every, baseline, rows[0]['month'])
    with one:
        chosen = st.selectbox('Account to explain', list(options),
                              format_func=lambda k: f"{line_label(options[k])} | {options[k]['month']} | vs {baseline}"
                                                    + (' · part of a material account total' if options[k]['scope'] == 'Account total' else '')
                                                    + (' · ⚠️ negative balance' if options[k]['negative'] else ''),
                              key='evidence_widget_account_' + baseline)
        target = options[chosen]
        saved = st.session_state.business_notes.get(chosen, {})
        widget_prefix = 'evidence_widget_' + hashlib.sha256(repr(chosen).encode()).hexdigest()
        who = f" Owner: {target['owner']}." if target.get('owner') else ''
        st.caption(f"Variance to explain: INR {abs(target['variance']):,.2f} ({target['direction'].lower()}).{who} "
                   'Enter a positive amount, no larger than this variance.')
        if target['negative']:
            which = 'actual' if target['actual'] < 0 else baseline
            st.warning(f"⚠️ Negative balance: the {which} is INR {target[which]:,.2f}. This is {NEGATIVE_NOTE}. "
                       'It needs an explanation whatever its size, and cannot be marked Recurring.')
        if finer and product_level in target.get('dims', ()):  # a brand line: show its SKUs
            product_breakdown(dataset, target, baseline, finer[-1])
        if gl_info:
            gl_breakdown(dataset, target, baseline)
        # Seed the widgets from the saved note through session state: a keyed widget keeps its old browser value
        # and ignores a changed `value=`, so loaded or restored notes would otherwise show an empty form.
        defaults = {'reason': saved.get('reason', ''), 'reference': saved.get('reference', ''),
                    'amount': str(saved.get('amount', '0')), 'status': saved.get('status', STATUSES[0]),
                    'timing': saved.get('timing', TIMINGS[0]), 'action': saved.get('action', '')}
        for field, value in defaults.items():
            st.session_state.setdefault(f'{widget_prefix}_{field}', value)
        with st.form(widget_prefix + '_form', enter_to_submit=False):
            reason = st.text_area('Business explanation', max_chars=2000, key=widget_prefix + '_reason')
            reference = st.text_input('Supporting reference', max_chars=300,
                                      help='For example: Fictional freight note, August 2026. References are labels; no documents are fetched.', key=widget_prefix + '_reference')
            covered = st.text_input('Amount explained (INR)', key=widget_prefix + '_amount')
            status = st.selectbox('Review status', STATUSES, key=widget_prefix + '_status')
            timing = st.selectbox('Timing', TIMINGS, key=widget_prefix + '_timing')
            action = st.text_area('Proposed next action (optional)', max_chars=1000, key=widget_prefix + '_action')
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

section('🧾', 'Draft commentary for review')
summary = (f"{selected[0]['month']} | {country} | Compared with {baseline}\n"
           f"Actual {'net profit' if pl else 'expense'}: INR {actual:,.2f}; {baseline}: INR {plan:,.2f}; "
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

section('🔒', 'Review pack')
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
    writer_section(locked, client, number_style)
else:
    st.write('Not locked yet. Save explanations first, then lock the pack. '
             'The Excel pack, CFO commentary and CFO PowerPoint deck appear here once locked.')

with st.expander('Finance rules and limitations'):
    st.write('Variance = actual minus comparison value. Expense above plan is unfavorable; sales above plan is favorable. '
             'Percentages divide by the size of the comparison value; zero baselines have no percentage. Missing, '
             'duplicate or invalid amounts stop the analysis. Values are INR with at most two decimal places. '
             'Negative amounts (credit notes, reversals) are accepted. A line that nets below zero is flagged as a '
             'negative balance, always needs an explanation whatever its size, and cannot be marked Recurring, so a '
             'reversal is never rolled forward into the full-year view. '
             'This version accepts one month; FX conversion and automatic driver attribution are not included. '
             'AI never calculates or infers causes; it rewords reviewer explanations and its figures are checked.')
    st.write('Lines are analysed at the level chosen under "Analyse by"; removing a level adds its rows up. '
             'When lines are finer than country × account, an account total that is material while no single line is '
             'brings all its moving lines into scope. Gross movement shows what offsetting lines hide inside a net figure. '
             'A cost centre master detects unknown cost centres and lists cost centres with no data; without a master, '
             'an entirely omitted account cannot be detected. All sample records are fictional. Commentary must be reviewed before use.')
    st.write('With a GL mapping, the account column of the data holds GL codes and each GL code rolls up to its reporting '
             'account; the analysis runs on accounts and the GL codes stay visible for drill-down. A GL code missing from '
             'the mapping stops the analysis, so nothing is dropped silently. A value that already is a reporting account '
             '(for example a budget kept at account level) is kept as it is. A credit GL code may be negative.')
