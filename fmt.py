"""Number style for management outputs.

International: 12,380,000 · 12.38m · 340k.   Indian: 1,23,80,000 · 1.24 Cr · 3.40 L.
The style only changes how numbers are written, never the figures, so it is not part of the pack fingerprint.
Each agent sets the style at the start of its own thread; the setting does not leak between threads.
"""
from contextvars import ContextVar
from decimal import Decimal

STYLES = ('International (k / m)', 'Indian (lakh / crore)')
_style = ContextVar('number_style', default='international')


def use(style):
    _style.set('indian' if style and str(style).lower().startswith('indian') else 'international')


def indian():
    return _style.get() == 'indian'


def group(value, decimals=0):
    """Absolute value with thousands separators: 3,80,000 in Indian style, 380,000 otherwise."""
    value = abs(Decimal(value))
    if not indian():
        return f'{value:,.{decimals}f}'
    whole, _, fraction = f'{value:.{decimals}f}'.partition('.')
    if len(whole) > 3:
        head, pairs = whole[:-3], []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        whole = ','.join(([head] if head else []) + pairs + [whole[-3:]])
    return whole + ('.' + fraction if fraction else '')


def balance(value, decimals=0):
    """A balance (an actual, a budget, a profit), with a minus sign when it is below zero: −15,000.
    group() alone suits variances, whose direction is given in words (above / below plan)."""
    return ('−' if Decimal(value) < 0 else '') + group(value, decimals)


def signed(value, decimals=0):
    value = Decimal(value)
    return ('+' if value > 0 else '−' if value < 0 else '') + group(value, decimals)


def compact(value, sign=True):
    """Short form for chart labels and heatmaps: +340k / 12.38m, or +3.40 L / 1.24 Cr."""
    value = Decimal(value)
    prefix = ('+' if value > 0 else '−' if value < 0 else '') if sign else ''
    size = abs(value)
    if indian():
        if size >= 10 ** 7:
            return f'{prefix}{size / 10 ** 7:.2f} Cr'
        return f'{prefix}{size / 10 ** 5:.2f} L' if size >= 1000 else f'{prefix}{size:.0f}'
    if size >= 10 ** 6:
        return f'{prefix}{size / 10 ** 6:.2f}m'
    return f'{prefix}{size / 1000:.0f}k' if size >= 1000 else f'{prefix}{size:.0f}'


def chart_unit():
    """(divisor, suffix, word) for chart values: lakhs in Indian style, thousands otherwise."""
    return (Decimal(10 ** 5), ' L', 'INR lakhs') if indian() else (Decimal(1000), 'k', 'INR thousands')


def big_unit():
    """(divisor, suffix, word) for large totals such as the bridge: crores or millions."""
    return (Decimal(10 ** 7), ' Cr', 'INR crores') if indian() else (Decimal(10 ** 6), 'm', 'INR millions')
