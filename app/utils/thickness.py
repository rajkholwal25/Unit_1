"""Parse and format thickness values (stored as numeric in DB)."""

from decimal import Decimal, InvalidOperation
from typing import Optional, Union


def parse_thickness(value) -> Optional[float]:
    """Return thickness as float, or None if empty/invalid."""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if v >= 0 else None
    if isinstance(value, Decimal):
        v = float(value)
        return v if v >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        v = float(text)
        return v if v >= 0 else None
    except (ValueError, TypeError):
        return None


def thickness_for_item_code(value: Union[float, int, str, Decimal]) -> str:
    """Format for item codes, e.g. 12.0 → '12', 12.5 → '12.5'."""
    v = parse_thickness(value)
    if v is None:
        return ''
    if v == int(v):
        return str(int(v))
    return str(v).rstrip('0').rstrip('.')


def thickness_display(value) -> str:
    """Human-readable thickness for UI."""
    v = parse_thickness(value)
    if v is None:
        return '—'
    if v == int(v):
        return str(int(v))
    return f'{v:g}'
