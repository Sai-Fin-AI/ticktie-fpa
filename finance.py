"""Variance engine for expenses or a full P&L. Monetary calculations use decimal arithmetic.

A file always has month, country, account, currency, actual, budget and forecast. It may also have a
cost_center column, cost centre details (cc_name, function, owner), a pl_line column and any number of
extra dimension columns such as project or region. The user chooses which dimensions to analyse by;
rows are then summed to that level.

Every account sits on one P&L line. Sales above plan is favorable; every other line is a cost, so
spend above plan is unfavorable. Without a pl_line column the line is read from the account name.
"""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

REQUIRED = ('month', 'country', 'account', 'currency', 'actual', 'budget', 'forecast')
FIELDS = REQUIRED
AMOUNTS = ('actual', 'budget', 'forecast')
CC = 'cost_center'
CC_ATTRS = ('cc_name', 'function', 'owner')
ALIASES = {'cost_centre': CC, 'cost_center_code': CC, 'cost_centre_code': CC, 'cc_code': CC,
           'cost_center_name': 'cc_name', 'cost_centre_name': 'cc_name', 'cc_owner': 'owner'}
DIM_LABELS = {CC: 'Cost centre', 'country': 'Country', 'account': 'Account', 'function': 'Function'}
SALES, OPEX = 'Sales', 'Operating expenses'
PL_LINES = (SALES, 'Cost of sales', OPEX, 'Depreciation and amortisation', 'Interest', 'Tax')
# (subtotal, number of P&L lines above it, show a margin %)
SUBTOTALS = (('Gross margin', 2, True), ('EBITDA', 3, True), ('EBIT', 4, False), ('Profit before tax', 5, False),
             ('Net profit', 6, True))
MARGIN_NAMES = {'Gross margin %': 'Gross margin', 'EBITDA %': 'EBITDA margin', 'Net profit %': 'Net margin'}
PL_ALIASES = {'revenue': SALES, 'sales': SALES, 'turnover': SALES, 'income': SALES, 'cogs': 'Cost of sales',
              'cost of goods sold': 'Cost of sales', 'cost of revenue': 'Cost of sales', 'direct costs': 'Cost of sales',
              'opex': OPEX, 'operating expense': OPEX, 'expenses': OPEX, 'overheads': OPEX,
              'depreciation': 'Depreciation and amortisation', 'd&a': 'Depreciation and amortisation',
              'amortisation': 'Depreciation and amortisation', 'amortization': 'Depreciation and amortisation',
              'finance costs': 'Interest', 'interest expense': 'Interest', 'income tax': 'Tax', 'taxation': 'Tax'}
ACCOUNT_RULES = (  # how an account name places itself on the P&L when the file has no pl_line column
    (r'^(?:(?:net|gross|product|service|services|export|domestic|online|retail|wholesale|total)\s+)*'
     r'(?:sales|revenue|revenues|turnover)$', SALES),
    (r'\b(?:cost of (?:sales|goods|revenue)|cogs|direct materials?)\b', 'Cost of sales'),
    (r'\b(?:depreciation|amorti[sz]ation)\b', 'Depreciation and amortisation'),
    (r'\b(?:interest|finance costs?)\b', 'Interest'),
    (r'^(?:income tax|corporate tax|corporation tax|tax expense|taxation|tax)$', 'Tax'),
)


def pl_line_for(account, default=OPEX):
    name = ' '.join(account.lower().split())
    for pattern, line in ACCOUNT_RULES:
        if re.search(pattern, name):
            return line
    return default


def pl_line_value(text):
    """A pl_line cell as one of PL_LINES, accepting common names; None if it is not recognised."""
    key = ' '.join(str(text).lower().split())
    return next((line for line in PL_LINES if line.lower() == key), None) or PL_ALIASES.get(key)


def dim_label(name):
    return DIM_LABELS.get(name, name.replace('_', ' ').title())


class Dataset(list):
    """Validated rows, plus which optional dimensions they carry and the cost centre details."""

    def __init__(self, rows, dims=(), attrs=None):
        super().__init__(rows)
        self.dims = tuple(dims)
        self.attrs = attrs or {}


def _column(header):
    name = re.sub(r'[\s\-]+', '_', header.strip().lower())
    return ALIASES.get(name, name)


def _header(reader):
    try:
        names = [_column(h) for h in next(reader)]
    except StopIteration:
        raise ValueError('The file is empty.') from None
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError('Each column name may appear once. Repeated: ' + ', '.join(duplicates))
    return names


def load_csv(text, default_line=OPEX):
    """default_line: the P&L line for accounts whose name does not say (no pl_line column)."""
    reader = csv.reader(io.StringIO(text.lstrip('﻿')))
    names = _header(reader)
    missing = [c for c in REQUIRED if c not in names]
    if missing:
        raise ValueError('Missing columns: ' + ', '.join(missing) + '. Required columns (any order): ' + ', '.join(REQUIRED) + '.')
    attr_cols = [c for c in CC_ATTRS if c in names]
    if attr_cols and CC not in names:
        raise ValueError(', '.join(attr_cols) + ' describe a cost centre, so the file also needs a cost_center column.')
    extras = [c for c in names if c not in REQUIRED and c != CC and c not in CC_ATTRS and c != 'pl_line']
    for name in extras:
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', name):
            raise ValueError(f'Column "{name}": extra column names must start with a letter and use letters, numbers or spaces.')
    dims = ([CC] if CC in names else []) + extras
    rows, seen, attrs, lines_of = [], set(), {}, {}
    for line, values in enumerate(reader, 2):
        if not values:
            continue
        if len(values) != len(names) or any(not v.strip() for v in values):
            raise ValueError(f'Row {line}: missing value or extra column. No values were assumed.')
        raw = dict(zip(names, (v.strip() for v in values)))
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', raw['month']):
            raise ValueError(f'Row {line}: month must be YYYY-MM.')
        if raw['currency'] != 'INR':
            raise ValueError(f'Row {line}: version 1 requires INR reporting amounts.')
        key = (raw['month'], raw['country'], *(raw[d] for d in dims), raw['account'], raw['currency'])
        if key in seen:
            raise ValueError(f"Row {line}: duplicate month/country/{''.join(d + '/' for d in dims)}account/currency.")
        seen.add(key)
        row = {k: raw[k] for k in ('month', 'country', 'account', 'currency', *dims)}
        for field in AMOUNTS:
            try:
                value = Decimal(raw[field])
                if not value.is_finite() or value < 0 or value != value.quantize(Decimal('.01')):
                    raise InvalidOperation
            except InvalidOperation:
                raise ValueError(f'Row {line}: {field} must be a nonnegative number with at most 2 decimals.') from None
            row[field] = value
        if CC in dims:
            info = {'country': raw['country'], **{a: raw[a] for a in attr_cols}}
            if attrs.setdefault(raw[CC], info) != info:
                raise ValueError(f'Row {line}: cost centre {raw[CC]} has a different country, name, function or owner '
                                 'than an earlier row. A cost centre belongs to one country.')
        if 'pl_line' in raw:
            line_name = pl_line_value(raw['pl_line'])
            if line_name is None:
                raise ValueError(f'Row {line}: P&L line "{raw["pl_line"]}" is not one of: ' + ', '.join(PL_LINES) + '.')
        else:
            line_name = pl_line_for(raw['account'], default_line)
        if lines_of.setdefault(raw['account'], line_name) != line_name:
            raise ValueError(f'Row {line}: account {raw["account"]} is on two P&L lines. An account belongs to one line.')
        row['pl_line'] = line_name
        row['dims'] = tuple(dims)
        rows.append(row)
    if not rows:
        raise ValueError('The file has no data rows.')
    if len({r['month'] for r in rows}) != 1:
        raise ValueError('Version 1 accepts one reporting month per file.')
    return Dataset(rows, dims, attrs)


def load_cc_master(text):
    """Cost centre list: cost_center plus any of country, cc_name, function, owner. Other columns are ignored."""
    reader = csv.reader(io.StringIO(text.lstrip('﻿')))
    names = _header(reader)
    if CC not in names:
        raise ValueError('The cost centre master needs a cost_center column.')
    used = [c for c in ('country',) + CC_ATTRS if c in names]
    master = {}
    for line, values in enumerate(reader, 2):
        if not values:
            continue
        raw = dict(zip(names, (v.strip() for v in values)))
        if len(values) != len(names) or not raw[CC] or any(not raw[c] for c in used):
            raise ValueError(f'Cost centre master row {line}: missing value or extra column.')
        if raw[CC] in master:
            raise ValueError(f'Cost centre master row {line}: {raw[CC]} is listed twice.')
        master[raw[CC]] = {c: raw[c] for c in used}
    if not master:
        raise ValueError('The cost centre master has no rows.')
    return master


def apply_master(dataset, master):
    """Return (dataset with master details, completeness warnings). Unknown or contradicting cost centres stop."""
    if CC not in dataset.dims:
        raise ValueError('The data has no cost_center column, so a cost centre master cannot be applied.')
    unknown = sorted(set(dataset.attrs) - set(master))
    if unknown:
        raise ValueError('Cost centres missing from the master: ' + ', '.join(unknown) + '.')
    merged = {}
    for code, info in dataset.attrs.items():
        clash = [k for k, v in master[code].items() if k in info and info[k] != v]
        if clash:
            raise ValueError(f'Cost centre {code}: the data and the master disagree on {", ".join(clash)}.')
        merged[code] = {**info, **master[code]}
    unused = sorted(set(master) - set(dataset.attrs))
    warnings = ['Completeness check: no rows for cost centres ' + ', '.join(unused) + ' listed in the master.'] if unused else []
    return Dataset(dataset, dataset.dims, merged), warnings


def merge_datasets(main, extra, name='The sales and P&L file'):
    """Add a second file (for example sales and other P&L lines) to the main expense data.

    Both files must cover the same month and the same levels (cost centre, extra dimensions); a line may
    appear in only one file, an account sits on one P&L line, and a cost centre keeps one set of details.
    """
    if tuple(main.dims) != tuple(extra.dims):
        def show(dims):
            return ', '.join(dim_label(d) for d in dims) or 'no extra levels'
        raise ValueError(f'{name} must have the same levels as the main file ({show(main.dims)}); it has {show(extra.dims)}.')
    if main[0]['month'] != extra[0]['month']:
        raise ValueError(f"{name} is for {extra[0]['month']}, but the main file is for {main[0]['month']}.")
    key = lambda r: (r['country'], *(r[d] for d in main.dims), r['account'])
    clash = sorted({' | '.join(key(r)) for r in extra} & {' | '.join(key(r)) for r in main})
    if clash:
        raise ValueError(f'{name} repeats lines that are already in the main file: ' + '; '.join(clash[:5]) + '.')
    lines_of = {r['account']: r['pl_line'] for r in main}
    for r in extra:
        if lines_of.setdefault(r['account'], r['pl_line']) != r['pl_line']:
            raise ValueError(f"Account {r['account']} is on different P&L lines in the two files.")
    attrs = {code: dict(info) for code, info in main.attrs.items()}
    for code, info in extra.attrs.items():
        known = attrs.setdefault(code, {})
        clash = [k for k, v in info.items() if k in known and known[k] != v]
        if clash:
            raise ValueError(f'Cost centre {code}: the two files disagree on {", ".join(clash)}.')
        known.update(info)
    return Dataset(list(main) + list(extra), main.dims, attrs)


def aggregate(rows, dims):
    """Sum rows to month/country/<dims>/account. Cost centre details travel with the cost centre."""
    dims = tuple(dims)
    attrs = getattr(rows, 'attrs', {}) if CC in dims else {}
    groups = {}
    for r in rows:
        key = (r['month'], r['currency'], r['country'], *(r[d] for d in dims), r['account'])
        group = groups.get(key)
        if group is None:
            group = groups[key] = {'month': r['month'], 'country': r['country'], 'account': r['account'],
                                   'pl_line': r.get('pl_line', OPEX), 'currency': r['currency'], **{d: r[d] for d in dims},
                                   **{a: Decimal(0) for a in AMOUNTS}, 'dims': dims}
            if CC in dims:
                group.update({k: v for k, v in attrs.get(r[CC], {}).items() if k in CC_ATTRS})
        for a in AMOUNTS:
            group[a] += r[a]
    return Dataset(groups.values(), dims, attrs)


def line_label(row):
    parts = [row['country']]
    for d in row.get('dims', ()):
        parts.append(f"{row[d]} {row.get('cc_name', '')}".strip() if d == CC else str(row[d]))
    return ' | '.join(parts + [row['account']])


def is_sales(row):
    return row.get('pl_line') == SALES


def compare(actual, baseline, amount_threshold=Decimal('50000'), pct_threshold=Decimal('5'), higher_is_better=False):
    """higher_is_better: True for sales and profit, False for costs."""
    if amount_threshold < 0 or pct_threshold < 0:
        raise ValueError('Thresholds cannot be negative.')
    variance = actual - baseline
    pct = variance / abs(baseline) * 100 if baseline else None
    material = variance != 0 and abs(variance) >= amount_threshold and (pct is None or abs(pct) >= pct_threshold)
    good = variance > 0 if higher_is_better else variance < 0
    return {'variance': variance, 'pct': pct, 'material': material,
            'direction': 'On plan' if not variance else 'Favorable' if good else 'Unfavorable'}


def analyze(rows, baseline='budget', amount_threshold=Decimal('50000'), pct_threshold=Decimal('5')):
    if baseline not in ('budget', 'forecast'):
        raise ValueError('Choose budget or forecast.')
    return sorted([{**row, **compare(row['actual'], row[baseline], amount_threshold, pct_threshold, is_sales(row))}
                   for row in rows], key=lambda r: abs(r['variance']), reverse=True)


def review_lines(rows, baseline, line_thresholds, total_thresholds=None):
    """Lines that need an explanation.

    A line qualifies on its own thresholds. When lines are finer than country x account (for example
    by cost centre), an account total that is material while none of its lines is ("spread" variance)
    brings every moving line in that account into scope, so the owners explain their share.
    """
    results = analyze(rows, baseline, *line_thresholds)
    for r in results:
        r['scope'] = 'Line' if r['material'] else None
    if total_thresholds and any(r.get('dims') for r in results):
        groups = {}
        for r in results:
            groups.setdefault((r['country'], r['account']), []).append(r)
        for members in groups.values():
            total = compare(sum(m['actual'] for m in members), sum(m[baseline] for m in members), *total_thresholds,
                            is_sales(members[0]))
            if total['material'] and not any(m['material'] for m in members):
                for m in members:
                    if m['variance']:
                        m['material'], m['scope'] = True, 'Account total'
    return results


def commentary(row, baseline):
    prefix = f"{line_label(row)} | {row['month']}: "
    noun = 'Revenue' if is_sales(row) else 'Expense'
    if not row['variance']:
        return prefix + f"{noun} equals {baseline} at INR {row['actual']:,.2f}."
    pct = f"{abs(row['pct']):.1f}%" if row['pct'] is not None else 'percentage N/A; zero baseline'
    return (prefix + f"{noun} is INR {abs(row['variance']):,.2f} "
            f"{'above' if row['variance'] > 0 else 'below'} {baseline} "
            f"({pct}; {row['direction'].lower()}). "
            'The input does not establish the cause. Confirm the explanation with the account owner.')


def has_sales(rows):
    return any(is_sales(r) for r in rows)


def statement(rows, baseline, actual_key='actual'):
    """The P&L from Sales to Net profit, or [] when the data has no sales (an expense-only review).

    Each entry: kind ('line' | 'account' | 'subtotal' | 'margin'), label, actual, baseline, variance (actual
    minus baseline), impact (effect on profit: + helps), pct, direction. Margin rows carry percentages and
    the change in percentage points. Account rows are listed under a line that has several accounts.
    """
    if not has_sales(rows):
        return []
    lines = {}
    for r in rows:
        account = lines.setdefault(r.get('pl_line', OPEX), {}).setdefault(r['account'], [Decimal(0), Decimal(0)])
        account[0] += r[actual_key]
        account[1] += r[baseline]
    sales = [sum(v[0] for v in lines.get(SALES, {}).values()), sum(v[1] for v in lines.get(SALES, {}).values())]

    def entry(kind, label, actual, plan, sign, line=None):
        variance = actual - plan
        result = compare(actual, plan, Decimal(0), Decimal(0), sign > 0)
        return {'kind': kind, 'label': label, 'pl_line': line, 'actual': actual, 'baseline': plan, 'variance': variance,
                'impact': variance * sign if variance else Decimal(0), 'pct': result['pct'], 'direction': result['direction']}

    out, profit = [], [Decimal(0), Decimal(0)]
    for position, name in enumerate(PL_LINES, 1):
        accounts = lines.get(name, {})
        if accounts:
            sign = 1 if name == SALES else -1
            total = [sum(v[0] for v in accounts.values()), sum(v[1] for v in accounts.values())]
            out.append(entry('line', name, *total, sign, name))
            if len(accounts) > 1:
                for account, (a, b) in sorted(accounts.items(), key=lambda kv: (-kv[1][1], kv[0])):
                    out.append(entry('account', account, a, b, sign, name))
            profit = [profit[0] + sign * total[0], profit[1] + sign * total[1]]
        for subtotal, after, margin in SUBTOTALS:
            if after == position and (accounts or subtotal == 'Net profit'):
                out.append(entry('subtotal', subtotal, *profit, 1))
                if margin and sales[0] and sales[1]:
                    a, b = profit[0] / sales[0] * 100, profit[1] / sales[1] * 100
                    out.append({'kind': 'margin', 'label': f'{subtotal} %', 'actual_pct': a, 'baseline_pct': b,
                                'pts': a - b, 'direction': 'Favorable' if a > b else 'Unfavorable' if a < b else 'On plan'})
    return out


BRIDGES = ('EBITDA', 'Net profit', 'Gross margin', 'Sales', 'Costs')


def bridge_options(rows):
    return list(BRIDGES) if has_sales(rows) else ['Costs']


def bridge(rows, baseline, measure, actual_key='actual'):
    """Budget-to-actual walk for one measure, account by account.

    Sales and Costs walk in their own terms (more sales is good, more cost is bad). Gross margin, EBITDA
    and Net profit walk in profit terms, so every step is its effect on that margin.
    Returns {'measure', 'start', 'end', 'steps': [(account, delta, favorable)], 'higher_is_better'}.
    """
    scope = {'Sales': (SALES,), 'Costs': PL_LINES[1:], 'Gross margin': PL_LINES[:2], 'EBITDA': PL_LINES[:3],
             'EBIT': PL_LINES[:4], 'Net profit': PL_LINES}[measure]
    in_scope = [r for r in rows if r.get('pl_line', OPEX) in scope]
    profit_terms = measure not in ('Sales', 'Costs')
    sign = lambda r: (1 if is_sales(r) else -1) if profit_terms else 1
    steps = {}
    for r in in_scope:
        steps[r['account']] = steps.get(r['account'], Decimal(0)) + sign(r) * (r[actual_key] - r[baseline])
    higher_is_better = measure != 'Costs'
    ordered = sorted(((a, v, (v > 0) == higher_is_better) for a, v in steps.items() if v), key=lambda s: abs(s[1]), reverse=True)
    return {'measure': measure, 'higher_is_better': higher_is_better, 'steps': ordered,
            'start': sum(sign(r) * r[baseline] for r in in_scope), 'end': sum(sign(r) * r[actual_key] for r in in_scope)}


# Synthetic teaching data. Cost centre lines add up to the same country x account totals as the
# account-level sample, so both views reconcile. All amounts are already translated into INR.
SAMPLE_LINES = (
    ('India', 'IN-SAL', 'Employee costs', 900000, 60000), ('India', 'IN-OPS', 'Employee costs', 600000, 20000),
    ('India', 'IN-COR', 'Employee costs', 500000, 20000), ('India', 'IN-SAL', 'Travel', 150000, -70000),
    ('India', 'IN-COR', 'Travel', 50000, 10000), ('India', 'IN-COR', 'Consulting', 0, 75000),
    ('India', 'IN-OPS', 'Warehousing', 800000, 0), ('India', 'IN-SAL', 'Distribution', 300000, -40000),
    ('India', 'IN-OPS', 'Distribution', 700000, 240000),
    ('Singapore', 'SG-SAL', 'Employee costs', 1200000, 90000), ('Singapore', 'SG-OPS', 'Employee costs', 800000, -90000),
    ('Singapore', 'SG-SAL', 'Travel', 200000, 10000), ('Singapore', 'SG-OPS', 'Warehousing', 800000, -80000),
    ('Singapore', 'SG-OPS', 'Distribution', 1000000, 40000),
    ('Australia', 'AU-SAL', 'Employee costs', 1000000, -150000), ('Australia', 'AU-OPS', 'Employee costs', 600000, 0),
    ('Australia', 'AU-COR', 'Employee costs', 400000, 0), ('Australia', 'AU-SAL', 'Travel', 150000, 40000),
    ('Australia', 'AU-COR', 'Travel', 50000, 10000), ('Australia', 'AU-COR', 'Consulting', 0, 55000),
    ('Australia', 'AU-OPS', 'Warehousing', 800000, 40000), ('Australia', 'AU-SAL', 'Distribution', 400000, 30000),
    ('Australia', 'AU-OPS', 'Distribution', 600000, 70000),
)
SAMPLE_CC = (
    ('IN-SAL', 'India', 'India Sales', 'Sales', 'Priya Nair'), ('IN-OPS', 'India', 'India Operations', 'Operations', 'Arjun Mehta'),
    ('IN-COR', 'India', 'India Corporate', 'Corporate', 'Kavya Rao'), ('SG-SAL', 'Singapore', 'Singapore Sales', 'Sales', 'Daniel Tan'),
    ('SG-OPS', 'Singapore', 'Singapore Operations', 'Operations', 'Mei Lin'),
    ('SG-COR', 'Singapore', 'Singapore Corporate', 'Corporate', 'Hui Min Goh'),
    ('AU-SAL', 'Australia', 'Australia Sales', 'Sales', 'Olivia Brown'),
    ('AU-OPS', 'Australia', 'Australia Operations', 'Operations', 'Jack Wilson'),
    ('AU-COR', 'Australia', 'Australia Corporate', 'Corporate', 'Sophie Taylor'),
)


def _csv(header, rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue()


def sample_csv(level='cost_center'):
    """Fictional August 2026 data. level='account' gives the original 15-row country x account file."""
    if level == 'account':
        countries = ('India', 'Singapore', 'Australia')
        accounts = ('Employee costs', 'Travel', 'Consulting', 'Warehousing', 'Distribution')
        budgets = (2000000, 200000, 0, 800000, 1000000)
        movements = ((100000, -60000, 75000, 0, 200000),
                     (0, 10000, 0, -80000, 40000),
                     (-150000, 50000, 55000, 40000, 100000))
        return _csv(FIELDS, [('2026-08', country, account, 'INR', budget + change, budget, budget + (change // 2))
                             for country, changes in zip(countries, movements)
                             for account, budget, change in zip(accounts, budgets, changes)])
    return _csv(('month', 'country', CC, 'account', 'currency', 'actual', 'budget', 'forecast'),
                [('2026-08', country, cc, account, 'INR', budget + change, budget, budget + change // 2)
                 for country, cc, account, budget, change in SAMPLE_LINES])


# The second demo file: sales and the P&L lines below and around operating expenses, on the same cost
# centres. Together with the expense sample it gives Sales 30.5m vs 30.0m down to Net profit 2.265m vs 2.475m.
# No line here is material at the demo thresholds, so the expense explanations stay the review's focus.
SAMPLE_PL_LINES = (
    ('India', 'IN-SAL', 'Product sales', 'Sales', 12000000, 300000),
    ('Singapore', 'SG-SAL', 'Product sales', 'Sales', 9000000, -200000),
    ('Australia', 'AU-SAL', 'Product sales', 'Sales', 9000000, 400000),
    ('India', 'IN-OPS', 'Cost of goods sold', 'Cost of sales', 5400000, 250000),
    ('Singapore', 'SG-OPS', 'Cost of goods sold', 'Cost of sales', 4050000, -50000),
    ('Australia', 'AU-OPS', 'Cost of goods sold', 'Cost of sales', 4050000, 200000),
    ('India', 'IN-COR', 'Depreciation', 'Depreciation and amortisation', 300000, 0),
    ('Singapore', 'SG-OPS', 'Depreciation', 'Depreciation and amortisation', 300000, 0),
    ('Australia', 'AU-COR', 'Depreciation', 'Depreciation and amortisation', 300000, 0),
    ('India', 'IN-COR', 'Interest expense', 'Interest', 100000, 0),
    ('Singapore', 'SG-OPS', 'Interest expense', 'Interest', 100000, 0),
    ('Australia', 'AU-COR', 'Interest expense', 'Interest', 100000, 0),
    ('India', 'IN-COR', 'Income tax', 'Tax', 300000, -24000),
    ('Singapore', 'SG-OPS', 'Income tax', 'Tax', 250000, -23000),
    ('Australia', 'AU-COR', 'Income tax', 'Tax', 275000, -23000),
)


def sample_pl_csv():
    """Fictional August 2026 sales and other P&L lines, with an explicit pl_line column."""
    return _csv(('month', 'country', CC, 'account', 'pl_line', 'currency', 'actual', 'budget', 'forecast'),
                [('2026-08', country, cc, account, line, 'INR', budget + change, budget, budget + change // 2)
                 for country, cc, account, line, budget, change in SAMPLE_PL_LINES])


def sample_cc_master_csv():
    """Includes SG-COR, which has no August rows, to show the completeness check."""
    return _csv((CC, 'country', 'cc_name', 'function', 'owner'), SAMPLE_CC)
