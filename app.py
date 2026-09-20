"""Run with: python -m streamlit run app.py"""
from decimal import Decimal
import streamlit as st
from finance import load_csv, sample_csv, analyze, compare, commentary

st.set_page_config(page_title='FP&A Variance Assistant', page_icon='📊', layout='wide')
st.title('FP&A Variance Assistant')
st.caption('Project 1 · Expense analysis · INR reporting currency · Version 0.1')
st.info('Synthetic demo data is loaded. Commentary uses fixed rules; AI is a later phase.')

with st.sidebar:
    st.header('Analysis settings')
    baseline = st.selectbox('Compare actuals against', ['budget', 'forecast'])
    amount = Decimal(str(st.number_input('Minimum variance (INR)', min_value=0, value=50000, step=10000)))
    pct = Decimal(str(st.number_input('Minimum variance (%)', min_value=0.0, value=5.0, step=1.0)))
    st.caption('Both thresholds must be met. With a zero baseline, only the INR threshold applies.')
    upload = st.file_uploader('Upload a one-month expense CSV', type=['csv'])
    st.download_button('Download sample input', sample_csv(), 'sample_expenses.csv', 'text/csv')

try:
    content = upload.getvalue().decode('utf-8-sig') if upload else sample_csv()
    rows = load_csv(content)
except (ValueError, UnicodeError) as error:
    st.error(str(error))
    st.stop()

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
st.dataframe(display, use_container_width=True, hide_index=True)

st.subheader('Draft commentary for review')
summary = (f"{selected[0]['month']} | {country} | Compared with {baseline}\n"
           f"Actual expense: INR {actual:,.2f}; {baseline}: INR {plan:,.2f}; "
           f"net variance: INR {total['variance']:+,.2f} ({total['direction'].lower()}).\n\n")
if material:
    for r in material:
        sentence = commentary(r, baseline)
        st.write(sentence)
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
             'FX conversion, driver attribution and AI generation are not included.')
    st.write('The validation cannot detect an entirely omitted account without an expected-account roster. '
             'A CSV must contain the same seven columns as the sample input. '
             'All sample records are fictional. Commentary must be reviewed before use.')
