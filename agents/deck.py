"""CFO deck agent: builds a PowerPoint review deck from a locked pack.

Every chart, table and number is drawn by code from the pack. The AI writes only the slide
headlines and three summary bullets; each one is tick-and-tied, and any that fail are replaced
by standard wording, so the deck always builds and never shows an untraced figure.
"""
import io
import json
import math
import re
from decimal import Decimal

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from agents import inr, material_lines, owner, pct_text
from checker import allowed_figures, internal_codes, misattributed_figures, unsupported_figures
from llm import AIError
from pack import load_pack, open_reason, pack_json

NAVY, BLUE, RED, GREEN = RGBColor(0x0B, 0x3B, 0x8C), RGBColor(0x17, 0x5C, 0xD3), RGBColor(0xB4, 0x23, 0x18), RGBColor(0x02, 0x7A, 0x48)
AMBER, GREY, INK, WHITE = RGBColor(0xB5, 0x47, 0x08), RGBColor(0x66, 0x70, 0x85), RGBColor(0x10, 0x18, 0x28), RGBColor(0xFF, 0xFF, 0xFF)
LIGHT, LINE = RGBColor(0xF5, 0xF7, 0xFB), RGBColor(0xE4, 0xE9, 0xF2)
KEYS = ('summary', 'bridge', 'top', 'heatmap', 'offsets', 'owners', 'outlook')
TIMINGS = ('One-off', 'Recurring', 'Timing difference', 'Unknown')

SYSTEM = """You write slide headlines for a CFO expense variance review deck.
Each headline states the "so what" of one slide in at most 14 words.
Rules:
1. Use ONLY the facts given. Never add causes, events or judgements that are not in the facts.
2. Copy amounts exactly as written in the facts (for example "INR 380,000"). Do not calculate new numbers.
3. Expense above plan is unfavorable; below plan is favorable.
4. Never write internal codes such as L01.
Slides: summary = overall result; bridge = which accounts move budget to actual; top = largest line movements;
heatmap = where variance is concentrated; offsets = what net figures hide; owners = who holds open items;
outlook = run-rate impact by timing. Also write exactly 3 summary bullets for the executive summary slide."""

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['headlines', 'summary'], 'properties': {
    'headlines': {'type': 'object', 'additionalProperties': False, 'required': list(KEYS),
                  'properties': {k: {'type': 'string'} for k in KEYS}},
    'summary': {'type': 'array', 'minItems': 3, 'maxItems': 3, 'items': {'type': 'string'}}}}


def _d(value):
    return Decimal(value) if value is not None else Decimal(0)


def _whole(value):
    return f'INR {abs(_d(value)):,.0f}'


def _compact(value):
    value = _d(value)
    sign = '+' if value > 0 else '−' if value < 0 else ''
    value = abs(value)
    if value >= 10 ** 6:
        return f'{sign}{value / 10 ** 6:.2f}m'
    return f'{sign}{value / 1000:.0f}k' if value >= 1000 else f'{sign}{value:.0f}'


def account_steps(pack):
    """Net variance per account across all countries and cost centres, largest first."""
    steps = {}
    for line in pack['lines']:
        steps[line['account']] = steps.get(line['account'], Decimal(0)) + _d(line['variance'])
    return sorted(((a, v) for a, v in steps.items() if v), key=lambda kv: abs(kv[1]), reverse=True)


def timing_split(pack):
    """Confirmed explained amounts by timing, plus what is still unexplained (material lines only)."""
    split = {t: Decimal(0) for t in TIMINGS}
    for _, line in material_lines(pack):
        if line['note'] and _d(line['explained']):
            split[line['note']['timing']] += _d(line['explained'])
    split['Unexplained'] = _d(pack['summary']['unexplained'])
    return split


def owner_table(pack):
    owners = {}
    for _, line in material_lines(pack):
        why = open_reason(line)
        if why:
            entry = owners.setdefault(owner(line), {'lines': 0, 'open': Decimal(0), 'reasons': {}})
            entry['lines'] += 1
            entry['open'] += _d(line['unexplained'])
            entry['reasons'][why] = entry['reasons'].get(why, 0) + 1
    return sorted(owners.items(), key=lambda kv: kv[1]['open'], reverse=True)


def explained_share(pack):
    gross = sum(abs(_d(l['variance'])) for l in pack['lines'] if l['material'])
    return (_d(pack['summary']['explained']) / gross * 100) if gross else None


def _facts(pack):
    t, s, basis = pack['totals'], pack['summary'], pack['basis']
    share = explained_share(pack)
    split = timing_split(pack)
    return {
        'period': pack['period'], 'comparison': basis,
        'net': f"{_whole(t['variance'])} {'above' if _d(t['variance']) > 0 else 'below'} {basis} ({t['direction'].lower()})",
        'net_pct': pct_text(t) if t['pct'] is not None else 'N/A',
        'actual': _whole(t['actual']), basis: _whole(t['baseline']),
        'explained_share': f'{share:.0f}%' if share is not None else 'N/A',
        'explained_confirmed': _whole(s['explained']), 'unexplained': _whole(s['unexplained']),
        'lines_to_explain': s['material'], 'open_items': s['open_items'],
        'accounts': [f"{a}: {_whole(v)} {'above' if v > 0 else 'below'} {basis}" for a, v in account_steps(pack)[:4]],
        'largest_lines': [f"{l['label']}: {_whole(l['variance'])} {l['direction'].lower()}" for _, l in material_lines(pack)[:4]],
        'offsets': [f"{e['label']}: net {_whole(e['net'])}, gross {_whole(e['gross'])}"
                    for e in pack.get('rollups', {}).get('account', []) if 'Offsetting movements' in e['flags']][:3],
        'spread': [e['label'] for e in pack.get('rollups', {}).get('account', []) if 'Material in total only' in e['flags']],
        'owners_with_open_items': [f"{name}: {v['lines']} line(s), {_whole(v['open'])} open" for name, v in owner_table(pack)[:3]],
        'timing': {k: _whole(v) for k, v in split.items()},
    }


def default_headlines(pack):
    t, s, basis = pack['totals'], pack['summary'], pack['basis']
    steps, share, owners = account_steps(pack), explained_share(pack), owner_table(pack)
    offsets = [e for e in pack.get('rollups', {}).get('account', []) if 'Offsetting movements' in e['flags']]
    split = timing_split(pack)
    top = material_lines(pack)[0][1] if material_lines(pack) else None
    word = 'above' if _d(t['variance']) > 0 else 'below'
    return {
        'summary': f"Expenses {_whole(t['variance'])} {word} {basis}; "
                   + (f"{share:.0f}% of material movement explained" if share is not None else 'no material movements'),
        'bridge': (f"{steps[0][0]} is the largest account movement at {_whole(steps[0][1])}" if steps else 'Actual equals plan'),
        'top': (f"Largest line: {top['label']}, {_whole(top['variance'])} {top['direction'].lower()}" if top
                else 'No line meets the thresholds'),
        'heatmap': f"{s['material']} lines need explanation across {len({l['country'] for l in pack['lines']})} countries",
        'offsets': (f"{offsets[0]['label']}: net {_whole(offsets[0]['net'])} hides {_whole(offsets[0]['gross'])} gross movement"
                    if offsets else 'No material offsets hidden inside account totals'),
        'owners': (f"{owners[0][0]} holds the most open variance: {_whole(owners[0][1]['open'])}" if owners
                   else 'No open items'),
        'outlook': f"Recurring {_whole(split['Recurring'])}, one-off {_whole(split['One-off'])}, "
                   f"unexplained {_whole(split['Unexplained'])}",
    }


def default_summary(pack):
    t, s, basis = pack['totals'], pack['summary'], pack['basis']
    return [f"Actual {_whole(t['actual'])} vs {basis} {_whole(t['baseline'])}: net {_whole(t['variance'])} {t['direction'].lower()}.",
            f"Confirmed explanations cover {_whole(s['explained'])}; {_whole(s['unexplained'])} remains unexplained.",
            f"{s['open_items']} open item(s) across {s['material']} lines that need explanation."]


def derived_records(pack):
    """Figures the deck shows beyond lines and roll-ups: account bridge steps, owner totals, timing split."""
    return ([(account, [value]) for account, value in account_steps(pack)]
            + [(name, [v['open']]) for name, v in owner_table(pack)]
            + [(timing, [value]) for timing, value in timing_split(pack).items() if timing != 'Unexplained'])


def _problems(text, allowed, pack):
    issues = [f'"{f}" is not in the pack' for f in unsupported_figures(text, allowed)]
    issues += misattributed_figures(text, pack, derived_records(pack))
    issues += [f'uses the internal code {c}' for c in internal_codes(text)]
    if len(text) > 130:
        issues.append('too long for a headline')
    return issues


def draft_headlines(pack, client):
    """Return (headlines, summary, issues, source). AI text that fails the checks is replaced, never shown."""
    defaults, fallback_summary = default_headlines(pack), default_summary(pack)
    if client is None:
        return defaults, fallback_summary, [], 'standard titles'
    allowed, facts = allowed_figures(pack), json.dumps(_facts(pack), ensure_ascii=False, indent=1)
    try:
        reply = client.complete('deck', pack['content_hash'], SYSTEM, facts, SCHEMA)

        def review(reply):
            found = {k: _problems(v, allowed, pack) for k, v in reply['headlines'].items()}
            found.update({f'summary {i}': _problems(b, allowed, pack) for i, b in enumerate(reply['summary'], 1)})
            return {k: v for k, v in found.items() if v}

        problems = review(reply)
        if problems:
            notes = '\n- '.join(f'{k}: {", ".join(v)}' for k, v in problems.items())
            reply = client.complete('deck-retry', pack['content_hash'], SYSTEM,
                                    facts + '\n\nYour previous headlines failed these checks. Fix them:\n- ' + notes, SCHEMA)
            problems = review(reply)
    except AIError as error:
        return defaults, fallback_summary, [f'AI headlines unavailable ({error}); standard titles used.'], 'standard titles'
    headlines = {k: (defaults[k] if k in problems else reply['headlines'][k].strip()) for k in KEYS}
    summary_failed = any(k.startswith('summary ') for k in problems)
    summary = fallback_summary if summary_failed else [b.strip() for b in reply['summary']]
    issues = [f'{k}: replaced with standard wording ({", ".join(v)})' for k, v in problems.items()]
    return headlines, summary, issues, f'AI ({client.label}), tick-and-tied'


# ---------- drawing helpers ----------

def _text(slide, x, y, w, h, text, size=14, bold=False, color=INK, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap, frame.vertical_anchor = True, anchor
    frame.margin_left = frame.margin_right = Inches(0.05)
    lines = text if isinstance(text, list) else [text]
    for i, line in enumerate(lines):
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        para.alignment = align
        run = para.add_run()
        run.text = line
        run.font.size, run.font.bold, run.font.color.rgb, run.font.name = Pt(size), bold, color, 'Segoe UI'
        para.space_after = Pt(6)
    return box


def _rect(slide, x, y, w, h, fill, shape=MSO_SHAPE.RECTANGLE):
    item = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    item.fill.solid()
    item.fill.fore_color.rgb = fill
    item.line.fill.background()
    item.shadow.inherit = False
    return item


def _slide(deck, kicker, headline, footer):
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _rect(slide, 0, 0, 13.333, 0.12, BLUE)
    _text(slide, 0.6, 0.35, 12, 0.35, kicker.upper(), size=11, bold=True, color=BLUE)
    _text(slide, 0.6, 0.65, 12.1, 0.9, headline, size=24, bold=True, color=INK)
    _rect(slide, 0.6, 7.0, 12.13, 0.01, LINE)
    _text(slide, 0.6, 7.05, 10.5, 0.3, footer, size=9, color=GREY)
    _text(slide, 11.7, 7.05, 1.0, 0.3, str(len(deck.slides)), size=9, color=GREY, align=PP_ALIGN.RIGHT)
    return slide


def _table(slide, x, y, w, header, rows, widths, row_h=0.36, size=11, fills=None, colours=None):
    shape = slide.shapes.add_table(len(rows) + 1, len(header), Inches(x), Inches(y), Inches(w), Inches(row_h * (len(rows) + 1)))
    table = shape.table
    for i, width in enumerate(widths):
        table.columns[i].width = Inches(width)
    for r, values in enumerate([header] + rows):
        for c, value in enumerate(values):
            cell = table.cell(r, c)
            cell.text = str(value)
            cell.margin_left = cell.margin_right = Inches(0.06)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            para = cell.text_frame.paragraphs[0]
            para.alignment = (PP_ALIGN.RIGHT if c and re.fullmatch(r'[+−-]?[\d,]+(\.\d+)?[%km]?|N/A', str(value)) else
                              PP_ALIGN.CENTER if str(value) == '·' else PP_ALIGN.LEFT)
            font = para.runs[0].font if para.runs else para.font
            font.size, font.name = Pt(size), 'Segoe UI'
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb, font.bold, font.color.rgb = NAVY, True, WHITE
            else:
                cell.fill.fore_color.rgb = (fills(r - 1, c) if fills else None) or (LIGHT if r % 2 == 0 else WHITE)
                font.color.rgb = (colours(r - 1, c) if colours else None) or INK
    return table


def _blend(colour, share):
    share = max(0.0, min(1.0, share))
    return RGBColor(*(round(255 + (channel - 255) * share) for channel in colour))


def _status(line):
    if not line['note']:
        return 'Pending'
    return {'Confirmed by reviewer': 'Confirmed', 'Unconfirmed': 'Unconfirmed'}.get(line['note']['status'], 'Conflicting')


def _signed(value):
    value = _d(value)
    return f"{'+' if value > 0 else '−' if value < 0 else ''}{abs(value):,.0f}"


def _label(point, text, colour, position):
    label = point.data_label
    label.has_text_frame = True
    label.text_frame.text = text
    run = label.text_frame.paragraphs[0].runs[0]
    run.font.size, run.font.bold, run.font.color.rgb = Pt(11), True, colour
    label.position = position


def _tidy(chart):
    """Light gridlines, no chart title, grey axis labels."""
    chart.has_title = False
    axis = chart.value_axis
    axis.has_major_gridlines = True
    axis.major_gridlines.format.line.color.rgb = LINE
    axis.format.line.fill.background()
    axis.tick_labels.font.size, axis.tick_labels.font.color.rgb = Pt(10), GREY
    chart.category_axis.tick_labels.font.size = Pt(11)
    chart.category_axis.format.line.color.rgb = LINE


def _pct(line):
    return f"{abs(_d(line['pct'])):.1f}%" if line['pct'] is not None else 'N/A'


def _plain(value):
    return f'{abs(_d(value)):,.0f}'


# ---------- slides ----------

def _title_slide(deck, pack, source):
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _rect(slide, 0, 0, 13.333, 7.5, NAVY)
    _rect(slide, 0, 5.2, 13.333, 2.3, BLUE)
    _text(slide, 0.8, 1.6, 11.5, 0.4, 'MONTH-END CLOSE · CFO REVIEW', size=14, bold=True, color=RGBColor(0xB2, 0xCC, 0xFF))
    _text(slide, 0.8, 2.1, 11.5, 1.4, f"Expense variance review: {pack['period']}", size=40, bold=True, color=WHITE)
    level = ' × '.join(['Country'] + [d.replace('_', ' ').title() for d in pack['grain']] + ['Account'])
    _text(slide, 0.8, 3.4, 11.5, 0.6, f"Actual vs {pack['basis']} · {pack['scope']} · {level} · {pack['currency']}", size=18, color=WHITE)
    _text(slide, 0.8, 5.6, 11.8, 1.2, [f"Locked review pack {pack['content_hash'][:12]} · locked {pack.get('locked_at', 'not locked')} UTC",
                                       f'Figures and charts are drawn from the pack. Headlines: {source}. Explanations are reviewer-supplied.'],
          size=13, color=WHITE)


def _summary_slide(deck, pack, headline, bullets, footer):
    slide = _slide(deck, 'Executive summary', headline, footer)
    t, s = pack['totals'], pack['summary']
    share = explained_share(pack)
    tiles = [('Actual · INR', _plain(t['actual']), '', INK), (pack['basis'].title() + ' · INR', _plain(t['baseline']), '', INK),
             ('Net variance · INR', _signed(t['variance']), f"{t['direction']} · {_pct(t)}",
              RED if _d(t['variance']) > 0 else GREEN),
             ('Explained', f'{share:.0f}%' if share is not None else '–', f"{_whole(s['explained'])} confirmed", BLUE),
             ('Open items', str(s['open_items']), f"{_whole(s['unexplained'])} unexplained", AMBER if s['open_items'] else GREEN)]
    for i, (label, value, sub, colour) in enumerate(tiles):
        x = 0.6 + i * 2.46
        _rect(slide, x, 1.75, 2.3, 1.45, LIGHT, MSO_SHAPE.ROUNDED_RECTANGLE)
        _rect(slide, x, 1.75, 0.08, 1.45, colour)
        _text(slide, x + 0.2, 1.85, 2.0, 0.3, label.upper(), size=10, bold=True, color=GREY)
        _text(slide, x + 0.2, 2.15, 2.05, 0.55, value, size=20, bold=True, color=colour)
        _text(slide, x + 0.2, 2.7, 2.05, 0.4, sub, size=10, color=GREY)
    _text(slide, 0.6, 3.6, 12, 0.4, 'KEY MESSAGES', size=11, bold=True, color=BLUE)
    _text(slide, 0.6, 4.0, 12.1, 2.8, [f'•  {b}' for b in bullets], size=17, color=INK)


def _bridge_slide(deck, pack, headline, footer):
    slide = _slide(deck, f"{pack['basis'].title()} to actual bridge", headline, footer)
    steps, plan, actual = account_steps(pack), _d(pack['totals']['baseline']), _d(pack['totals']['actual'])
    cats, base, total, up, down = [pack['basis'].title()], [0.0], [float(plan)], [0.0], [0.0]
    running, levels = plan, [plan]
    for account, delta in steps:
        cats.append(account)
        if delta >= 0:
            base.append(float(running)); up.append(float(delta)); down.append(0.0)
            running += delta
        else:
            running += delta
            base.append(float(running)); down.append(float(-delta)); up.append(0.0)
        total.append(0.0)
        levels.append(running)
    cats.append('Actual'); base.append(0.0); total.append(float(actual)); up.append(0.0); down.append(0.0)
    data = CategoryChartData()
    data.categories = cats
    for name, values in (('Base', base), ('Total', total), ('Increase', up), ('Decrease', down)):
        data.add_series(name, values)
    chart = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_STACKED, Inches(0.6), Inches(1.7), Inches(8.6), Inches(5.1), data).chart
    chart.has_legend = False
    plot = chart.plots[0]
    plot.gap_width, plot.overlap = 45, 100
    series_list = list(chart.series)
    base_series = series_list[0]
    base_series.format.fill.background()
    base_series.format.line.fill.background()
    for series, colour in zip(series_list[1:], (BLUE, RED, GREEN)):
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = colour
    for i in range(len(cats)):
        if total[i]:  # start and end bars: amount inside, white
            _label(series_list[1].points[i], f'{total[i] / 1e6:.2f}m', WHITE, XL_LABEL_POSITION.INSIDE_END)
        elif up[i] or down[i]:  # steps: amount just below the bar, on the invisible base
            _label(base_series.points[i], _compact(up[i] or -down[i]), RED if up[i] else GREEN, XL_LABEL_POSITION.INSIDE_END)
    low, high = min(levels + [actual]), max(levels + [actual])
    span = max(high - low, Decimal(1))
    step = Decimal(10) ** int(math.floor(math.log10(float(span))))
    axis = chart.value_axis
    axis.minimum_scale = float(max(Decimal(0), ((low - span * Decimal('0.6')) / step).to_integral_value(rounding='ROUND_FLOOR') * step))
    axis.maximum_scale = float(((high + span * Decimal('0.25')) / step).to_integral_value(rounding='ROUND_CEILING') * step)
    _tidy(chart)
    axis.tick_labels.number_format, axis.tick_labels.number_format_is_linked = '#,##0.0,,"m"', False
    chart.category_axis.tick_labels.font.size = Pt(11)
    _text(slide, 0.6, 6.75, 8.6, 0.3, f'INR millions. The axis starts at {axis.minimum_scale / 1e6:.1f}m, not zero, so movements are visible.',
          size=9, color=GREY)
    rows = [[a, _signed(v), 'Unfavorable' if v > 0 else 'Favorable'] for a, v in steps]
    _table(slide, 9.5, 1.75, 3.25, ['Account', 'INR', 'Outcome'], rows, [1.35, 1.0, 0.9], size=10,
           colours=lambda r, c: (RED if steps[r][1] > 0 else GREEN) if c else None)


def _top_slide(deck, pack, headline, footer):
    slide = _slide(deck, 'Largest movements', headline, footer)
    lines = material_lines(pack)[:9]
    rows = [[l['label'], owner(l) if l['attrs'].get('owner') else '–', _signed(l['variance']), _pct(l),
             _status(l), _plain(l['unexplained'])] for _, l in lines]
    status_colour = {'Confirmed': GREEN, 'Unconfirmed': AMBER, 'Conflicting': RED, 'Pending': GREY}
    _table(slide, 0.6, 1.75, 12.13, ['Line', 'Owner', 'Variance INR', '%', 'Status', 'Unexplained INR'], rows,
           [4.6, 1.9, 1.6, 1.4, 1.3, 1.33], row_h=0.42, size=12,
           colours=lambda r, c: (RED if _d(lines[r][1]['variance']) > 0 else GREEN) if c == 2
           else status_colour[rows[r][4]] if c == 4 else AMBER if c == 5 and rows[r][5] != '0' else None)
    if not lines:
        _text(slide, 0.6, 2.4, 12, 0.5, 'No line meets the materiality thresholds.', size=16, color=GREY)


def _heatmap_slide(deck, pack, headline, footer):
    slide = _slide(deck, 'Where the variance sits', headline, footer)
    accounts = list(dict.fromkeys(l['account'] for l in pack['lines']))
    names = list(dict.fromkeys(l['label'].rsplit(' | ', 1)[0] for l in pack['lines']))
    cells = {(l['label'].rsplit(' | ', 1)[0], l['account']): l for l in pack['lines']}
    shown = names[:12]
    peak = max((abs(_d(l['variance'])) for l in pack['lines']), default=Decimal(1)) or Decimal(1)
    rows = [[n] + [_compact(cells[(n, a)]['variance']) if (n, a) in cells else '·' for a in accounts]
            + [_compact(sum((_d(cells[(n, a)]['variance']) for a in accounts if (n, a) in cells), Decimal(0)))] for n in shown]

    def fill(r, c):
        if c == 0 or c > len(accounts):
            return None
        line = cells.get((shown[r], accounts[c - 1]))
        if not line or not _d(line['variance']):
            return WHITE
        value = _d(line['variance'])
        return _blend(RED if value > 0 else GREEN, 0.15 + 0.7 * float(abs(value) / peak))

    def colour(r, c):
        line = cells.get((shown[r], accounts[c - 1])) if 0 < c <= len(accounts) else None
        if line and abs(_d(line['variance'])) / peak > Decimal('0.55'):
            return WHITE
        return None

    table = _table(slide, 0.6, 1.75, 12.13, ['Line'] + accounts + ['Net'], rows,
                   [3.6] + [round(7.6 / len(accounts), 2)] * len(accounts) + [0.93], row_h=0.38, size=11, fills=fill, colours=colour)
    for r, name in enumerate(shown, 1):
        for c, account in enumerate(accounts, 1):
            line = cells.get((name, account))
            if line and line['material']:
                table.cell(r, c).text_frame.paragraphs[0].runs[0].font.bold = True
    note = 'Red = overspend, green = underspend, darker = larger; bold = needs explanation; · = no data. INR.'
    if len(names) > len(shown):
        note += f' Showing {len(shown)} of {len(names)} rows; the appendix lists every line needing explanation.'
    _text(slide, 0.6, 6.6, 12, 0.3, note, size=9, color=GREY)


def _offsets_slide(deck, pack, headline, footer):
    slide = _slide(deck, 'What net figures hide', headline, footer)
    entries = [e for e in pack.get('rollups', {}).get('account', []) if e['flags']][:6]
    if not entries:
        _text(slide, 0.6, 2.2, 12, 0.6, 'No account total hides offsetting lines or a spread variance at the current thresholds.',
              size=16, color=GREY)
        return
    data = CategoryChartData()
    shown = entries[::-1]  # bar charts draw the first category at the bottom
    data.categories = [e['label'] for e in shown]
    data.add_series('Net movement', [float(abs(_d(e['net']))) for e in shown])
    data.add_series('Gross movement', [float(_d(e['gross'])) for e in shown])
    chart = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.6), Inches(1.7), Inches(7.4), Inches(5.0), data).chart
    chart.has_legend, chart.legend.position, chart.legend.include_in_layout = True, XL_LEGEND_POSITION.BOTTOM, False
    chart.legend.font.size = Pt(11)
    for series, colour in zip(chart.series, (BLUE, AMBER)):
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = colour
    plot = chart.plots[0]
    plot.has_data_labels = True
    plot.data_labels.font.size, plot.data_labels.number_format, plot.data_labels.number_format_is_linked = Pt(10), '#,##0,"k"', False
    _tidy(chart)
    chart.value_axis.tick_labels.number_format, chart.value_axis.tick_labels.number_format_is_linked = '#,##0,"k"', False
    notes = []
    for e in entries:
        if 'Offsetting movements' in e['flags']:
            notes.append(f"⇅ {e['label']}: net {_signed(e['net'])}, gross {_d(e['gross']):,.0f}; "
                         f"offsetting lines hide {_d(e['hidden']):,.0f}.")
        if 'Material in total only' in e['flags']:
            notes.append(f"Σ {e['label']}: material in total, but no single line crosses the threshold, so every owner explains their share.")
    _text(slide, 8.3, 1.8, 4.45, 5.0, notes, size=13, color=INK)


def _owners_slide(deck, pack, headline, footer):
    slide = _slide(deck, 'Open items by owner', headline, footer)
    owners = owner_table(pack)
    rows = [[name, str(v['lines']), _plain(v['open']), '; '.join(f'{n} × {why.lower()}' for why, n in v['reasons'].items())]
            for name, v in owners[:10]]
    if not rows:
        _text(slide, 0.6, 2.2, 12, 0.6, 'Every line that needs an explanation has a confirmed one.', size=16, color=GREEN)
        return
    _table(slide, 0.6, 1.75, 12.13, ['Owner', 'Open lines', 'Unexplained INR', 'Why open'], rows, [2.6, 1.4, 1.9, 6.23],
           row_h=0.42, size=12, colours=lambda r, c: AMBER if c == 2 else None)


def _outlook_slide(deck, pack, headline, footer):
    slide = _slide(deck, 'Outlook: run-rate impact', headline, footer)
    split = timing_split(pack)
    data = CategoryChartData()
    order = list(split)[::-1]  # first category at the top
    data.categories = order
    data.add_series('INR', [float(split[k]) for k in order])
    chart = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.6), Inches(1.7), Inches(6.4), Inches(4.0), data).chart
    chart.has_legend = False
    series = chart.series[0]
    for i, colour in enumerate((BLUE, RED, GREEN, GREY, AMBER)[::-1]):
        point = series.points[i]
        point.format.fill.solid()
        point.format.fill.fore_color.rgb = colour
    plot = chart.plots[0]
    plot.has_data_labels = True
    plot.data_labels.font.size, plot.data_labels.number_format, plot.data_labels.number_format_is_linked = Pt(11), '#,##0,"k"', False
    _tidy(chart)
    chart.value_axis.tick_labels.number_format, chart.value_axis.tick_labels.number_format_is_linked = '#,##0,"k"', False
    _text(slide, 0.6, 5.75, 6.4, 1.1, ['Confirmed explanations only, by the timing the reviewer gave. Unexplained = material variance '
                                       'without a confirmed explanation.'], size=10, color=GREY)
    meaning = [f"↻ Recurring {_whole(split['Recurring'])}: likely to continue; reflect it in the next forecast.",
               f"⇄ Timing {_whole(split['Timing difference'])}: expected to reverse in later months.",
               f"① One-off {_whole(split['One-off'])}: no run-rate impact expected.",
               f"? Open {_whole(split['Unexplained'])}: no confirmed explanation yet."]
    actions = [f"☐ {l['label']}: {l['note']['action']}" for _, l in material_lines(pack) if l['note'] and l['note']['action']][:5]
    _text(slide, 7.3, 1.75, 5.45, 2.4, meaning, size=13, color=INK)
    if actions:
        _text(slide, 7.3, 4.2, 5.45, 0.35, 'ACTIONS PROPOSED BY REVIEWERS', size=11, bold=True, color=BLUE)
        _text(slide, 7.3, 4.55, 5.45, 2.4, actions, size=11, color=INK)


def _appendix_slides(deck, pack, footer):
    lines = material_lines(pack)
    for start in range(0, max(len(lines), 1), 12):
        chunk = lines[start:start + 12]
        slide = _slide(deck, 'Appendix', f'Lines needing explanation ({start + 1}–{start + len(chunk)} of {len(lines)})', footer)
        rows = [[l['label'], _plain(l['actual']), _plain(l['baseline']), _signed(l['variance']), _pct(l), _status(l),
                 _plain(l['unexplained'])] for _, l in chunk]
        _table(slide, 0.6, 1.6, 12.13, ['Line', 'Actual INR', pack['basis'].title() + ' INR', 'Variance INR', '%', 'Status', 'Unexplained INR'],
               rows, [4.4, 1.4, 1.4, 1.4, 1.2, 1.1, 1.23], row_h=0.38, size=10,
               colours=lambda r, c, chunk=chunk: (RED if _d(chunk[r][1]['variance']) > 0 else GREEN) if c == 3 else None)


def build_deck(pack, headlines, summary, source):
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    footer = f"{pack['period']} · actual vs {pack['basis']} · locked pack {pack['content_hash'][:12]} · figures from the engine · headlines: {source}"
    _title_slide(deck, pack, source)
    _summary_slide(deck, pack, headlines['summary'], summary, footer)
    _bridge_slide(deck, pack, headlines['bridge'], footer)
    _top_slide(deck, pack, headlines['top'], footer)
    _heatmap_slide(deck, pack, headlines['heatmap'], footer)
    if pack['grain']:
        _offsets_slide(deck, pack, headlines['offsets'], footer)
    _owners_slide(deck, pack, headlines['owners'], footer)
    _outlook_slide(deck, pack, headlines['outlook'], footer)
    _appendix_slides(deck, pack, footer)
    output = io.BytesIO()
    deck.save(output)
    return output.getvalue()


def make_deck(pack, client):
    """Locked pack in, PowerPoint out. Never raises for AI problems: standard titles are used instead."""
    pack = load_pack(pack_json(pack))  # refuse a pack edited after locking
    headlines, summary, issues, source = draft_headlines(pack, client)
    outline = [('Title', f"Expense variance review: {pack['period']}"), ('Executive summary', headlines['summary']),
               ('Bridge', headlines['bridge']), ('Largest movements', headlines['top']), ('Heatmap', headlines['heatmap'])]
    if pack['grain']:
        outline.append(('What net figures hide', headlines['offsets']))
    outline += [('Open items by owner', headlines['owners']), ('Outlook', headlines['outlook']), ('Appendix', 'Lines needing explanation')]
    appendix = max(1, math.ceil(len(material_lines(pack)) / 12))
    return {'pptx': build_deck(pack, headlines, summary, source), 'outline': outline, 'summary': summary,
            'slides': len(outline) - 1 + appendix,
            'issues': issues, 'source': source}
