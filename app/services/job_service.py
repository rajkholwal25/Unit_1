"""job_service.py — Business logic for job card lifecycle.

All business rules live here, not in models or routes.
Routes call these functions; routes never manipulate db.session directly
for multi-step operations.
"""
from __future__ import annotations

import re
from datetime import datetime
from flask import current_app
from flask_login import current_user

from app.extensions import db
from sqlalchemy.exc import OperationalError, ProgrammingError
from app.models.job import (
    JobMaster,
    JobHeaderLine,
    JobDetailLine,
    JobDetailLineFgInvolved,
    JOB_STATUSES,
)
from app.models.audit import JobStatusHistory


# ---------------------------------------------------------- job number
def generate_job_no(job_type_cat: str, job_series: str, original_job_no: str = None) -> str:
    """Generate a 7-digit job number with independent counters per prefix.
    
    Prefixes:
    - Normal: Mono=1, Rigid=2, Commercial=3
    - Rejection: Mono=7, Rigid=8, Commercial=9
    
    Each prefix has its own 6-digit auto-incrementing sequence.
    """
    prefix_map = {
        'Normal': {'Mono': '1', 'Rigid': '2', 'Commercial': '3'},
        'Rejection': {'Mono': '7', 'Rigid': '8', 'Commercial': '9'}
    }
    
    series = job_series if job_series in prefix_map else 'Normal'
    cat = job_type_cat if job_type_cat in prefix_map[series] else 'Mono'
    prefix = prefix_map[series].get(cat, '1')

    # Find the last job with the EXACT same prefix
    last = (
        JobMaster.query
        .filter(JobMaster.job_no.like(f'{prefix}%'))
        .order_by(JobMaster.job_no.desc())
        .first()
    )
    
    if last and len(last.job_no) >= 7:
        try:
            # Suffix is everything after the first character
            curr_suffix = last.job_no[1:]
            # Handle cases with non-numeric suffixes if they ever exist (defensive)
            numeric_part = ''.join(filter(str.isdigit, curr_suffix))
            next_num = int(numeric_part or 0) + 1
        except (ValueError, IndexError):
            next_num = 1
    else:
        next_num = 1
        
    return f'{prefix}{next_num:06d}'


# -------------------------------------------------------- status transitions
# Allowed transitions: from_status -> [list of valid to_status values]
ALLOWED_TRANSITIONS = {
    'open':     ['staged', 'closed', 'cancelled'],
    'staged':   ['released', 'open', 'closed', 'cancelled'],
    'released': ['closed', 'staged', 'cancelled'],
    'closed':   [],
    'cancelled': [],
}


def can_transition(current_status: str, target_status: str) -> bool:
    return target_status in ALLOWED_TRANSITIONS.get(current_status, [])


def transition_job_status(
    job: JobMaster,
    to_status: str,
    remark: str = None,
    user_id: int = None,
) -> JobMaster:
    """Change job overall_status with validation and audit log.

    Raises ValueError if the transition is not allowed.
    The caller must commit db.session after this call.
    """
    if not can_transition(job.overall_status, to_status):
        raise ValueError(
            f'Cannot move job {job.job_no} from '
            f'"{job.overall_status}" to "{to_status}".'
        )

    actor = user_id or (current_user.id if current_user.is_authenticated else None)

    history = JobStatusHistory(
        job_id=job.job_no,
        from_status=job.overall_status,
        to_status=to_status,
        changed_by=actor,
        remark=remark,
    )
    db.session.add(history)

    job.overall_status = to_status
    job.updated_at = datetime.utcnow()
    return job


# --------------------------------------------------- detail ↔ header FG links
def _fg_num_from_code(fg_code: str) -> str:
    if not fg_code:
        return 'FG'
    match = re.search(r'(FG\d+)', fg_code, re.IGNORECASE)
    return match.group(1).upper() if match else fg_code.strip()


def sync_detail_line_fg_involved(
    detail_line: JobDetailLine,
    job_master: JobMaster,
    selected_header_indices: list[int],
    selected_lines: list,
    created_header_lines: list[JobHeaderLine],
) -> None:
    """Persist which header FG/SO lines apply to this detail line (replace-all).

    Does not commit. Caller must have flushed so ``detail_line.id`` is assigned.
    ``created_header_lines`` must list ``JobHeaderLine`` rows in the same order as
    ``selected_lines`` indices (one entry per selected SO/FG row).
    """
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError

    # Schema drift support:
    # - Older DB revision uses job_master_id FK (job_master.id)
    # - Newer revision uses job_id FK (job_master.job_no)
    try:
        bind = db.session.get_bind()
        insp = sa.inspect(bind)
        cols = {c['name'] for c in insp.get_columns('job_detail_line_fg_involved')}
    except Exception:
        cols = set()

    try:
        JobDetailLineFgInvolved.query.filter_by(
            detail_line_id=detail_line.id
        ).delete(synchronize_session=False)
    except (ProgrammingError, OperationalError) as e:
        # Migration not applied (table missing) or DB mismatch.
        # Don't block job creation; skip persisting the links.
        current_app.logger.warning(
            '[FG-INVOLVED] Skipped save: %s', str(e)[:240]
        )
        db.session.rollback()
        return

    seen: set[int] = set()
    for hi in selected_header_indices:
        try:
            hi_int = int(hi)
        except (TypeError, ValueError):
            continue
        if hi_int in seen:
            continue
        seen.add(hi_int)
        if hi_int < 0 or hi_int >= len(created_header_lines):
            continue
        hl = created_header_lines[hi_int]
        row = (
            selected_lines[hi_int]
            if hi_int < len(selected_lines) and isinstance(selected_lines[hi_int], dict)
            else {}
        )
        fg_code = str(row.get('fg_code') or '').strip()
        de = row.get('doc_entry')
        try:
            doc_entry = int(de) if de not in (None, '') else None
        except (TypeError, ValueError):
            doc_entry = None
        ln = row.get('line_num')
        try:
            line_num = int(ln) if ln not in (None, '') else None
        except (TypeError, ValueError):
            line_num = None
        so_num = str(row.get('so_no') or '').strip() or None

        try:
            # Use raw insert so we can populate either job_id or job_master_id depending on DB schema.
            payload = {
                'detail_line_id': detail_line.id,
                'header_line_id': hl.id,
                'fg_num': _fg_num_from_code(fg_code),
                'sap_so_number': so_num,
                'sap_so_doc_entry': doc_entry,
                'sap_so_line_num': line_num,
                'sap_fg_item_code': fg_code or None,
            }
            if 'job_id' in cols:
                payload['job_id'] = job_master.job_no
            if 'job_master_id' in cols:
                payload['job_master_id'] = job_master.id

            if not payload.get('job_id') and not payload.get('job_master_id'):
                # If we can't detect columns, fall back to ORM insert (best effort).
                db.session.add(
                    JobDetailLineFgInvolved(
                        job_id=job_master.job_no,
                        detail_line_id=detail_line.id,
                        header_line_id=hl.id,
                        fg_num=_fg_num_from_code(fg_code),
                        sap_so_number=so_num,
                        sap_so_doc_entry=doc_entry,
                        sap_so_line_num=line_num,
                        sap_fg_item_code=fg_code or None,
                    )
                )
            else:
                cols_sql = ', '.join(payload.keys())
                vals_sql = ', '.join([f':{k}' for k in payload.keys()])
                db.session.execute(
                    sa.text(
                        f"INSERT INTO job_detail_line_fg_involved ({cols_sql}) VALUES ({vals_sql})"
                    ),
                    payload,
                )
        except (ProgrammingError, OperationalError) as e:
            current_app.logger.warning(
                '[FG-INVOLVED] Skipped row insert: %s', str(e)[:240]
            )
            db.session.rollback()
            return
        except IntegrityError as e:
            # Covers FK mismatches like missing job_master_id in older schema.
            current_app.logger.warning(
                '[FG-INVOLVED] Integrity error (schema mismatch?): %s', str(e)[:240]
            )
            db.session.rollback()
            return


# --------------------------------------------------- creation helpers
def create_job(
    customer_code: str,
    customer_name: str,
    created_by: int,
    so_entry: int = None,
    so_number: str = None,
    priority: str = 'normal',
    delivery_date=None,
    remarks: str = None,
    assigned_planner_id: int = None,
    job_type_cat: str = 'Mono',
    job_series: str = 'Normal',
    original_job_no: str = None,
    sap_job_card_doc_entry: int = None,
    sap_job_card_doc_num_snap: str = None,
    sap_job_card_series_snap: str = None,
    sap_job_card_title_snap: str = None,
) -> JobMaster:
    """Create a new JobMaster and write the initial status history row.

    Does NOT commit — caller must call db.session.commit().
    """
    job = JobMaster(
        job_no=generate_job_no(job_type_cat, job_series, original_job_no),
        sap_customer_code=customer_code,
        sap_customer_name_snap=customer_name,
        sap_so_entry=so_entry,
        sap_so_number_snap=so_number,
        overall_status='open',
        priority=priority,
        delivery_date=delivery_date,
        remarks=remarks,
        assigned_planner_id=assigned_planner_id,
        created_by=created_by,
        job_type_cat=job_type_cat,
        job_series=job_series,
        original_job_no=(original_job_no or None),
        sap_job_card_doc_entry=sap_job_card_doc_entry,
        sap_job_card_doc_num_snap=(sap_job_card_doc_num_snap or None),
        sap_job_card_series_snap=(sap_job_card_series_snap or None),
        sap_job_card_title_snap=(sap_job_card_title_snap or None),
    )
    db.session.add(job)
    db.session.flush()   # get job.id without committing

    # Write initial audit row
    history = JobStatusHistory(
        job_id=job.job_no,
        from_status=None,
        to_status='open',
        changed_by=created_by,
        remark='Job initialized as open',
    )
    db.session.add(history)

    return job



def add_header_line(
    job: JobMaster,
    fg_item_code: str,
    fg_item_name: str,
    dispatch_qty,
    uom: str,
    job_type: str = 'new',
    ups: int = 1,
    length=None, width=None, height=None,
    # Detail line fields
    element_name: str = None,
    detail_ups: int = None,
    detail_yield_loss_pct=None,
    raw_material_item_code: str = None,
    paper_brand: str = None,
    mill: str = None,
    total_sheets: int = None,
    paper_supplied_by: str = 'company',
    wastage_pct: float = 0,
    wastage_sheets: int = None,
    sheet_length: float = None,
    sheet_width: float = None,
    gsm: int = None,
    print_style: str = None,
    print_type: str = None,
    front_colours: str = None,
    back_colours: str = None,
    die_no: str = None,
    pasting_style: str = None,
    special_instructions: str = None,
) -> tuple[JobHeaderLine, JobDetailLine]:
    """Add a new component line (header + initial detail) to an existing job.

    line_no is auto-assigned as max(existing) + 1.
    Does NOT commit.
    """
    existing_max = (
        db.session.query(db.func.max(JobHeaderLine.line_no))
        .filter_by(job_id=job.job_no)

        .scalar()
    ) or 0
    new_no = existing_max + 1

    # 1. Header Line
    line = JobHeaderLine(
        job_id=job.job_no,
        line_no=new_no,
        sap_fg_item_code=fg_item_code,
        sap_fg_item_name_snap=fg_item_name,
        dispatch_qty=dispatch_qty,
        uom=uom,
        ups=ups,
        job_type=job_type,
        length=length,
        width=width,
        height=height,
    )
    db.session.add(line)

    # 2. Detail Line (uses header ups unless a detail-specific override is provided)
    detail = JobDetailLine(
        job_id=job.job_no,
        detail_no=new_no,
        element_name=element_name or (fg_item_name or 'Component'),
        ups=detail_ups if detail_ups not in (None, '') and float(detail_ups) > 0 else None,
        yield_loss_pct=detail_yield_loss_pct if detail_yield_loss_pct not in (None, '') else None,
        raw_material_item_code=raw_material_item_code,
        paper_brand=paper_brand,
        mill=mill,
        total_sheets=total_sheets,
        paper_supplied_by=paper_supplied_by,
        wastage_pct=wastage_pct,
        wastage_sheets=wastage_sheets if wastage_sheets not in (None, '') else None,
        sheet_length=sheet_length,
        sheet_width=sheet_width,
        gsm=gsm,
        print_style=print_style,
        print_type=print_type,
        front_colours=front_colours,
        back_colours=back_colours,
        die_no=die_no,
        pasting_style=pasting_style,
        special_instructions=special_instructions,
    )
    # Respect explicit wastage_sheets from the form; only compute when not provided.
    if detail.wastage_sheets in (None, ''):
        detail.compute_wastage()
    db.session.add(detail)

    return line, detail


def add_header_line_only(
    job: JobMaster,
    fg_item_code: str,
    fg_item_name: str,
    dispatch_qty,
    uom: str,
    job_type: str = 'new',
    ups: int = 1,
    length=None, width=None, height=None,
) -> JobHeaderLine:
    """Add a header line only (no detail line).

    Use when multiple FG lines share a single detail spec.
    """
    existing_max = (
        db.session.query(db.func.max(JobHeaderLine.line_no))
        .filter_by(job_id=job.job_no)
        .scalar()
    ) or 0
    new_no = existing_max + 1

    line = JobHeaderLine(
        job_id=job.job_no,
        line_no=new_no,
        sap_fg_item_code=fg_item_code,
        sap_fg_item_name_snap=fg_item_name,
        dispatch_qty=dispatch_qty,
        uom=uom,
        ups=ups,
        job_type=job_type,
        length=length,
        width=width,
        height=height,
    )
    db.session.add(line)
    return line
