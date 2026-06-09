"""Create and allocate sequential GRN batch numbers (R000001, R000002, …)."""

from __future__ import annotations

import re

from sqlalchemy import func

from app.models.roll_grn import RollGrnEntry
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_DECIMAL_QUANT = Decimal('0.001')

_GRN_RE = re.compile(r'^R(\d+)$', re.IGNORECASE)


def format_grn_number(seq: int) -> str:
    if seq < 1:
        raise ValueError('GRN sequence must be positive')
    return f'R{seq:06d}'


def _max_grn_sequence() -> int:
    rows = (
        RollGrnEntry.query.with_entities(RollGrnEntry.grn_number)
        .order_by(RollGrnEntry.id.desc())
        .limit(500)
        .all()
    )
    max_seq = 0
    for (grn_no,) in rows:
        m = _GRN_RE.match((grn_no or '').strip())
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq


def allocate_next_grn_number() -> str:
    """Thread-safe enough for typical single-app use via row lock on insert."""
    max_seq = _max_grn_sequence()
    return format_grn_number(max_seq + 1)


def _decimal_or_none(val):
    """Parse form decimal from string (avoids 4.6 → 4.599 float drift)."""
    if val is None or val == '':
        return None
    try:
        d = Decimal(str(val).strip().replace(',', '.'))
        if d < 0:
            return None
        return d.quantize(_DECIMAL_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None


def list_roll_grns():
    return (
        RollGrnEntry.query.order_by(RollGrnEntry.id.desc())
        .all()
    )


def get_roll_grn_by_number(grn_number: str) -> RollGrnEntry | None:
    norm = (grn_number or '').strip().upper()
    if not norm:
        return None
    return RollGrnEntry.query.filter(
        func.upper(RollGrnEntry.grn_number) == norm
    ).first()


def get_roll_grn_by_supplier_roll(supplier_name: str, supplier_roll_number: str) -> RollGrnEntry | None:
    """Match existing raw by supplier + roll number (case-insensitive) for re-upload dedup."""
    sup = (supplier_name or '').strip()
    roll = (supplier_roll_number or '').strip()
    if not sup or not roll:
        return None
    return RollGrnEntry.query.filter(
        func.lower(RollGrnEntry.supplier_name) == sup.lower(),
        func.lower(RollGrnEntry.supplier_roll_number) == roll.lower(),
    ).first()
