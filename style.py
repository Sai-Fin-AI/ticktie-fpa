"""Look and feel for the Streamlit screens: page styling, hero banner and table colouring."""
from html import escape

import streamlit as st

CSS = """
<style>
.block-container {padding-top: 1.6rem; max-width: 1400px;}
h1 {display: none;}  /* replaced by the hero banner */
h3 {font-weight: 750 !important; letter-spacing: -.01em; margin-top: .2rem !important;}
[data-testid="stMetric"] {background: #fff; border: 1px solid #e4e9f2; border-left: 5px solid #175cd3;
  border-radius: 12px; padding: 14px 16px; box-shadow: 0 1px 2px rgba(16,24,40,.06);}
[data-testid="stMetric"] {padding: 12px 12px;}
[data-testid="stMetricLabel"] p, [data-testid="stMetricLabel"] div {font-size: .72rem; text-transform: uppercase;
  letter-spacing: .04em; color: #475467; white-space: normal !important; overflow: visible !important; line-height: 1.25;}
[data-testid="stMetricValue"] {font-weight: 750; color: #101828; font-size: clamp(1.05rem, 1.55vw, 1.9rem) !important;}
[data-testid="stMetricValue"] div {overflow: visible !important; text-overflow: clip !important;}
[data-testid="stMetricDelta"] div {white-space: normal !important;}
[class*="st-key-card_"] {background: #fff; box-shadow: 0 1px 3px rgba(16,24,40,.06); border-radius: 14px !important;}
div[data-testid="stExpander"] details {background: #fff; border-radius: 10px; border: 1px solid #e4e9f2;}
[data-testid="stDataFrame"] {border-radius: 10px; overflow: hidden; border: 1px solid #e4e9f2;}
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
  border-radius: 999px; font-weight: 600; border: 1px solid #c7d7f0;}
.stButton > button:hover, .stDownloadButton > button:hover {border-color: #175cd3; color: #175cd3;}
/* Primary buttons are filled blue: keep their text white in every state (hover, focus, after a click). */
button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primary"]:hover,
button[data-testid="stBaseButton-primary"]:focus, button[data-testid="stBaseButton-primary"]:active,
button[data-testid="stBaseButton-primary"] p {color: #fff !important; border-color: transparent;}
[data-testid="stSidebar"] {background: linear-gradient(180deg, #eaf0f8 0%, #f5f7fb 100%); border-right: 1px solid #dbe3ef;}
[data-testid="stSidebar"] h2 {font-size: 1rem; text-transform: uppercase; letter-spacing: .06em; color: #175cd3;}
[data-testid="stAlert"] {border-radius: 10px;}
</style>
"""


def apply_style():
    st.html(CSS)


def hero(title, subtitle, chips):
    tags = ''.join(f'<span style="display:inline-block;margin:6px 8px 0 0;padding:4px 12px;border-radius:999px;'
                   f'background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28);font-size:13px">{escape(c)}</span>'
                   for c in chips)
    st.html(f'<div style="background:linear-gradient(120deg,#0b3b8c 0%,#175cd3 55%,#2e90fa 100%);color:#fff;'
            f'border-radius:16px;padding:22px 26px;margin-bottom:6px;box-shadow:0 6px 18px rgba(23,92,211,.25);'
            f"font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif\">"
            f'<div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.8">Month-end close · FP&amp;A</div>'
            f'<div style="font-size:28px;font-weight:800;margin-top:2px">📊 {escape(title)}</div>'
            f'<div style="opacity:.9;margin-top:4px;font-size:14px">{escape(subtitle)}</div><div>{tags}</div></div>')


def section(icon, title, caption=None):
    st.subheader(f'{icon} {title}')
    if caption:
        st.caption(caption)


def colour_table(frame):
    """Red for overspend, green for underspend, amber badge for lines needing explanation. Values stay numeric."""
    def money(value):
        if not isinstance(value, (int, float)) or value == 0:
            return ''
        return 'color:#b42318;font-weight:600' if value > 0 else 'color:#027a48;font-weight:600'

    def outcome(value):
        return {'Unfavorable': 'color:#b42318;font-weight:600', 'Favorable': 'color:#027a48;font-weight:600'}.get(value, '')

    def needs(value):
        return 'background-color:#fffaeb;color:#b54708;font-weight:600' if str(value).startswith('Yes') else 'color:#98a2b3'

    def flags(value):
        return 'color:#b54708;font-weight:600' if value else ''

    styler = frame.style
    for column in frame.columns:
        if column in ('Variance INR', 'Net variance INR'):
            styler = styler.map(money, subset=[column])
        elif column == 'Outcome':
            styler = styler.map(outcome, subset=[column])
        elif column == 'Material':
            styler = styler.map(needs, subset=[column])
        elif column == 'Flags':
            styler = styler.map(flags, subset=[column])
    return styler


def money_columns(frame):
    return {c: st.column_config.NumberColumn(c, format='localized') for c in frame.columns if c.endswith('INR')}
