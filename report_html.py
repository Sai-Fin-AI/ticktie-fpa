"""CFO commentary as a styled page: headline tiles, colour-coded cards and coverage bars.

Colour is never the only signal: every badge also carries an arrow or icon and a word, so the
page reads in black-and-white print and for colour-blind readers. All text is HTML-escaped.
"""
from decimal import Decimal
from html import escape

import fmt
from finance import MARGIN_NAMES
from agents import cost_centre, grouped_lines, material_lines, owner, pct_text
from pack import open_reason

RED, GREEN, AMBER, GREY, BLUE = '#b42318', '#027a48', '#b54708', '#667085', '#175cd3'
TINT = {RED: '#fef3f2', GREEN: '#ecfdf3', AMBER: '#fffaeb', GREY: '#f2f4f7', BLUE: '#eff8ff'}
STATUS = {'Confirmed by reviewer': ('✅', 'Confirmed', GREEN), 'Unconfirmed': ('🟡', 'Unconfirmed', AMBER),
          'Conflicting explanations': ('🔴', 'Conflicting', RED), None: ('⚪', 'Explanation pending', GREY)}
TIMING = {'One-off': '① One-off', 'Recurring': '↻ Recurring', 'Timing difference': '⇄ Timing difference', 'Unknown': '? Timing unknown'}
FONT = "font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _inr(value, places=2):
    return f'INR {fmt.group(value, places)}'


def _chip(text, colour):
    return (f'<span style="display:inline-block;padding:2px 10px;margin:0 6px 4px 0;border-radius:999px;font-size:12px;'
            f'font-weight:600;color:{colour};background:{TINT[colour]};border:1px solid {colour}33">{escape(text)}</span>')


def _direction(direction):
    if direction == 'Unfavorable':
        return '▲ Unfavorable', RED
    if direction == 'Favorable':
        return '▼ Favorable', GREEN
    return '● On plan', GREY


def _tile(label, value, sub='', colour='#101828'):
    return (f'<div style="flex:1 1 150px;min-width:140px;padding:12px 14px;border:1px solid #eaecf0;border-radius:10px;background:#fff">'
            f'<div style="font-size:12px;color:{GREY};text-transform:uppercase;letter-spacing:.04em">{escape(label)}</div>'
            f'<div style="font-size:20px;font-weight:700;color:{colour};margin-top:4px">{escape(value)}</div>'
            f'<div style="font-size:12px;color:{colour if sub.startswith(("▲", "▼")) else GREY}">{escape(sub)}</div></div>')


def _coverage_bar(line):
    variance = abs(Decimal(line['variance']))
    explained = Decimal(line['explained'])
    share = int(explained / variance * 100) if variance else 0
    if line['note'] is None:
        label = f"Nothing explained yet · {_inr(line['unexplained'])} open"
    elif line['note']['status'] != 'Confirmed by reviewer':
        label = f"Not counted until resolved · {_inr(line['unexplained'])} open"
    elif share == 100:
        label = f'Fully explained · {_inr(explained)}'
    else:
        label = f"Explained {_inr(explained)} ({share}%) · Unexplained {_inr(line['unexplained'])}"
    return (f'<div style="margin-top:10px"><div style="display:flex;height:8px;border-radius:4px;overflow:hidden;background:{TINT[AMBER]}">'
            f'<div style="width:{share}%;background:{GREEN}"></div><div style="flex:1;background:#fdb022"></div></div>'
            f'<div style="font-size:12px;color:{GREY};margin-top:4px">{escape(label)}</div></div>')


def _compact(value):
    return fmt.compact(value)


def pl_table_html(rows, basis):
    """The P&L statement as a compact table: bold subtotals, indented accounts, margin rows in percentage points.
    Profit impact is green when it helps profit and red when it hurts. Colour is always paired with a sign."""
    if not rows:
        return ''
    cell = 'padding:5px 8px;text-align:right;white-space:nowrap'
    head = (f'<tr style="color:{GREY};font-size:12px"><th style="text-align:left;padding:5px 8px">INR</th>'
            f'<th style="{cell}">Actual</th><th style="{cell}">{escape(basis.title())}</th>'
            f'<th style="{cell}">Profit impact</th><th style="{cell}">%</th></tr>')
    body = []
    for e in rows:
        if e['kind'] == 'margin':
            colour = GREEN if Decimal(e['pts']) > 0 else RED if Decimal(e['pts']) < 0 else GREY
            pts = Decimal(e['pts'])
            body.append(f'<tr style="font-size:12px;color:{GREY}"><td style="padding:3px 8px 6px 18px">{escape(MARGIN_NAMES.get(e["label"], e["label"]))}</td>'
                        f'<td style="{cell}">{Decimal(e["actual_pct"]):.1f}%</td><td style="{cell}">{Decimal(e["baseline_pct"]):.1f}%</td>'
                        f'<td style="{cell};color:{colour}">{"+" if pts > 0 else "−" if pts < 0 else ""}{abs(pts):.1f} pts</td><td></td></tr>')
            continue
        impact = Decimal(e['impact'])
        colour = GREEN if impact > 0 else RED if impact < 0 else GREY
        sub = e['kind'] == 'subtotal'
        style = (f'font-weight:700;background:#f2f4f7;border-top:1px solid #d0d5dd' if sub else
                 f'color:#475467' if e['kind'] == 'account' else 'font-weight:600')
        indent = '22px' if e['kind'] == 'account' else '8px'
        pct = f"{abs(Decimal(e['pct'])):.1f}%" if e['pct'] is not None else 'N/A'
        amount = lambda v: ('−' if Decimal(v) < 0 else '') + fmt.group(v)
        body.append(f'<tr style="{style};border-bottom:1px solid #eaecf0"><td style="padding:5px 8px 5px {indent}">{escape(e["label"])}</td>'
                    f'<td style="{cell}">{amount(e["actual"])}</td><td style="{cell}">{amount(e["baseline"])}</td>'
                    f'<td style="{cell};color:{colour}">{"▲ " if impact > 0 else "▼ " if impact < 0 else ""}'
                    f'{fmt.signed(impact) if impact else "0"}</td><td style="{cell};color:{GREY}">{pct}</td></tr>')
    return (f'<div style="{FONT};overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:13px;'
            f'font-variant-numeric:tabular-nums;background:#fff">{head}{"".join(body)}</table></div>')


def heatmap_html(pack, country=None):
    """Grid of variance by line (rows) and account (columns). Red = unfavorable, green = favorable; bold = needs
    explanation. In a P&L review the Net column and row add up profit impact, because sales and costs mix there."""
    lines = [l for l in pack['lines'] if country in (None, l['country'])]
    if not lines:
        return ''
    profit = pack.get('kind') == 'pl'
    accounts = list(dict.fromkeys(l['account'] for l in lines))
    row_names = list(dict.fromkeys(l['label'].rsplit(' | ', 1)[0] for l in lines))
    cells = {(l['label'].rsplit(' | ', 1)[0], l['account']): l for l in lines}
    peak = max(abs(Decimal(l['variance'])) for l in lines) or Decimal(1)
    effect = lambda l: Decimal(l['impact']) if profit else Decimal(l['variance'])

    def cell(line):
        if line is None:
            return f'<td style="padding:6px 8px;text-align:center;color:#d0d5dd;border:1px solid #f2f4f7">·</td>'
        value = Decimal(line['variance'])
        alpha = float(min(Decimal(1), abs(value) / peak)) * 0.85 + (0.08 if value else 0)
        rgb = '180,35,24' if line['direction'] == 'Unfavorable' else '2,122,72'
        strong = 'font-weight:700;outline:2px solid #101828;outline-offset:-2px' if line['material'] else ''
        ink = '#fff' if alpha > 0.55 else '#101828'
        return (f'<td title="{escape(line["label"])}: {escape(_inr(value))}" style="padding:6px 8px;text-align:center;'
                f'background:rgba({rgb},{alpha:.2f});color:{ink};border:1px solid #fff;{strong}">{_compact(value)}</td>')

    def total(values, good_if_up):
        value = sum(values, Decimal(0))
        colour = GREY if not value else GREEN if (value > 0) == good_if_up else RED
        return f'<td style="padding:6px 8px;text-align:center;font-weight:700;color:{colour};background:#f9fafb">{_compact(value)}</td>'

    def account_total(account):
        members = [l for l in lines if l['account'] == account]
        return total((Decimal(l['variance']) for l in members), members[0].get('pl_line') == 'Sales')

    head = ''.join(f'<th style="padding:6px 8px;font-size:12px;color:{GREY};font-weight:600">{escape(a)}</th>' for a in accounts)
    body = ''.join(
        f'<tr><th style="padding:6px 8px;text-align:left;font-size:12px;font-weight:600;white-space:nowrap">{escape(r)}</th>'
        + ''.join(cell(cells.get((r, a))) for a in accounts)
        + total((effect(l) for (name, _), l in cells.items() if name == r), profit) + '</tr>' for r in row_names)
    foot = ('<tr><th style="padding:6px 8px;text-align:left;font-size:12px">Net</th>'
            + ''.join(account_total(a) for a in accounts)
            + total((effect(l) for l in lines), profit) + '</tr>')
    net_note = ' · Net column and row = profit impact (+ helps profit)' if profit else ''
    return (f'<div style="{FONT};overflow-x:auto"><table style="border-collapse:collapse;font-size:13px;background:#fff;border-radius:10px">'
            f'<tr><th></th>{head}<th style="padding:6px 8px;font-size:12px;color:{GREY}">Net</th></tr>{body}{foot}</table>'
            f'<div style="font-size:12px;color:{GREY};margin-top:6px">Variance vs {escape(pack["basis"])} in INR. '
            '<span style="color:#b42318">■</span> unfavorable · <span style="color:#027a48">■</span> favorable · '
            f'darker = larger · outlined bold = needs explanation · · = no data{net_note}</div></div>')

def _group_header(group):
    label, colour = _direction(group['direction'])
    flags = ''.join(_chip(f'⇅ {f}' if f == 'Offsetting movements' else f'Σ {f}', AMBER) for f in group['flags'])
    hidden = (f" · gross {escape(_inr(group['gross']))}, offsets hide {escape(_inr(group['hidden']))}"
              if 'Offsetting movements' in group['flags'] else '')
    return (f'<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;align-items:center;'
            f'margin:16px 0 8px;padding:8px 12px;border-radius:8px;background:#fff;border:1px solid #eaecf0">'
            f'<div><b>{escape(group["label"])}</b> <span style="color:{GREY};font-size:13px">· net '
            f'<b style="color:{colour}">{escape(_inr(group["net"]))}</b> {escape(label)}{hidden}</span></div>'
            f'<div>{flags}</div></div>')


def _card(line, text, grouped=False):
    label, colour = _direction(line['direction'])
    icon, status, status_colour = STATUS[line['note']['status'] if line['note'] else None]
    chips = _chip(f'{label} · {pct_text(line)}', colour) + _chip(f'{icon} {status}', status_colour)
    if line['note']:
        chips += _chip(TIMING[line['note']['timing']], BLUE if line['note']['timing'] != 'Unknown' else GREY)
    if line.get('scope') == 'Account total':
        chips += _chip('Σ Part of a material account total', AMBER)
    title = (escape(cost_centre(line)) if grouped and cost_centre(line)
             else escape(line.get('label', f"{line['country']} | {line['account']}")).replace(' | ', f' <span style="color:{GREY}">|</span> '))
    if line.get('attrs', {}).get('owner'):
        title += f' <span style="font-weight:500;color:{GREY};font-size:13px">· 👤 {escape(owner(line))}</span>'
    return (f'<div style="border:1px solid #eaecf0;border-left:5px solid {colour};border-radius:10px;padding:14px 16px;'
            f'margin:0 0 12px;{"margin-left:16px;" if grouped else ""}background:#fff">'
            f'<div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:baseline">'
            f'<div style="font-size:15px;font-weight:700">{title}</div>'
            f'<div style="font-size:15px;font-weight:700;color:{colour}">{escape(_inr(line["variance"]))}</div></div>'
            f'<div style="margin-top:8px">{chips}</div>'
            f'<div style="font-size:14px;line-height:1.55;color:#344054;margin-top:4px">{escape(text)}</div>'
            f'{_coverage_bar(line)}</div>')


def _section(title, colour=BLUE):
    return (f'<div style="font-size:13px;font-weight:700;color:{colour};text-transform:uppercase;letter-spacing:.06em;'
            f'margin:22px 0 10px">{escape(title)}</div>')


def full_year_html(outlook):
    """Full-year effect of confirmed explanations, written by code from the pack."""
    if not outlook:
        return ''

    def signed(value, bold=False):
        value = Decimal(value)
        colour = RED if value > 0 else GREEN if value < 0 else GREY
        return (f'<div style="font-size:{18 if bold else 16}px;font-weight:700;color:{colour}">'
                f'{escape(fmt.signed(value)) if value else "0"}</div>')

    months = outlook['months_left']
    once = Decimal(outlook['one_off']) + Decimal(outlook['unknown_timing'])
    cells = [('One-off (once)', once), (f'Recurring × {months + 1} months', outlook['recurring_fy']),
             ('Timing (reverses in year)', 0), ('Full-year effect, confirmed', outlook['fy_effect'])]
    body = ''.join(f'<div style="flex:1 1 150px;min-width:140px"><div style="font-size:12px;color:{GREY}">{escape(label)}</div>'
                   f'{signed(value, i == 3)}</div>' for i, (label, value) in enumerate(cells))
    return (_section(f"Full-year view · {outlook['fy']} · {months} month{'s' if months != 1 else ''} left") +
            f'<div style="background:#fff;border:1px solid #eaecf0;border-radius:10px;padding:12px 16px">'
            f'<div style="display:flex;flex-wrap:wrap;gap:12px">{body}</div>'
            f'<div style="font-size:12px;color:{GREY};margin-top:8px">Assumes recurring items continue at this month\'s amount '
            'and timing differences reverse within the year. Unexplained variance is not projected. '
            '+ = above plan (unfavorable). INR.</div></div>')


def commentary_html(pack, reply, issues, model, replaced=0):
    t, s, basis = pack['totals'], pack['summary'], pack['basis']
    written = {e['line_id']: e['commentary'] for e in reply['lines']}
    net_label, net_colour = _direction(t['direction'])
    material_abs = sum(abs(Decimal(l['variance'])) for l in pack['lines'] if l['material'])
    explained_share = int(Decimal(s['explained']) / material_abs * 100) if material_abs else 0
    out = [f'<div style="{FONT};color:#101828;background:#f9fafb;padding:20px;border-radius:12px;max-width:980px">']
    if issues:
        out.append(f'<div style="padding:12px 14px;border-radius:10px;background:{TINT[RED]};color:{RED};font-weight:600;margin-bottom:14px">'
                   f'✖ FAILED NUMBER CHECK. Do not use. {escape("; ".join(issues))}</div>')
    profit = pack.get('kind') == 'pl' and pack.get('pl')
    out.append(f'<div style="font-size:24px;font-weight:800">CFO commentary</div>'
               f'<div style="color:{GREY};margin:2px 0 16px">{escape(pack["period"])} · {"P&amp;L" if profit else "Expenses"} vs '
               f'{escape(basis)} · {escape(pack["scope"])} · {escape(pack["currency"])}</div>')
    if profit:
        rows = {e['label']: e for e in pack['pl']}

        def pl_tile(name):
            e, margin = rows[name], rows.get(f'{name} %')
            label, colour = _direction(e['direction'])
            sub = f"{label} · {fmt.signed(e['impact'])}" + (f" · {Decimal(margin['actual_pct']):.1f}% margin" if margin else '')
            return _tile(name, ('−' if Decimal(e['actual']) < 0 else '') + _inr(e['actual'], 0), sub, colour)
        headline_tiles = [pl_tile(n) for n in ('Sales', 'EBITDA', 'Net profit') if n in rows]
    else:
        headline_tiles = [_tile('Actual', _inr(t['actual'], 0)), _tile(basis.title(), _inr(t['baseline'], 0)),
                          _tile('Net variance', _inr(t['variance'], 0),
                                f"{net_label}" + (f" · {pct_text(t)}" if t['pct'] is not None else ''), net_colour)]
    out.append('<div style="display:flex;flex-wrap:wrap;gap:10px">' + ''.join(headline_tiles + [
        _tile('Explained', f'{explained_share}%', f"{_inr(s['explained'], 0)} of {_inr(material_abs, 0)} material"),
        _tile('Open items', str(s['open_items']), f"{_inr(s['unexplained'], 0)} unexplained", AMBER if s['open_items'] else GREEN),
    ]) + '</div>')
    out.append(_section('Executive summary'))
    out.append(f'<div style="background:{TINT[BLUE]};border:1px solid {BLUE}33;border-radius:10px;padding:12px 16px">'
               '<ul style="margin:0;padding-left:20px;line-height:1.7;font-size:14px">'
               + ''.join(f'<li>{escape(b)}</li>' for b in reply['executive_summary']) + '</ul></div>')
    if profit:
        out.append(_section('Profit and loss'))
        out.append(pl_table_html(pack['pl'], basis))
    if pack.get('grain'):
        out.append(_section('Where the variance sits'))
        out.append(heatmap_html(pack))
    out.append(_section(f"Lines needing explanation ({s['material']})"))
    for group, members in grouped_lines(pack):
        if group:
            out.append(_group_header(group))
        for line_id, line in members:
            text = written.get(line_id, 'Commentary missing.') if line['note'] else 'Explanation pending from the owner. No cause is assumed.'
            out.append(_card(line, text, grouped=group is not None))
    opens = [(l, open_reason(l)) for _, l in material_lines(pack) if open_reason(l)]
    def open_row(line, why):
        who = f' · 👤 {escape(owner(line))}' if line.get('attrs', {}).get('owner') else ''
        return (f'<div style="display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px dashed {AMBER}44;font-size:14px">'
                f'<span>🟠 <b>{escape(line.get("label", line["country"] + " | " + line["account"]))}</b> · {escape(why)}{who}</span>'
                f'<span style="font-weight:700;color:{AMBER};white-space:nowrap">{escape(_inr(line["unexplained"]))}</span></div>')

    if opens:
        out.append(_section(f'Open items ({len(opens)})', AMBER))
        out.append(f'<div style="background:{TINT[AMBER]};border:1px solid {AMBER}33;border-radius:10px;padding:6px 16px">'
                   + ''.join(open_row(l, why) for l, why in opens) + '</div>')
    out.append(full_year_html(pack.get('outlook')))
    actions = [l for _, l in material_lines(pack) if l['note'] and l['note']['action']]
    if actions:
        out.append(_section('Actions proposed by reviewers', GREEN))
        out.append('<div style="background:#fff;border:1px solid #eaecf0;border-radius:10px;padding:6px 16px">' + ''.join(
            f'<div style="padding:7px 0;font-size:14px">☐ <b>{escape(l.get("label", l["country"] + " | " + l["account"]))}</b>: '
            f'{escape(l["note"]["action"])}</div>' for l in actions) + '</div>')
    check = (f'<span style="color:{GREEN};font-weight:700">✔ Tick-and-tie passed</span>' if not issues
             else f'<span style="color:{RED};font-weight:700">✖ Tick-and-tie failed</span>')
    from agents.writer import source_note  # late import: the writer imports this module
    out.append(f'<div style="margin-top:18px;font-size:12px;color:{GREY};line-height:1.6">{check} · Every figure checked against '
               f'locked pack <code>{escape(pack["content_hash"][:12])}</code> · {escape(source_note(model, replaced))} · '
               'Explanations are reviewer-supplied. Review before use.</div></div>')
    return ''.join(out)


def html_document(body, title):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)}</title><style>body{{margin:24px;background:#fff}}@media print{{body{{margin:0}}}}</style>'
            f'</head><body>{body}</body></html>')
