"""Parse and format thickness values (stored as numeric in DB)."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Union

_THICKNESS_QUANT = Decimal('0.001')


def _quantize_thickness(d: Decimal) -> Decimal:
    """Store/display at most 3 decimal places (mm)."""
    return d.quantize(_THICKNESS_QUANT, rounding=ROUND_HALF_UP)


def parse_thickness(value) -> Optional[float]:
    """Return thickness as float (3 dp max), or None if empty/invalid."""
    if value is None or value == '':
        return None
    try:
        if isinstance(value, Decimal):
            d = value
        elif isinstance(value, (int, float)):
            d = Decimal(str(value))
        else:
            text = str(value).strip().replace(',', '.')
            if not text:
                return None
            d = Decimal(text)
        if d < 0:
            return None
        return float(_quantize_thickness(d))
    except (InvalidOperation, ValueError, TypeError):
        return None


def thickness_for_item_code(value: Union[float, int, str, Decimal]) -> str:
    """Format for item codes, e.g. 12.0 → '12', 12.5 → '12.5'."""
    v = parse_thickness(value)
    if v is None:
        return ''
    d = _quantize_thickness(Decimal(str(v)))
    if d == d.to_integral_value():
        return str(int(d))
    s = format(d, 'f').rstrip('0').rstrip('.')
    return s or '0'


def thickness_display(value) -> str:
    """Human-readable thickness for UI."""
    v = parse_thickness(value)
    if v is None:
        return '—'
    d = _quantize_thickness(Decimal(str(v)))
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), 'f').rstrip('0').rstrip('.')
