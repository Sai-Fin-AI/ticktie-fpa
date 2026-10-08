"""Expense-only variance engine. Monetary calculations use decimal arithmetic.

A file always has month, country, account, currency, actual, budget and forecast. It may also have a
cost_center column, cost centre details (cc_name, function, owner) and any number of extra dimension
columns such as project or region. The user chooses which dimensions to analyse by; rows are then
summed to that level.
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


def load_csv(text):
    reader = csv.reader(io.StringIO(text.lstrip('﻿')))
    names = _header(reader)
    missing = [c for c in REQUIRED if c not in names]
    if missing:
        raise ValueError('Missing columns: ' + ', '.join(missing) + '. Required columns (any order): ' + ', '.join(REQUIRED) + '.')
    attr_cols = [c for c in CC_ATTRS if c in names]
    if attr_cols and CC not in names:
        raise ValueError(', '.join(attr_cols) + ' describe a cost centre, so the file also needs a cost_center column.')
    extras = [c for c in names if c not in REQUIRED and c != CC and c not in CC_ATTRS]
    for name in extras:
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', name):
            raise ValueError(f'Column "{name}": extra column names must start with a letter and use letters, numbers or spaces.')
    dims = ([CC] if CC in names else []) + extras
    rows, seen, attrs = [], set(), {}
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
                                   'currency': r['currency'], **{d: r[d] for d in dims},
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


def compare(actual, baseline, amount_threshold=Decimal('50000'), pct_threshold=Decimal('5')):
    if amount_threshold < 0 or pct_threshold < 0:
        raise ValueError('Thresholds cannot be negative.')
    variance = actual - baseline
    pct = variance / baseline * 100 if baseline else None
    material = variance != 0 and abs(variance) >= amount_threshold and (pct is None or abs(pct) >= pct_threshold)
    return {'variance': variance, 'pct': pct, 'material': material,
            'direction': 'Unfavorable' if variance > 0 else 'Favorable' if variance < 0 else 'On plan'}


def analyze(rows, baseline='budget', amount_threshold=Decimal('50000'), pct_threshold=Decimal('5')):
    if baseline not in ('budget', 'forecast'):
        raise ValueError('Choose budget or forecast.')
    return sorted([{**row, **compare(row['actual'], row[baseline], amount_threshold, pct_threshold)}
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
            total = compare(sum(m['actual'] for m in members), sum(m[baseline] for m in members), *total_thresholds)
            if total['material'] and not any(m['material'] for m in members):
                for m in members:
                    if m['variance']:
                        m['material'], m['scope'] = True, 'Account total'
    return results


def commentary(row, baseline):
    prefix = f"{line_label(row)} | {row['month']}: "
    if not row['variance']:
        return prefix + f"Expense equals {baseline} at INR {row['actual']:,.2f}."
    pct = f"{abs(row['pct']):.1f}%" if row['pct'] is not None else 'percentage N/A; zero baseline'
    return (prefix + f"Expense is INR {abs(row['variance']):,.2f} "
            f"{'above' if row['variance'] > 0 else 'below'} {baseline} "
            f"({pct}; {row['direction'].lower()}). "
            'The input does not establish the cause. Confirm the explanation with the account owner.')


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


def sample_cc_master_csv():
    """Includes SG-COR, which has no August rows, to show the completeness check."""
    return _csv((CC, 'country', 'cc_name', 'function', 'owner'), SAMPLE_CC)
