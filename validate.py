"""Rule-based data checks and cleaning, run on the uploaded files before any figure is calculated. No AI.

Three kinds of rule, every one of them logged:
- Cleaned automatically: extra spaces in codes and names ("IN-SAL " -> "IN-SAL").
- Suggested, applied only when the user accepts: the same code spelled differently in two files or within
  one file (upper/lower case, leading zeros, separators such as - . _ / or spaces). The budget's "00510100"
  and the actuals' 510100 would otherwise be two different lines.
- Stopped: codes Excel has already corrupted (5.1E+11, or numbers too long to keep every digit), which no
  rule can repair; the file must be re-exported with the column formatted as text.
"""
import re
from collections import Counter

from finance import CC

FIELDS = (('account', 'Account / GL code'), (CC, 'Cost / profit centre'), ('country', 'Country / entity'),
          ('sku', 'SKU'), ('brand', 'Brand'))
FILE_NAMES = {'gl_mapping': 'GL mapping', 'actuals': 'Actuals', 'combined': 'Data file', 'budget': 'Budget',
              'forecast': 'Forecast', 'pl': 'Sales and P&L lines'}
PRIORITY = tuple(FILE_NAMES)  # whose spelling wins: the GL mapping is the master, then the actuals
SEPARATORS = re.compile(r'[\s\-_./]')
SCIENTIFIC = re.compile(r'[+-]?\d+(?:[.,]\d+)?[eE][+-]?\d+')


def match_key(value):
    """Spellings with the same key are the same code: ignore case, separators and leading zeros."""
    key = SEPARATORS.sub('', str(value).upper())
    return re.sub(r'(?<!\d)0+(?=\d)', '', key)


def difference(a, b):
    if a.lower() == b.lower():
        return 'upper/lower case'
    if SEPARATORS.sub('', a.lower()) == SEPARATORS.sub('', b.lower()):
        return 'separators (- . _ / or spaces)'
    return 'leading zeros'


def _corrupted(value):
    if isinstance(value, float) and abs(value) >= 1e15:
        return 'is too long for Excel to keep every digit'
    if isinstance(value, str) and SCIENTIFIC.fullmatch(value.strip()):
        return 'is in scientific notation, so Excel has already dropped digits'
    return None


def check(specs, gl_codes=()):
    """specs: {kind: (headers, rows, roles, name)} as mapped on screen. gl_codes: the GL mapping's codes.

    Returns (cleaned specs, automatic fixes, suggestions). Raises ValueError for corrupted codes.
    """
    cleaned, auto, problems = {}, [], []
    spellings = {field: {} for field, _ in FIELDS}  # field -> spelling -> Counter(kind)
    for code in gl_codes:
        spellings['account'].setdefault(code, Counter())['gl_mapping'] += 1
    for kind, (headers, rows, roles, name) in specs.items():
        rows = [list(r) for r in rows]
        for field, label in FIELDS:
            if field not in roles:
                continue
            i, fixed = roles.index(field), []
            for line, row in enumerate(rows, 2):
                if i >= len(row):
                    continue
                value = row[i]
                problem = _corrupted(value)
                if problem:
                    problems.append(f'{name} row {line}: {label} "{value}" {problem}.')
                    continue
                if isinstance(value, str):
                    tidy = ' '.join(value.split())
                    if tidy != value:
                        fixed.append((value, tidy))
                    row[i] = value = tidy
                text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value).strip()
                if text:
                    spellings[field].setdefault(text, Counter())[kind] += 1
            if fixed:
                auto.append({'check': 'Extra spaces removed', 'file': name, 'field': label, 'count': len(fixed),
                             'example': f'"{fixed[0][0]}" → "{fixed[0][1]}"'})
        cleaned[kind] = (headers, rows, roles, name)
    if problems:
        raise ValueError(' '.join(problems[:5]) + (f' ({len(problems) - 5} more.)' if len(problems) > 5 else '')
                         + ' Re-export the file with that column formatted as text.')
    return cleaned, auto, _suggestions(spellings, specs)


def _suggestions(spellings, specs):
    names = {kind: spec[3] for kind, spec in specs.items()} | {'gl_mapping': 'GL mapping'}
    out = []
    for field, label in FIELDS:
        groups = {}
        for spelling, kinds in spellings[field].items():
            groups.setdefault(match_key(spelling), []).append((spelling, kinds))
        for key, members in groups.items():
            if len(members) < 2 or sum('gl_mapping' in k for _, k in members) > 1:
                continue  # one spelling, or the mapping itself lists both: not ours to merge

            def rank(member):
                spelling, kinds = member
                best = min(PRIORITY.index(k) for k in kinds)
                return best, -sum(kinds.values()), spelling

            members.sort(key=rank)
            target = members[0][0]
            variants = [{'value': s, 'files': [names[k] for k in sorted(kinds, key=PRIORITY.index)],
                         'rows': sum(kinds.values()), 'difference': difference(s, target)}
                        for s, kinds in members[1:]]
            out.append({'id': f'{field}:{key}', 'field': field, 'label': label, 'to': target,
                        'to_files': [names[k] for k in sorted(members[0][1], key=PRIORITY.index)], 'from': variants})
    return out


def apply(specs, suggestions, accepted):
    """Rename the accepted variants to their target spelling. Returns new specs."""
    renames = {}
    for s in suggestions:
        if s['id'] in accepted:
            for v in s['from']:
                renames.setdefault(s['field'], {})[v['value']] = s['to']
    if not renames:
        return specs
    out = {}
    for kind, (headers, rows, roles, name) in specs.items():
        rows = [list(r) for r in rows]
        for field, mapping in renames.items():
            if field in roles:
                i = roles.index(field)
                for row in rows:
                    if i < len(row):
                        text = str(int(row[i])) if isinstance(row[i], float) and row[i].is_integer() else str(row[i]).strip()
                        if text in mapping:
                            row[i] = mapping[text]
        out[kind] = (headers, rows, roles, name)
    return out


def log(auto, suggestions, accepted):
    """The audit trail: every automatic fix and every suggestion, with what was done about it."""
    entries = [{'check': a['check'], 'file': a['file'], 'detail': f"{a['field']}: {a['count']} value(s), e.g. {a['example']}",
                'action': 'Fixed automatically'} for a in auto]
    for s in suggestions:
        for v in s['from']:
            entries.append({'check': 'Same code spelled differently', 'file': ', '.join(v['files']),
                            'detail': f"{s['label']} \"{v['value']}\" ({v['rows']} row(s)) vs \"{s['to']}\" in "
                                      f"{', '.join(s['to_files'])}: differs by {v['difference']}",
                            'action': f"Treated as \"{s['to']}\" (accepted by reviewer)" if s['id'] in accepted
                                      else 'Left as is (not accepted)'})
    return entries
