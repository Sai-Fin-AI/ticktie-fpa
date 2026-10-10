"""Data loading screens: data source, uploads, column mapping and the reconciliation of control totals."""
import hashlib
from decimal import Decimal

import streamlit as st

import validate
from finance import gl_resolver, sample_csv, sample_gl_csv, sample_gl_pl_csv, sample_pl_csv
from ingest import (ROLE_LABELS, ROLES, check_roles, combined_csv, guess_roles, read_table, sample_actuals_csv,
                    sample_budget_xlsx, sample_forecast_csv, separate_csv)

COMBINED, SEPARATE = 'One combined file', 'Separate files'
TYPES = ['csv', 'xlsx']
FILES = (('actuals', 'Actuals file (GL export)'), ('budget', 'Budget file'), ('forecast', 'Forecast file (optional)'))
PL_LABEL = 'Sales and other P&L lines (optional, CSV or Excel)'


def source_uploads():
    """Sidebar: choose how data arrives and collect the uploads."""
    mode = st.radio('Data source', [COMBINED, SEPARATE],
                    help='Combined: one file with actual, budget and forecast columns. Separate: actuals from the '
                         'ledger, budget from the planning file and an optional forecast, matched line by line.')
    if mode == COMBINED:
        uploads = {'combined': st.file_uploader('Upload a one-month expense file (CSV or Excel)', type=TYPES,
                                                help='Any column order or names: you confirm what each column means.')}
        st.download_button('Download sample input', sample_csv(), 'sample_expenses.csv', 'text/csv')
        st.download_button('Sample input by GL code', sample_gl_csv(), 'sample_expenses_by_gl.csv', 'text/csv',
                           help='The same data posted to GL codes (several per account). Use it with the sample GL mapping.')
    else:
        uploads = {kind: st.file_uploader(label, type=TYPES, key='upload_' + kind) for kind, label in FILES}
        st.caption('Samples in the shape systems export them:')
        st.download_button('Sample actuals (GL export)', sample_actuals_csv(), 'sample_actuals.csv', 'text/csv')
        st.download_button('Sample budget (full-year Excel)', sample_budget_xlsx(), 'sample_budget.xlsx',
                           'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        st.download_button('Sample forecast', sample_forecast_csv(), 'sample_forecast.csv', 'text/csv')
    uploads['pl'] = st.file_uploader(PL_LABEL, type=TYPES, key='upload_pl',
                                     help='Optional. Sales, cost of sales, depreciation, interest and tax, with actual, '
                                          'budget and forecast columns. With it, the review covers the full P&L from sales '
                                          'to net profit. Its levels may differ from the expense file: sales usually come by '
                                          'profit centre and SKU (or brand, or category); leave the product blank on lines '
                                          'that have none, such as tax.')
    st.download_button('Sample sales and P&L lines', sample_pl_csv(), 'sample_sales_and_pl.csv', 'text/csv')
    return mode, {k: v for k, v in uploads.items() if v is not None}


def _map_columns(kind, upload):
    """Dropdown per column, pre-filled with a guess. Returns (headers, rows, roles, name) or stops on problems."""
    data = upload.getvalue()
    headers, rows = read_table(data, upload.name)
    file_id = hashlib.sha256(data).hexdigest()[:10]
    guesses = guess_roles(headers, kind)
    options = list(ROLES[kind])
    title = {'combined': 'Data file', 'actuals': 'Actuals', 'budget': 'Budget', 'forecast': 'Forecast',
             'pl': 'Sales and P&L lines'}[kind]
    with st.expander(f'Column mapping · {title}: {upload.name}', expanded=bool(check_roles(guesses, kind, 'unknown'))):
        st.caption('Check what each column means. Guesses come from the column names; '
                   '"Extra dimension" makes a column available under Analyse by, "Ignore" leaves it out.')
        cols = st.columns(4)
        roles = []
        for i, header in enumerate(headers):
            sample = ', '.join(str(r[i]) for r in rows[:3] if i < len(r))
            roles.append(cols[i % 4].selectbox(f'“{header}”', options, index=options.index(guesses[i]),
                                               format_func=ROLE_LABELS.get, key=f'map_{kind}_{file_id}_{i}',
                                               help=f'First values: {sample}' if sample else None))
    return headers, rows, roles, upload.name


def load_inputs(mode, uploads, gl_map=None):
    """Main area: mapping and conversion. Returns a dict describing the standard-layout data.

    'pl_text' is the optional sales and P&L file in the standard layout (None without one).
    gl_map: the uploaded GL mapping, when the files post to GL codes. The demo is GL-level data.
    """
    main = {'combined'} if mode == COMBINED else {'actuals', 'budget'}
    if not main <= set(uploads):
        if mode == SEPARATE:
            st.info('Upload at least the actuals and budget files. Until then the demo data is shown.')
        elif 'pl' in uploads:
            st.info('Upload the main expense file as well. Until then the demo data is shown.')
        return {'text': sample_gl_csv(), 'pl_text': sample_gl_pl_csv(), 'source': 'demo', 'demo': True, 'attrs': {},
                'report': None, 'has_forecast': True, 'fingerprint': 'demo', 'data_log': []}
    kinds = (['combined'] if mode == COMBINED else [k for k, _ in FILES if k in uploads]) + (['pl'] if 'pl' in uploads else [])
    specs = {kind: _map_columns(kind, uploads[kind]) for kind in kinds}
    country = ''
    if any('country' not in spec[2] for spec in specs.values()):
        country = st.text_input('Country / entity for files without a country column',
                                help='For single-entity ledgers, e.g. India. Every row of those files gets this name.')
    problems = [f'{spec[3]}: {p}' for kind, spec in specs.items() for p in check_roles(spec[2], kind, country)]
    if problems:
        raise ValueError('Column mapping needs attention. ' + ' '.join(problems))
    if any('currency' not in spec[2] for spec in specs.values()):
        st.caption('A file has no currency column, so its amounts are read as INR, the only supported currency.')
    digest = hashlib.sha256(mode.encode())
    for kind, spec in specs.items():
        digest.update(kind.encode() + spec[3].encode() + uploads[kind].getvalue() + repr(spec[2]).encode())
    digest.update(country.encode())
    specs, data_log, accepted = data_checks(specs, list(gl_map or ()), digest.hexdigest()[:10])
    digest.update(repr(sorted(accepted)).encode())  # accepting a match changes the data
    source = 'upload:' + ','.join(f'{k}={s[3]}' for k, s in specs.items())
    credits = gl_map is not None
    pl_spec = specs.pop('pl', None)
    pl_text = combined_csv(*pl_spec, country) if pl_spec else None
    if mode == COMBINED:
        headers, rows, roles, name = specs['combined']
        return {'text': combined_csv(headers, rows, roles, name, country), 'pl_text': pl_text,
                'source': source, 'demo': False, 'attrs': {}, 'report': None, 'has_forecast': True,
                'fingerprint': digest.hexdigest(), 'data_log': data_log}
    text, attrs, report = separate_csv(specs, country, resolve=gl_resolver(gl_map) if credits else None)
    return {'text': text, 'pl_text': pl_text, 'source': source, 'demo': False, 'attrs': attrs, 'report': report,
            'has_forecast': report['forecast'], 'fingerprint': digest.hexdigest(), 'data_log': data_log}


def data_checks(specs, gl_codes, file_id):
    """Run the rule-based checks, show what was cleaned, and let the reviewer accept suggested matches.

    Returns (cleaned specs, audit log, accepted suggestion ids). Nothing is merged without a tick.
    """
    cleaned, auto, suggestions = validate.check(specs, gl_codes)
    accepted = []
    if auto or suggestions:
        title = f'Data checks · {len(auto)} automatic fix(es)' + (f' · {len(suggestions)} possible match(es) to review'
                                                                  if suggestions else '')
        with st.expander(title, expanded=bool(suggestions)):
            for a in auto:
                st.caption(f"✓ {a['file']} · {a['field']}: {a['check'].lower()} on {a['count']} value(s), "
                           f"for example {a['example']}.")
            if suggestions:
                st.markdown('**The same code may be spelled differently.** Until you tick a match, each spelling is '
                            'treated as its own line, so a budget and its actuals may not meet.')
            for s in suggestions:
                variants = '; '.join(f"\"{v['value']}\" in {', '.join(v['files'])} ({v['rows']} row(s), "
                                     f"differs by {v['difference']})" for v in s['from'])
                if st.checkbox(f"{s['label']}: treat {variants} as \"{s['to']}\" ({', '.join(s['to_files'])})",
                               key=f"datafix_{file_id}_{s['id']}"):
                    accepted.append(s['id'])
            st.caption('Rules only, no AI. Every check and your decision go into the review pack (Data checks sheet).')
    return validate.apply(cleaned, suggestions, accepted), validate.log(auto, suggestions, accepted), accepted

def show_reconciliation(report, rows):
    """Control totals per file against what the analysis uses, plus lines found in only one file."""
    if not report:
        return
    used = {'actuals': sum(r['actual'] for r in rows), 'budget': sum(r['budget'] for r in rows),
            'forecast': sum(r['forecast'] for r in rows)}
    unbudgeted, unspent = report['unbudgeted'], report['unspent']
    if unbudgeted:
        st.warning(f'{len(unbudgeted)} line(s) have actuals but no budget line and are treated as unbudgeted '
                   '(zero budget). See Data reconciliation.')
    with st.expander('Data reconciliation', expanded=False):
        table = ['| File | Rows read | Lines for ' + report['month'] + ' | Other months ignored | File total INR | Used in analysis INR | Ties? |',
                 '| --- | ---: | ---: | ---: | ---: | ---: | :---: |']
        for f in report['files']:
            ties = '✅' if f['total'] == used[f['kind']] else '❌'
            table.append(f"| {f['kind'].title()} · {f['name']} | {f['rows']} | {f['lines']} | {f['other_months']} | "
                         f"{f['total']:,.2f} | {used[f['kind']]:,.2f} | {ties} |")
        st.markdown('\n'.join(table))
        if not report['forecast']:
            st.caption('No forecast file: only the budget comparison is available.')
        if report['dropped_dims']:
            st.caption('Not every file has ' + ', '.join(report['dropped_dims']) + ', so the files are matched without it.')
        for title, items in (('Actuals with no budget line (zero budget)', unbudgeted),
                             ('Budget with no actuals (zero actual)', unspent)):
            if items:
                st.markdown(f'**{title}:** ' + '; '.join(items))
