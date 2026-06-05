"""Unit 1 dimension unit labels (display only — stored numeric values unchanged).

- Thickness: micron (short ``mic`` / suffix ``MIC`` in FG ItemName, e.g. ``PET 12MIC …``)
- Length: meter (``mtr``)
- Width: millimetre (``mm``)
"""

from __future__ import annotations

from typing import Any, Optional

THICKNESS_UNIT = 'mic'
THICKNESS_SUFFIX = 'MIC'
LENGTH_UNIT = 'mtr'
WIDTH_UNIT = 'mm'


def thickness_suffix_for_label() -> str:
    return THICKNESS_SUFFIX


def format_length_with_unit(value: Any) -> str:
    if value is None or value == '':
        return '—'
    return f'{value} {LENGTH_UNIT}'


def format_width_with_unit(value: Any) -> str:
    if value is None or value == '':
        return '—'
    return f'{value} {WIDTH_UNIT}'


def format_length_x_width(length: Any, width: Any) -> str:
    parts: list[str] = []
    if length not in (None, ''):
        parts.append(format_length_with_unit(length))
    if width not in (None, ''):
        parts.append(format_width_with_unit(width))
    return ' × '.join(parts) if parts else '—'


def format_thickness_label(value: Optional[str] = None) -> str:
    """Column/field label, e.g. ``Thickness (mic)``."""
    if value:
        return f'Thickness ({value})'
    return f'Thickness ({THICKNESS_UNIT})'
