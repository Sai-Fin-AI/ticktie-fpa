"""On-screen charts (Altair, which ships with Streamlit). The deck draws its own native PowerPoint charts."""
from decimal import Decimal

import altair as alt
import pandas as pd

import fmt

TOTAL, GOOD, BAD = '#13294B', '#067647', '#B42318'


def bridge_chart(walk, baseline):
    """Waterfall from finance.bridge: start bar, one floating bar per account, end bar."""
    running, rows = Decimal(walk['start']), []
    name = walk['measure']
    rows.append({'step': f'{baseline.title()} {name.lower() if name in ("Sales", "Costs") else name}', 'low': 0.0,
                 'high': float(running), 'kind': 'Total', 'label': fmt.compact(running, sign=False)})
    for account, delta, favorable in walk['steps']:
        start, running = running, running + Decimal(delta)
        rows.append({'step': account, 'low': float(min(start, running)), 'high': float(max(start, running)),
                     'kind': 'Helps' if favorable else 'Hurts', 'label': fmt.compact(delta)})
    end = Decimal(walk['end'])
    rows.append({'step': f'Actual {name.lower() if name in ("Sales", "Costs") else name}', 'low': 0.0, 'high': float(end),
                 'kind': 'Total', 'label': fmt.compact(end, sign=False)})
    frame = pd.DataFrame(rows)
    frame['order'] = range(len(frame))
    levels = [r['high'] for r in rows] + [r['low'] for r in rows if r['kind'] != 'Total']
    span = max(levels) - min(levels) or 1.0
    domain = [max(0.0, min(levels) - span * 0.6), max(levels) + span * 0.15]
    good, bad = ('Helps', 'Hurts')
    legend = {'Total': f'{baseline.title()} / actual', good: 'Lowers cost' if name == 'Costs' else 'Helps',
              bad: 'Raises cost' if name == 'Costs' else 'Hurts'}
    frame['legend'] = frame['kind'].map(legend)
    x = alt.X('step:N', sort=list(frame['step']), title=None, axis=alt.Axis(labelAngle=-30, labelLimit=120))
    colour = alt.Color('legend:N', title=None, scale=alt.Scale(domain=list(legend.values()), range=[TOTAL, GOOD, BAD]),
                       legend=alt.Legend(orient='top'))
    bars = alt.Chart(frame).mark_bar(cornerRadius=3, clip=True).encode(  # the axis does not start at zero
        x=x, y=alt.Y('low:Q', scale=alt.Scale(domain=domain, zero=False), title=None,
                     axis=alt.Axis(format='~s', grid=True)), y2='high:Q', color=colour,
        tooltip=[alt.Tooltip('step:N', title='Step'), alt.Tooltip('label:N', title='Amount')])
    text = alt.Chart(frame).mark_text(dy=-7, fontSize=11, fontWeight=500).encode(x=x, y='high:Q', text='label:N')
    return (bars + text).properties(height=320)
