"""CFO commentary as a styled page: headline tiles, colour-coded cards and coverage bars.

Colour is never the only signal: every badge also carries an arrow or icon and a word, so the
page reads in black-and-white print and for colour-blind readers. All text is HTML-escaped.
"""
from decimal import Decimal
from html import escape

from agents import material_lines, pct_text
from pack import open_reason

RED, GREEN, AMBER, GREY, BLUE = '#b42318', '#027a48', '#b54708', '#667085', '#175cd3'
TINT = {RED: '#fef3f2', GREEN: '#ecfdf3', AMBER: '#fffaeb', GREY: '#f2f4f7', BLUE: '#eff8ff'}
STATUS = {'Confirmed by reviewer': ('✅', 'Confirmed', GREEN), 'Unconfirmed': ('🟡', 'Unconfirmed', AMBER),
          'Conflicting explanations': ('🔴', 'Conflicting', RED), None: ('⚪', 'Explanation pending', GREY)}
TIMING = {'One-off': '① One-off', 'Recurring': '↻ Recurring', 'Timing difference': '⇄ Timing difference', 'Unknown': '? Timing unknown'}
FONT = "font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _inr(value, places=2):
    return f'INR {abs(Decimal(value)):,.{places}f}'


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


def _card(line, text):
    label, colour = _direction(line['direction'])
    icon, status, status_colour = STATUS[line['note']['status'] if line['note'] else None]
    chips = _chip(f'{label} · {pct_text(line)}', colour) + _chip(f'{icon} {status}', status_colour)
    if line['note']:
        chips += _chip(TIMING[line['note']['timing']], BLUE if line['note']['timing'] != 'Unknown' else GREY)
    return (f'<div style="border:1px solid #eaecf0;border-left:5px solid {colour};border-radius:10px;padding:14px 16px;'
            f'margin:0 0 12px;background:#fff">'
            f'<div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:baseline">'
            f'<div style="font-size:15px;font-weight:700">{escape(line["country"])} <span style="color:{GREY}">|</span> '
            f'{escape(line["account"])}</div>'
            f'<div style="font-size:15px;font-weight:700;color:{colour}">{escape(_inr(line["variance"]))}</div></div>'
            f'<div style="margin-top:8px">{chips}</div>'
            f'<div style="font-size:14px;line-height:1.55;color:#344054;margin-top:4px">{escape(text)}</div>'
            f'{_coverage_bar(line)}</div>')


def _section(title, colour=BLUE):
    return (f'<div style="font-size:13px;font-weight:700;color:{colour};text-transform:uppercase;letter-spacing:.06em;'
            f'margin:22px 0 10px">{escape(title)}</div>')


def commentary_html(pack, reply, issues, model):
    t, s, basis = pack['totals'], pack['summary'], pack['basis']
    written = {e['line_id']: e['commentary'] for e in reply['lines']}
    net_label, net_colour = _direction(t['direction'])
    material_abs = sum(abs(Decimal(l['variance'])) for l in pack['lines'] if l['material'])
    explained_share = int(Decimal(s['explained']) / material_abs * 100) if material_abs else 0
    out = [f'<div style="{FONT};color:#101828;background:#f9fafb;padding:20px;border-radius:12px;max-width:980px">']
    if issues:
        out.append(f'<div style="padding:12px 14px;border-radius:10px;background:{TINT[RED]};color:{RED};font-weight:600;margin-bottom:14px">'
                   f'✖ FAILED NUMBER CHECK. Do not use. {escape("; ".join(issues))}</div>')
    out.append(f'<div style="font-size:24px;font-weight:800">CFO commentary</div>'
               f'<div style="color:{GREY};margin:2px 0 16px">{escape(pack["period"])} · Expenses vs {escape(basis)} · '
               f'{escape(pack["scope"])} · {escape(pack["currency"])}</div>')
    out.append('<div style="display:flex;flex-wrap:wrap;gap:10px">' + ''.join([
        _tile('Actual', _inr(t['actual'], 0)),
        _tile(basis.title(), _inr(t['baseline'], 0)),
        _tile('Net variance', _inr(t['variance'], 0), f"{net_label}" + (f" · {pct_text(t)}" if t['pct'] is not None else ''), net_colour),
        _tile('Explained', f'{explained_share}%', f"{_inr(s['explained'], 0)} of {_inr(material_abs, 0)} material"),
        _tile('Open items', str(s['open_items']), f"{_inr(s['unexplained'], 0)} unexplained", AMBER if s['open_items'] else GREEN),
    ]) + '</div>')
    out.append(_section('Executive summary'))
    out.append(f'<div style="background:{TINT[BLUE]};border:1px solid {BLUE}33;border-radius:10px;padding:12px 16px">'
               '<ul style="margin:0;padding-left:20px;line-height:1.7;font-size:14px">'
               + ''.join(f'<li>{escape(b)}</li>' for b in reply['executive_summary']) + '</ul></div>')
    out.append(_section(f"Material movements ({s['material']})"))
    for line_id, line in material_lines(pack):
        text = written.get(line_id, 'Commentary missing.') if line['note'] else 'Explanation pending from the account owner. No cause is assumed.'
        out.append(_card(line, text))
    opens = [(l, open_reason(l)) for _, l in material_lines(pack) if open_reason(l)]
    if opens:
        out.append(_section(f'Open items ({len(opens)})', AMBER))
        out.append(f'<div style="background:{TINT[AMBER]};border:1px solid {AMBER}33;border-radius:10px;padding:6px 16px">' + ''.join(
            f'<div style="display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px dashed {AMBER}44;font-size:14px">'
            f'<span>🟠 <b>{escape(l["country"])} | {escape(l["account"])}</b> · {escape(why)}</span>'
            f'<span style="font-weight:700;color:{AMBER};white-space:nowrap">{escape(_inr(l["unexplained"]))}</span></div>'
            for l, why in opens) + '</div>')
    actions = [l for _, l in material_lines(pack) if l['note'] and l['note']['action']]
    if actions:
        out.append(_section('Actions proposed by reviewers', GREEN))
        out.append('<div style="background:#fff;border:1px solid #eaecf0;border-radius:10px;padding:6px 16px">' + ''.join(
            f'<div style="padding:7px 0;font-size:14px">☐ <b>{escape(l["country"])} | {escape(l["account"])}</b>: '
            f'{escape(l["note"]["action"])}</div>' for l in actions) + '</div>')
    check = (f'<span style="color:{GREEN};font-weight:700">✔ Tick-and-tie passed</span>' if not issues
             else f'<span style="color:{RED};font-weight:700">✖ Tick-and-tie failed</span>')
    out.append(f'<div style="margin-top:18px;font-size:12px;color:{GREY};line-height:1.6">{check} · Every figure checked against '
               f'locked pack <code>{escape(pack["content_hash"][:12])}</code> · Drafted by AI ({escape(model)}) · '
               'Explanations are reviewer-supplied. Review before use.</div></div>')
    return ''.join(out)


def html_document(body, title):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)}</title><style>body{{margin:24px;background:#fff}}@media print{{body{{margin:0}}}}</style>'
            f'</head><body>{body}</body></html>')
