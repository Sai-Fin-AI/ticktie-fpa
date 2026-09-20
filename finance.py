"""Expense-only variance engine. Monetary calculations use decimal arithmetic."""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

FIELDS = ('month', 'country', 'account', 'currency', 'actual', 'budget', 'forecast')


def load_csv(text):
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if reader.fieldnames != list(FIELDS):
        raise ValueError('Use these columns, in order: ' + ', '.join(FIELDS))
    rows, seen = [], set()
    for line, raw in enumerate(reader, 2):
        if None in raw or any(v is None or not v.strip() for v in raw.values()):
            raise ValueError(f'Row {line}: missing value or extra column. No values were assumed.')
        row = {k: v.strip() for k, v in raw.items()}
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', row['month']):
            raise ValueError(f'Row {line}: month must be YYYY-MM.')
        if row['currency'] != 'INR':
            raise ValueError(f'Row {line}: version 1 requires INR reporting amounts.')
        key = tuple(row[k] for k in FIELDS[:4])
        if key in seen:
            raise ValueError(f'Row {line}: duplicate month/country/account/currency.')
        seen.add(key)
        for field in ('actual', 'budget', 'forecast'):
            try:
                value = Decimal(row[field])
                if not value.is_finite() or value < 0 or value != value.quantize(Decimal('.01')):
                    raise InvalidOperation
            except InvalidOperation:
                raise ValueError(f'Row {line}: {field} must be a nonnegative number with at most 2 decimals.') from None
            row[field] = value
        rows.append(row)
    if not rows:
        raise ValueError('The file has no data rows.')
    if len({r['month'] for r in rows}) != 1:
        raise ValueError('Version 1 accepts one reporting month per file.')
    return rows


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


def commentary(row, baseline):
    prefix = f"{row['country']} | {row['account']} | {row['month']}: "
    if not row['variance']:
        return prefix + f"Expense equals {baseline} at INR {row['actual']:,.2f}."
    pct = f"{abs(row['pct']):.1f}%" if row['pct'] is not None else 'percentage N/A; zero baseline'
    return (prefix + f"Expense is INR {abs(row['variance']):,.2f} "
            f"{'above' if row['variance'] > 0 else 'below'} {baseline} "
            f"({pct}; {row['direction'].lower()}). "
            'The input does not establish the cause. Confirm the explanation with the account owner.')


def sample_csv():
    """Synthetic teaching data; all countries are already translated into INR."""
    countries = ('India', 'Singapore', 'Australia')
    accounts = ('Employee costs', 'Travel', 'Consulting', 'Warehousing', 'Distribution')
    budgets = (2000000, 200000, 0, 800000, 1000000)
    movements = ((100000, -60000, 75000, 0, 200000),
                 (0, 10000, 0, -80000, 40000),
                 (-150000, 50000, 55000, 40000, 100000))
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(FIELDS)
    for country, changes in zip(countries, movements):
        for account, budget, change in zip(accounts, budgets, changes):
            forecast = budget + (change // 2)
            writer.writerow(('2026-08', country, account, 'INR', budget + change, budget, forecast))
    return output.getvalue()
