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
# The responsibility centre a line was posted to: a cost centre for costs, a profit centre for sales and cost
# of sales. Both have an owner, so one level (stored as cost_center) carries either; a profit_center column
# is read into it. A file with both columns keeps the profit centre as an extra level.
CC = 'cost_center'
CC_ATTRS = ('cc_name', 'function', 'owner')
CENTRE_ALIASES = {'cost_centre', 'cost_center_code', 'cost_centre_code', 'cc_code', 'profit_center', 'profit_centre',
                  'profit_center_code', 'profit_centre_code', 'pc_code'}
# Product hierarchy, coarse to fine. Sales and cost of sales may come by SKU, by brand only, or by category;
# lines without a product (tax, a freight income line) leave these blank.
PRODUCT_LEVELS = ('category', 'brand', 'sku')
ALIASES = {**{a: CC for a in CENTRE_ALIASES},
           'cost_center_name': 'cc_name', 'cost_centre_name': 'cc_name', 'profit_center_name': 'cc_name',
           'profit_centre_name': 'cc_name', 'cc_owner': 'owner',
           'sku_code': 'sku', 'item_code': 'sku', 'product_code': 'sku', 'material': 'sku', 'material_code': 'sku',
           'brand_name': 'brand', 'product_category': 'category', 'product_group': 'category', 'category_name': 'category'}
DIM_LABELS = {CC: 'Cost / profit centre', 'country': 'Country', 'account': 'Account', 'function': 'Function',
              'sku': 'SKU', 'brand': 'Brand', 'category': 'Category'}
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


def _slug(header):
    return re.sub(r'[\s\-]+', '_', header.strip().lower())


def _column(header):
    name = _slug(header)
    return ALIASES.get(name, name)


def extra_name(header):
    """The level name for an extra column. A second centre column (profit centre next to a cost centre) keeps
    its own name instead of colliding with the centre."""
    name = _column(header)
    return _slug(header) if name == CC else name


def _header(reader):
    try:
        raw = next(reader)
    except StopIteration:
        raise ValueError('The file is empty.') from None
    names = [_column(h) for h in raw]
    if names.count(CC) > 1:  # cost centre and profit centre: the exact cost_center column (or the first) is the centre
        keep = next((i for i, h in enumerate(raw) if _slug(h) == CC), names.index(CC))
        names = [n if n != CC or i == keep else _slug(raw[i]) for i, n in enumerate(names)]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError('Each column name may appear once. Repeated: ' + ', '.join(duplicates))
    return names


def load_csv(text, default_line=OPEX):
    """default_line: the P&L line for accounts whose name does not say (no pl_line column).

    Amounts may be negative: a credit GL code, a reversal, a credit note. A line that nets below zero is
    flagged by review_lines and always needs an explanation.
    Product levels (sku, brand, category) may be blank on a line that has no product, such as tax; each
    row's 'dims' lists only the levels it carries.
    """
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
        if len(values) != len(names) or any(not v.strip() for n, v in zip(names, values) if n not in PRODUCT_LEVELS):
            raise ValueError(f'Row {line}: missing value or extra column. No values were assumed.')
        raw = dict(zip(names, (v.strip() for v in values)))
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', raw['month']):
            raise ValueError(f'Row {line}: month must be YYYY-MM.')
        if raw['currency'] != 'INR':
            raise ValueError(f'Row {line}: version 1 requires INR reporting amounts.')
        own = tuple(d for d in dims if raw[d])
        key = (raw['month'], raw['country'], *((d, raw[d]) for d in own), raw['account'], raw['currency'])
        if key in seen:
            raise ValueError(f"Row {line}: duplicate month/country/{''.join(d + '/' for d in own)}account/currency.")
        seen.add(key)
        row = {k: raw[k] for k in ('month', 'country', 'account', 'currency', *own)}
        for field in AMOUNTS:
            try:
                value = Decimal(raw[field])
                if not value.is_finite() or value != value.quantize(Decimal('.01')):
                    raise InvalidOperation
            except InvalidOperation:
                raise ValueError(f'Row {line}: {field} must be a number with at most 2 decimals.') from None
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
        row['dims'] = own
        rows.append(row)
    if not rows:
        raise ValueError('The file has no data rows.')
    if len({r['month'] for r in rows}) != 1:
        raise ValueError('Version 1 accepts one reporting month per file.')
    return Dataset(rows, dims, attrs)


def load_cc_master(text):
    """Cost and profit centre list: cost_center (or profit_center) plus any of country, cc_name, function, owner."""
    reader = csv.reader(io.StringIO(text.lstrip('﻿')))
    names = _header(reader)
    if CC not in names:
        raise ValueError('The centre master needs a cost_center (or profit_center) column.')
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
        raise ValueError('The data has no cost or profit centre column, so a centre master cannot be applied.')
    unknown = sorted(set(dataset.attrs) - set(master))
    if unknown:
        raise ValueError('Cost / profit centres missing from the master: ' + ', '.join(unknown) + '.')
    merged = {}
    for code, info in dataset.attrs.items():
        clash = [k for k, v in master[code].items() if k in info and info[k] != v]
        if clash:
            raise ValueError(f'Centre {code}: the data and the master disagree on {", ".join(clash)}.')
        merged[code] = {**info, **master[code]}
    unused = sorted(set(master) - set(dataset.attrs))
    warnings = ['Completeness check: no rows for centres ' + ', '.join(unused) + ' listed in the master.'] if unused else []
    return Dataset(dataset, dataset.dims, merged), warnings


# GL mapping: when ledgers post to many GL codes per reporting account (Travel = airfare, hotels, local
# conveyance ...), the data's account column holds GL codes and a mapping rolls them up to accounts.
GL = 'gl_code'
GL_COLUMNS = {'gl': GL, 'gl_account': GL, 'glcode': GL, 'gl_number': GL, 'gl_account_code': GL, 'gl_account_number': GL,
              'gl_description': 'gl_name', 'gl_account_name': 'gl_name', 'gl_account_description': 'gl_name',
              'description': 'gl_name', 'reporting_account': 'account', 'mapped_account': 'account',
              'account_name': 'account', 'group_account': 'account', 'account_group': 'account'}


class UnmappedGL(ValueError):
    """GL codes in the data that the mapping does not know. codes: the unknown ones; present: every code in the data."""

    def __init__(self, codes, present):
        self.codes, self.present = codes, present
        shown = ', '.join(codes[:10]) + (f' and {len(codes) - 10} more' if len(codes) > 10 else '')
        super().__init__(f'{len(codes)} GL code(s) in the data are not in the GL mapping: {shown}. Download the '
                         'mapping file below, fill in the account for each new code, and upload it again.')


def load_gl_map(text):
    """GL mapping: gl_code and account, plus optional gl_name and pl_line. Other columns are ignored.

    Returns {gl_code: {'account', 'gl_name', optional 'pl_line'}}. An account sits on one P&L line.
    """
    reader = csv.reader(io.StringIO(text.lstrip('﻿')))
    names = [GL_COLUMNS.get(n, n) for n in _header(reader)]
    if GL not in names or 'account' not in names:
        raise ValueError('The GL mapping needs a gl_code column and an account column (the reporting account each '
                         'GL code rolls up to).')
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise ValueError('The GL mapping has more than one column for ' + ', '.join(repeated) + '.')
    gl_map, lines_of = {}, {}
    for line, values in enumerate(reader, 2):
        if not any(v.strip() for v in values):
            continue
        if len(values) != len(names):
            raise ValueError(f'GL mapping row {line}: missing value or extra column.')
        raw = dict(zip(names, (v.strip() for v in values)))
        code, account = raw[GL], raw['account']
        if not code:
            raise ValueError(f'GL mapping row {line}: the gl_code is empty.')
        if not account:
            raise ValueError(f'GL mapping row {line}: GL {code} has no account. Fill in the account it rolls up to.')
        if code in gl_map:
            raise ValueError(f'GL mapping row {line}: GL {code} is listed twice.')
        entry = {'account': account, 'gl_name': raw.get('gl_name', '')}
        if raw.get('pl_line'):
            entry['pl_line'] = pl_line_value(raw['pl_line'])
            if entry['pl_line'] is None:
                raise ValueError(f'GL mapping row {line}: P&L line "{raw["pl_line"]}" is not one of: ' + ', '.join(PL_LINES) + '.')
            if lines_of.setdefault(account, entry['pl_line']) != entry['pl_line']:
                raise ValueError(f'GL mapping row {line}: account {account} is on two P&L lines. All GL codes of an '
                                 'account sit on one line.')
        gl_map[code] = entry
    if not gl_map:
        raise ValueError('The GL mapping has no rows.')
    return gl_map


def gl_resolver(gl_map):
    """GL code -> reporting account. A value that already is a reporting account (for example a budget kept at
    account level while actuals post to GL codes) stays as it is."""
    return lambda code: gl_map[code]['account'] if code in gl_map else code


def apply_gl_map(dataset, gl_map):
    """Roll the data's GL codes up to reporting accounts.

    Returns (dataset, summary). Rows keep their gl_code and gl_name, so aggregate() adds up the GL codes of an
    account and gl_lines() shows them. Unknown codes stop with UnmappedGL.
    """
    accounts = {e['account'] for e in gl_map.values()}
    present = sorted({r['account'] for r in dataset})
    unknown = [c for c in present if c not in gl_map and c not in accounts]
    if unknown:
        raise UnmappedGL(unknown, present)
    lines_of = {e['account']: e['pl_line'] for e in gl_map.values() if 'pl_line' in e}
    rows = []
    for r in dataset:
        entry = gl_map.get(r['account'], {'account': r['account'], 'gl_name': 'Booked at account level'})
        account = entry['account']
        line = entry.get('pl_line') or lines_of.get(account) or pl_line_for(account, r['pl_line'])
        if lines_of.setdefault(account, line) != line:
            raise ValueError(f'Account {account} gets GL codes from two P&L lines ({lines_of[account]} and {line}). '
                             'Add a pl_line column to the GL mapping so each account sits on one line.')
        rows.append({**r, 'account': account, 'pl_line': line, GL: r['account'], 'gl_name': entry['gl_name']})
    result = Dataset(rows, dataset.dims, dataset.attrs)
    used = [c for c in present if c in gl_map]
    return result, {'gl_codes': len(used), 'accounts': len({gl_map[c]['account'] for c in used}),
                    'unused': [c for c in gl_map if c not in set(present)]}


def gl_lines(dataset, dims, baseline):
    """Every GL code at the chosen level (country x dims x account x GL), largest variance first."""
    groups = {}
    for r in dataset:
        if GL not in r:
            continue
        own = own_dims(r, dims)
        key = (r['country'], *((d, r[d]) for d in own), r['account'], r[GL])
        g = groups.setdefault(key, {'country': r['country'], **{d: r[d] for d in own}, 'account': r['account'],
                                    'pl_line': r['pl_line'], GL: r[GL], 'gl_name': r['gl_name'], 'dims': own,
                                    'actual': Decimal(0), baseline: Decimal(0)})
        g['actual'] += r['actual']
        g[baseline] += r[baseline]
    out = [{**g, **compare(g['actual'], g[baseline], Decimal(0), Decimal(0), is_sales(g))} for g in groups.values()]
    return sorted(out, key=lambda g: (-abs(g['variance']), g['country'], g[GL]))


def same_line(row, line):
    """True when a finer row adds up into an analysed line (same country, account and levels)."""
    dims = line.get('dims', ())
    return (row['country'] == line['country'] and row['account'] == line['account']
            and own_dims(row, dims) == tuple(dims) and all(row[d] == line[d] for d in dims))


def gl_detail(dataset, line, baseline):
    """The GL codes inside one analysed line."""
    return [g for g in gl_lines(dataset, line.get('dims', ()), baseline) if same_line(g, line)]


def accounts_in(text):
    """The distinct account values of a standard-layout CSV, read without validation (for hints on errors)."""
    try:
        return sorted({r.get('account', '').strip() for r in csv.DictReader(io.StringIO(text.lstrip('﻿')))} - {''})
    except csv.Error:
        return []


def looks_like_gl_codes(accounts):
    """True when most account values are codes (510100, GL-5101, 6100-200) rather than names."""
    accounts = list(accounts)
    codes = [a for a in accounts if re.fullmatch(r'[A-Za-z]{0,4}[\s\-_.]?\d[\d\s\-_.]{2,}', a)]
    return bool(accounts) and len(codes) * 2 >= len(accounts)


# Product hierarchy: SKU -> brand -> category. A product master (optional) places each SKU in its brand and
# category, so data that carries SKUs only can be reviewed by brand. Data may also come by brand alone.
PRODUCT_COLUMNS = {**{k: v for k, v in ALIASES.items() if v in PRODUCT_LEVELS}, 'product': 'sku', 'item': 'sku',
                   'sku_name': 'sku_name', 'product_name': 'sku_name', 'description': 'sku_name'}


class UnmappedSKU(ValueError):
    """SKUs in the data that the product master does not list."""

    def __init__(self, codes, present):
        self.codes, self.present = codes, present
        shown = ', '.join(codes[:10]) + (f' and {len(codes) - 10} more' if len(codes) > 10 else '')
        super().__init__(f'{len(codes)} SKU(s) in the data are not in the product master: {shown}. Download the '
                         'product master below, fill in the brand (and category) for each new SKU, and upload it again.')


def load_product_master(text):
    """Product master: sku plus brand and/or category, optional sku_name. Returns {sku: {...}}."""
    reader = csv.reader(io.StringIO(text.lstrip('﻿')))
    names = [PRODUCT_COLUMNS.get(n, n) for n in _header(reader)]
    if 'sku' not in names or not {'brand', 'category'} & set(names):
        raise ValueError('The product master needs a sku column and a brand or category column.')
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise ValueError('The product master has more than one column for ' + ', '.join(repeated) + '.')
    used = [c for c in ('brand', 'category', 'sku_name') if c in names]
    master, category_of = {}, {}
    for line, values in enumerate(reader, 2):
        if not any(v.strip() for v in values):
            continue
        if len(values) != len(names):
            raise ValueError(f'Product master row {line}: missing value or extra column.')
        raw = dict(zip(names, (v.strip() for v in values)))
        sku = raw['sku']
        if not sku:
            raise ValueError(f'Product master row {line}: the sku is empty.')
        if sku in master:
            raise ValueError(f'Product master row {line}: SKU {sku} is listed twice.')
        if not any(raw[c] for c in used if c != 'sku_name'):
            raise ValueError(f'Product master row {line}: SKU {sku} has no brand or category. Fill it in.')
        entry = {c: raw[c] for c in used if raw[c]}
        if 'brand' in entry and 'category' in entry and category_of.setdefault(entry['brand'], entry['category']) != entry['category']:
            raise ValueError(f"Product master row {line}: brand {entry['brand']} is in two categories. A brand sits in one category.")
        master[sku] = entry
    if not master:
        raise ValueError('The product master has no rows.')
    return master


def apply_products(dataset, master):
    """Give every line with a SKU its brand and category from the master. Unknown SKUs stop (UnmappedSKU);
    a brand or category in the data that contradicts the master stops. A line with a brand but no SKU gets
    the brand's category. Returns (dataset, summary)."""
    present = sorted({r['sku'] for r in dataset if 'sku' in r.get('dims', ())})
    unknown = [s for s in present if s not in master]
    if unknown:
        raise UnmappedSKU(unknown, present)
    category_of = {e['brand']: e['category'] for e in master.values() if 'brand' in e and 'category' in e}
    rows = []
    for r in dataset:
        r = dict(r)
        have = set(r.get('dims', ()))
        known = dict(master[r['sku']]) if 'sku' in have else {}
        if 'category' not in known and (known.get('brand') or r.get('brand')) in category_of:
            known['category'] = category_of[known.get('brand') or r['brand']]
        for level in ('brand', 'category'):
            if level in known:
                if level in have and r[level] != known[level]:
                    where = f"SKU {r['sku']}" if 'sku' in have else f"brand {r.get('brand')}"
                    raise ValueError(f'{where}: the data says {level} {r[level]}, the product master says {known[level]}.')
                r[level] = known[level]
                have.add(level)
        if 'sku' in have and known.get('sku_name'):
            r['sku_name'] = known['sku_name']
        r['dims'] = union_dims([d for d in r.get('dims', ()) if d not in PRODUCT_LEVELS] + sorted(have & set(PRODUCT_LEVELS)))
        rows.append(r)
    dims = union_dims(dataset.dims, *(r['dims'] for r in rows))
    result = Dataset(rows, dims, dataset.attrs)
    check_products(result)
    return result, {'skus': len(present), 'brands': len({master[s].get('brand') for s in present} - {None}),
                    'unused': [s for s in master if s not in set(present)]}


def check_products(dataset):
    """One place in the hierarchy per product: a SKU in one brand and category, a brand in one category."""
    seen = {}
    for r in dataset:
        have = r.get('dims', ())
        for child, parent in (('sku', 'brand'), ('sku', 'category'), ('brand', 'category')):
            if child in have and parent in have:
                first = seen.setdefault((child, r[child], parent), r[parent])
                if first != r[parent]:
                    raise ValueError(f'{dim_label(child)} {r[child]} is in two {parent == "brand" and "brands" or "categories"} '
                                     f'({first} and {r[parent]}). Each product sits in one place in the hierarchy.')
    return dataset


def product_levels(dims):
    return [d for d in PRODUCT_LEVELS if d in dims]


def breakdown(dataset, line, baseline, level):
    """The finer items (for example the SKUs) inside one analysed line, largest variance first."""
    groups = {}
    for r in dataset:
        if level not in r.get('dims', ()) or not same_line(r, line):
            continue
        g = groups.setdefault(r[level], {level: r[level], 'name': r.get('sku_name', '') if level == 'sku' else '',
                                         'actual': Decimal(0), baseline: Decimal(0), 'pl_line': r['pl_line']})
        g['actual'] += r['actual']
        g[baseline] += r[baseline]
    out = [{**g, **compare(g['actual'], g[baseline], Decimal(0), Decimal(0), is_sales(g))} for g in groups.values()]
    return sorted(out, key=lambda g: (-abs(g['variance']), g[level]))


def product_margins(rows, baseline, level):
    """Sales, cost of sales and gross margin per product (brand, SKU or category), for lines that carry one.

    Each entry: label, sales/cogs/gm actual and baseline, sales_variance, sales_pct, gm_variance, gm_pct_actual,
    gm_pct_baseline, gm_pts. Largest sales first.
    """
    groups = {}
    for r in rows:
        if level not in r.get('dims', ()) or r.get('pl_line') not in (SALES, 'Cost of sales'):
            continue
        g = groups.setdefault(r[level], {'sales': [Decimal(0), Decimal(0)], 'cogs': [Decimal(0), Decimal(0)]})
        part = g['sales' if is_sales(r) else 'cogs']
        part[0] += r['actual']
        part[1] += r[baseline]
    out = []
    for label, g in groups.items():
        (sa, sb), (ca, cb) = g['sales'], g['cogs']
        ga, gb = sa - ca, sb - cb
        pa, pb = (ga / sa * 100 if sa else None), (gb / sb * 100 if sb else None)
        out.append({'label': label, 'sales_actual': sa, 'sales_baseline': sb, 'sales_variance': sa - sb,
                    'sales_pct': (sa - sb) / abs(sb) * 100 if sb else None, 'cogs_actual': ca, 'cogs_baseline': cb,
                    'gm_actual': ga, 'gm_baseline': gb, 'gm_variance': ga - gb, 'gm_pct_actual': pa, 'gm_pct_baseline': pb,
                    'gm_pts': pa - pb if pa is not None and pb is not None else None})
    return sorted(out, key=lambda e: (-e['sales_baseline'], e['label']))


def merge_datasets(main, extra, name='The sales and P&L file'):
    """Add a second file (for example sales and other P&L lines) to the main expense data.

    The files must cover the same month. Their levels may differ: costs by cost centre, sales by profit centre
    and SKU or brand. Each line keeps its own levels. A line may appear in only one file, an account sits on
    one P&L line, and a centre keeps one set of details.
    """
    if main[0]['month'] != extra[0]['month']:
        raise ValueError(f"{name} is for {extra[0]['month']}, but the main file is for {main[0]['month']}.")
    key = lambda r: (r['country'], *((d, r[d]) for d in r['dims']), r['account'])
    clash = sorted(' | '.join((k[0], *(v for _, v in k[1:-1]), k[-1])) for k in {key(r) for r in extra} & {key(r) for r in main})
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
            raise ValueError(f'Centre {code}: the two files disagree on {", ".join(clash)}.')
        known.update(info)
    return Dataset(list(main) + list(extra), union_dims(main.dims, extra.dims), attrs)


def union_dims(*groups):
    """Levels in a stable order: centre first, other levels as found, product levels last (coarse to fine)."""
    found = list(dict.fromkeys(d for dims in groups for d in dims))
    return tuple([d for d in found if d == CC] + [d for d in found if d != CC and d not in PRODUCT_LEVELS]
                 + [d for d in PRODUCT_LEVELS if d in found])


def own_dims(row, dims):
    """The chosen levels this row carries (a tax line has no SKU; a cost line no brand)."""
    have = row.get('dims', ())
    return tuple(d for d in dims if d in have)


def aggregate(rows, dims):
    """Sum rows to month/country/<dims>/account. Each row is summed on the chosen levels it carries, so cost
    lines (by cost centre) and sales lines (by profit centre and brand) sit side by side. Centre details
    travel with the centre."""
    dims = tuple(dims)
    attrs = getattr(rows, 'attrs', {}) if CC in dims else {}
    groups = {}
    for r in rows:
        own = own_dims(r, dims)
        key = (r['month'], r['currency'], r['country'], *((d, r[d]) for d in own), r['account'])
        group = groups.get(key)
        if group is None:
            group = groups[key] = {'month': r['month'], 'country': r['country'], 'account': r['account'],
                                   'pl_line': r.get('pl_line', OPEX), 'currency': r['currency'], **{d: r[d] for d in own},
                                   **{a: Decimal(0) for a in AMOUNTS}, 'dims': own}
            if CC in own:
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


NEGATIVE_NOTE = 'usually a reversal, reclass or misposting; check the posting before relying on the variance'


def negative_balance(row, baseline):
    """A line whose actual or comparison value nets below zero (Travel at INR -15,000). It is valid
    accounting, but rare enough that it always needs a reviewer's explanation."""
    return row['actual'] < 0 or row[baseline] < 0


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
    A moving line with a negative balance is always in scope ('Negative balance'), whatever its size.
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
    for r in results:
        r['negative'] = negative_balance(r, baseline)
        if r['negative'] and r['variance'] and not r['material']:
            r['material'], r['scope'] = True, 'Negative balance'
    return results


def commentary(row, baseline):
    prefix = f"{line_label(row)} | {row['month']}: "
    noun = 'Revenue' if is_sales(row) else 'Expense'
    if not row['variance']:
        return prefix + f"{noun} equals {baseline} at INR {row['actual']:,.2f}."
    pct = f"{abs(row['pct']):.1f}%" if row['pct'] is not None else 'percentage N/A; zero baseline'
    flag = ''
    if negative_balance(row, baseline):
        which = 'actual' if row['actual'] < 0 else baseline
        flag = f" Negative balance: the {which} is INR {row[which]:,.2f}, {NEGATIVE_NOTE}."
    return (prefix + f"{noun} is INR {abs(row['variance']):,.2f} "
            f"{'above' if row['variance'] > 0 else 'below'} {baseline} "
            f"({pct}; {row['direction'].lower()}).{flag} "
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
            # EBITDA and Net profit always show (the headline tiles), even when no line sits just above them
            if after == position and (accounts or subtotal in ('EBITDA', 'Net profit')):
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
    ('India', 'IN-COR', 'Recruitment', 10000, -15000),  # an agency credit note: the account nets to -5,000
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
    # Profit centres: sales and cost of sales post here; the country's commercial head owns them.
    ('PC-IN', 'India', 'India Commercial', 'Commercial', 'Rohan Iyer'),
    ('PC-SG', 'Singapore', 'Singapore Commercial', 'Commercial', 'Sarah Lim'),
    ('PC-AU', 'Australia', 'Australia Commercial', 'Commercial', 'Liam Murphy'),
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
        rows = [('2026-08', country, account, 'INR', budget + change, budget, budget + (change // 2))
                for country, changes in zip(countries, movements)
                for account, budget, change in zip(accounts, budgets, changes)]
        rows.insert(5, ('2026-08', 'India', 'Recruitment', 'INR', -5000, 10000, 2500))  # a negative balance
        return _csv(FIELDS, rows)
    return _csv(('month', 'country', CC, 'account', 'currency', 'actual', 'budget', 'forecast'),
                [('2026-08', country, cc, account, 'INR', budget + change, budget, budget + change // 2)
                 for country, cc, account, budget, change in SAMPLE_LINES])


# The second demo file: sales and cost of sales by profit centre and SKU, plus depreciation, interest and tax on
# corporate cost centres (no SKU). Together with the expense sample: Sales 30.5m vs 30.0m down to Net profit.
# Fictional products: (sku, name, brand, category).
SAMPLE_SKUS = (
    ('AUR-100', 'Aurora Shampoo 200ml', 'Aurora', 'Personal care'),
    ('AUR-200', 'Aurora Conditioner 200ml', 'Aurora', 'Personal care'),
    ('NIM-100', 'Nimbus Soap 4-pack', 'Nimbus', 'Personal care'),
    ('NIM-200', 'Nimbus Body Wash 250ml', 'Nimbus', 'Personal care'),
    ('BRZ-100', 'Breeze Detergent 1kg', 'Breeze', 'Home care'),
    ('BRZ-200', 'Breeze Dishwash 500ml', 'Breeze', 'Home care'),
)
PROFIT_CENTRES = {'India': 'PC-IN', 'Singapore': 'PC-SG', 'Australia': 'PC-AU'}
# Per country, per SKU in SAMPLE_SKUS order: (sales budget, sales change, cost of sales change). Cost of sales is
# budgeted at 45% of sales. India's story: Nimbus loses 360k of sales (a delisting), Aurora's cost of sales rises
# 300k (input costs), and the country total still looks calm (+300k sales), so the mix hides inside the net.
SAMPLE_SKU_MOVES = {
    'India': ((3000000, 250000, 200000), (2000000, 150000, 100000), (2500000, -300000, -80000),
              (1000000, -60000, -20000), (2500000, 150000, 30000), (1000000, 110000, 20000)),
    'Singapore': ((2500000, -150000, -40000), (1500000, -50000, -20000), (2000000, 50000, 20000),
                  (1000000, 0, 0), (1200000, -30000, -5000), (800000, -20000, -5000)),
    'Australia': ((2000000, 100000, 60000), (1000000, 50000, 30000), (2000000, 50000, 30000),
                  (1000000, 0, 0), (2000000, 150000, 60000), (1000000, 50000, 20000)),
}
SAMPLE_CORPORATE_LINES = (  # (country, cost centre, account, P&L line, budget, change)
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


def _sample_pl_lines():
    """(country, centre, sku, account, P&L line, budget, change) for every line of the sales and P&L sample."""
    out = []
    for account, line in (('Product sales', SALES), ('Cost of goods sold', 'Cost of sales')):
        for country, moves in SAMPLE_SKU_MOVES.items():
            for (sku, *_), (budget, change, cogs_change) in zip(SAMPLE_SKUS, moves):
                out.append((country, PROFIT_CENTRES[country], sku, account, line,
                            *((budget, change) if line == SALES else (budget * 45 // 100, cogs_change))))
    return out + [(country, cc, '', account, line, budget, change)
                  for country, cc, account, line, budget, change in SAMPLE_CORPORATE_LINES]


def sample_pl_csv():
    """Fictional August 2026 sales and other P&L lines: profit centre and SKU for sales and cost of sales, an
    explicit pl_line column. Brands come from the product master."""
    return _csv(('month', 'country', 'profit_center', 'sku', 'account', 'pl_line', 'currency', 'actual', 'budget', 'forecast'),
                [('2026-08', country, centre, sku, account, line, 'INR', budget + change, budget, budget + change // 2)
                 for country, centre, sku, account, line, budget, change in _sample_pl_lines()])


def sample_product_master_csv():
    return _csv(('sku', 'sku_name', 'brand', 'category'), SAMPLE_SKUS)


def sample_cc_master_csv():
    """Cost centres and profit centres. Includes SG-COR, which has no August rows, to show the completeness check."""
    return _csv((CC, 'country', 'cc_name', 'function', 'owner'), SAMPLE_CC)


# Fictional chart of accounts: each reporting account is made of GL codes. (code, name, budget share %,
# variance share %) split every sample line across its GL codes, so the GL-level demo adds up exactly to the
# account-level one. 540900 is a credit GL (recharges to a group company), so a GL code can be negative.
SAMPLE_GL = {
    'Employee costs': (('510100', 'Salaries and wages', 70, 30), ('510200', 'Bonus and incentives', 15, 60),
                       ('510300', 'Employer PF and social security', 10, 5), ('510400', 'Staff welfare', 5, 5)),
    'Travel': (('520100', 'Airfare', 50, 60), ('520200', 'Hotels', 30, 30), ('520300', 'Local conveyance', 20, 10)),
    'Consulting': (('530100', 'Advisory fees', 50, 80), ('530200', 'Legal fees', 50, 20)),
    'Warehousing': (('540100', 'Warehouse rent', 70, 20), ('540200', '3PL handling charges', 40, 70),
                    ('540300', 'Warehouse utilities', 10, 10), ('540900', 'Warehouse recharges to group', -20, 0)),
    'Distribution': (('550100', 'Outbound freight', 60, 80), ('550200', 'Courier', 25, 10), ('550300', 'Packaging', 15, 10)),
    'Recruitment': (('560100', 'Recruitment agency fees', 80, 100), ('560200', 'Job portal subscriptions', 20, 0)),
    'Product sales': (('410100', 'Sales to distributors', 70, 50), ('410200', 'Online sales', 30, 50)),
    'Cost of goods sold': (('610100', 'Materials consumed', 75, 80), ('610200', 'Freight inward', 15, 15),
                           ('610300', 'Direct labour', 10, 5)),
    'Depreciation': (('710100', 'Depreciation: plant and machinery', 70, 50), ('710200', 'Amortisation: software', 30, 50)),
    'Interest expense': (('720100', 'Interest on term loan', 100, 100),),
    'Income tax': (('730100', 'Current tax', 90, 90), ('730200', 'Deferred tax', 10, 10)),
}


def _split(total, shares):
    parts = [total * s // 100 for s in shares]
    parts[0] += total - sum(parts)  # rounding goes to the first GL code, so the parts add up exactly
    return parts


def _gl_rows(country, cc, account, budget, change, *levels):
    codes = SAMPLE_GL[account]
    budgets = _split(budget, [c[2] for c in codes])
    changes, halves = _split(change, [c[3] for c in codes]), _split(change // 2, [c[3] for c in codes])
    return [('2026-08', country, cc, *levels, code, 'INR', b + d, b, b + h)
            for (code, *_), b, d, h in zip(codes, budgets, changes, halves)]


def sample_gl_csv():
    """The expense sample at GL code level: the account column holds GL codes."""
    return _csv(('month', 'country', CC, 'account', 'currency', 'actual', 'budget', 'forecast'),
                [row for line in SAMPLE_LINES for row in _gl_rows(*line)])


def sample_gl_pl_csv():
    """The sales and P&L sample at GL code level. No pl_line column: the GL mapping places each account."""
    return _csv(('month', 'country', 'profit_center', 'sku', 'account', 'currency', 'actual', 'budget', 'forecast'),
                [row for country, centre, sku, account, _, budget, change in _sample_pl_lines()
                 for row in _gl_rows(country, centre, account, budget, change, sku)])


def sample_gl_map_csv():
    lines = {account: line for _, _, _, account, line, _, _ in _sample_pl_lines()}
    return _csv((GL, 'gl_name', 'account', 'pl_line'),
                [(code, name, account, lines.get(account, OPEX))
                 for account, codes in SAMPLE_GL.items() for code, name, _, _ in codes])
