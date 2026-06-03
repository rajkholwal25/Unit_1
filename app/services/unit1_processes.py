"""Unit 1 film manufacturing: process_master rows and warehouse codes."""

from app.extensions import db
from app.models.reference import ProcessMaster

UNIT1_PROCESS_ROWS = (
    ('EMB', 'Embossing', 'converting', 'FBD-EMB'),
    ('SLT', 'Slitting', 'converting', 'FBD-SLT'),
    ('MET', 'Metalisation', 'converting', 'FBD-MTL'),
)

UNIT1_PROCESS_CODES = frozenset(code for code, *_ in UNIT1_PROCESS_ROWS)

UNIT1_RAW_MATERIAL_WAREHOUSE = 'FBD-RM'

# All manufacturing quantities are weighed in kilograms (not sheets/PCS).
UNIT1_DEFAULT_UOM = 'KGS'

# Process suffixes appended to FG base item code (Unit 1: PET-12-1009-TR-EMB, not …-GEN-EMB).
UNIT1_PROCESS_CODE_SUFFIXES = frozenset(
    {'EMB', 'SLT', 'MET', 'MTL', 'HRI', 'COAT', 'ALOX', 'FG', 'RM', 'PK', 'PACK'}
)


def unit1_fg_base_code(fg_code: str) -> str:
    """Strip trailing process suffixes so PET-12-1009-TR-EMB → PET-12-1009-TR."""
    code = (fg_code or '').strip().upper()
    if not code:
        return 'FG'
    parts = code.split('-')
    while len(parts) > 1 and parts[-1] in UNIT1_PROCESS_CODE_SUFFIXES:
        parts.pop()
    return '-'.join(parts) if parts else code


def unit1_process_item_code(fg_code: str, process_code: str) -> str:
    """Unit 1 output code: ``{FG base}-{process}`` e.g. PET-12-1009-TR-EMB."""
    base = unit1_fg_base_code(fg_code)
    pc = (process_code or 'PROC').strip().upper()
    pc = pc.split('-')[-1] if pc else 'PROC'
    if pc in ('FG', 'PK', 'PACK', 'PKPACK', 'PK-PACK'):
        return base[:50]
    return f'{base}-{pc}'[:50]


UNIT1_WAREHOUSE_OPTIONS = (
    'FBD-RM',
    'FBD-EMB',
    'FBD-SLT',
    'FBD-MTL',
    'FBD-HRI',
    'FBD-COAT',
    'FBD-ALOX',
    'FBD-FG',
)


def seed_unit1_process_master() -> str:
    """Upsert EMB/SLT/MET and deactivate all other process_master rows."""
    active_codes = set(UNIT1_PROCESS_CODES)
    for code, name, cat, wc in UNIT1_PROCESS_ROWS:
        row = ProcessMaster.query.filter_by(process_code=code).first()
        if row:
            row.name = name
            row.category = cat
            row.default_workcenter = wc
            row.is_active = True
        else:
            db.session.add(
                ProcessMaster(
                    process_code=code,
                    name=name,
                    category=cat,
                    default_workcenter=wc,
                    is_active=True,
                )
            )
    deactivated = 0
    for row in ProcessMaster.query.all():
        if row.process_code not in active_codes and row.is_active:
            row.is_active = False
            deactivated += 1
    db.session.commit()
    return (
        f'Unit 1 processes active: {", ".join(active_codes)}. '
        f'Deactivated {deactivated} other row(s).'
    )
