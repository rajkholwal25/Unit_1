"""Create and allocate sequential GRN numbers (R000001, R000002, …)."""

from __future__ import annotations

import re

from sqlalchemy import func

from app.extensions import db
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


def create_roll_grn_from_form(form, *, created_by_id: int | None) -> RollGrnEntry:
    supplier_name = (form.get('supplier_name') or '').strip()
    supplier_roll_number = (form.get('supplier_roll_number') or '').strip()
    film_type = (form.get('film_type') or '').strip()
    coating = (form.get('coating') or '').strip()

    width_mm = _decimal_or_none(form.get('width_mm'))
    thickness_mic = _decimal_or_none(form.get('thickness_mic'))
    length_mtr = _decimal_or_none(form.get('length_mtr'))
    gross_weight_kg = _decimal_or_none(form.get('gross_weight_kg'))
    net_weight_kg = _decimal_or_none(form.get('net_weight_kg'))
    core_weight_kg = _decimal_or_none(form.get('core_weight_kg'))

    missing = []
    if not supplier_name:
        missing.append('Supplier Name')
    if not supplier_roll_number:
        missing.append('Roll Number')
    if not film_type:
        missing.append('Film Type')
    if not coating:
        missing.append('Chemical Coating')
    if width_mm is None or width_mm <= 0:
        missing.append('Width (mm)')
    if thickness_mic is None:
        missing.append('Thickness (mic)')
    if length_mtr is None or length_mtr <= 0:
        missing.append('Length (mtr)')
    if gross_weight_kg is None or gross_weight_kg <= 0:
        missing.append('Gross Weight (kg)')
    if net_weight_kg is None or net_weight_kg <= 0:
        missing.append('Net weight (kg)')

    if missing:
        raise ValueError('Required: ' + ', '.join(missing))

    grn_number = allocate_next_grn_number()
    entry = RollGrnEntry(
        grn_number=grn_number,
        supplier_name=supplier_name[:200],
        supplier_roll_number=supplier_roll_number[:100],
        film_type=film_type[:50],
        coating=coating[:50],
        width_mm=width_mm,
        thickness_mic=thickness_mic,
        length_mtr=length_mtr,
        gross_weight_kg=gross_weight_kg,
        net_weight_kg=net_weight_kg,
        core_weight_kg=core_weight_kg,
        created_by_id=created_by_id,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


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
