"""Ensure each SAP sales order number is linked to at most one active job."""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from app.models.job import JobMaster


def normalize_so_number(so_no) -> Optional[str]:
    s = str(so_no or '').strip()
    return s or None


def job_using_so(
    so_no,
    *,
    exclude_job_no: Optional[str] = None,
    exclude_cancelled: bool = True,
) -> Optional[JobMaster]:
    norm = normalize_so_number(so_no)
    if not norm:
        return None
    q = JobMaster.query.filter(JobMaster.sap_so_number_snap == norm)
    if exclude_cancelled:
        q = q.filter(JobMaster.overall_status != 'cancelled')
    if exclude_job_no:
        q = q.filter(JobMaster.job_no != str(exclude_job_no).strip())
    return q.first()


def so_number_usage_map() -> Dict[str, str]:
    """Map normalized SO number → job_no (non-cancelled jobs only)."""
    rows = (
        JobMaster.query.filter(
            JobMaster.sap_so_number_snap.isnot(None),
            JobMaster.sap_so_number_snap != '',
            JobMaster.overall_status != 'cancelled',
        )
        .with_entities(JobMaster.sap_so_number_snap, JobMaster.job_no)
        .all()
    )
    out: Dict[str, str] = {}
    for so_snap, job_no in rows:
        norm = normalize_so_number(so_snap)
        if norm:
            out[norm] = job_no
    return out


def so_already_used_message(so_no, *, exclude_job_no: Optional[str] = None) -> Optional[str]:
    existing = job_using_so(so_no, exclude_job_no=exclude_job_no)
    if not existing:
        return None
    norm = normalize_so_number(so_no)
    return (
        f'Sales order {norm} is already used on job {existing.job_no}. '
        'Create a new open sales order in SAP, or choose a different SO.'
    )


def find_duplicate_so_job_groups() -> dict[str, list[JobMaster]]:
    """SO number → active jobs sharing that SO (only groups with 2+ jobs)."""
    from collections import defaultdict

    groups: dict[str, list[JobMaster]] = defaultdict(list)
    rows = (
        JobMaster.query.filter(
            JobMaster.sap_so_number_snap.isnot(None),
            JobMaster.sap_so_number_snap != '',
            JobMaster.overall_status != 'cancelled',
        )
        .order_by(JobMaster.created_at.asc(), JobMaster.job_no.asc())
        .all()
    )
    for job in rows:
        norm = normalize_so_number(job.sap_so_number_snap)
        if norm:
            groups[norm].append(job)
    return {so: jobs for so, jobs in groups.items() if len(jobs) > 1}


def plan_duplicate_so_cleanup() -> tuple[list[tuple[str, str]], list[JobMaster]]:
    """Return (kept per SO, jobs to cancel). Keeps newest created_at / highest job_no."""
    kept: list[tuple[str, str]] = []
    to_cancel: list[JobMaster] = []
    for so, jobs in sorted(find_duplicate_so_job_groups().items()):
        keeper = jobs[-1]
        kept.append((so, keeper.job_no))
        to_cancel.extend(jobs[:-1])
    return kept, to_cancel


_DUPLICATE_SO_CANCEL_REMARK = (
    'Auto-cancelled: duplicate SO; kept latest job for this sales order.'
)


def _force_cancel_job(job: JobMaster, *, actor_user_id: int, remark: str) -> None:
    """Admin cleanup: cancel without transition rules (e.g. job already closed)."""
    from datetime import datetime

    from app.extensions import db
    from app.models.audit import JobStatusHistory

    if job.overall_status == 'cancelled':
        return
    db.session.add(
        JobStatusHistory(
            job_id=job.job_no,
            from_status=job.overall_status,
            to_status='cancelled',
            changed_by=actor_user_id,
            remark=remark,
        )
    )
    job.overall_status = 'cancelled'
    job.updated_at = datetime.utcnow()


def cancel_duplicate_so_jobs(*, dry_run: bool = True, actor_user_id: int | None = None) -> dict:
    """Cancel older jobs that share an SO; keep the latest job per SO."""
    from app.extensions import db
    from app.services.job_service import transition_job_status

    kept, to_cancel = plan_duplicate_so_cleanup()
    result = {
        'dry_run': dry_run,
        'kept': [{'so_no': so, 'job_no': jn} for so, jn in kept],
        'cancelled': [],
        'skipped': [],
        'errors': [],
    }
    if not to_cancel:
        return result

    actor = actor_user_id
    if actor is None and to_cancel:
        actor = to_cancel[0].created_by

    for job in to_cancel:
        entry = {'job_no': job.job_no, 'so_no': normalize_so_number(job.sap_so_number_snap)}
        if job.overall_status == 'cancelled':
            result['skipped'].append({**entry, 'reason': 'already_cancelled'})
            continue
        if dry_run:
            result['cancelled'].append({**entry, 'status': 'would_cancel'})
            continue
        try:
            try:
                transition_job_status(
                    job,
                    'cancelled',
                    remark=_DUPLICATE_SO_CANCEL_REMARK,
                    user_id=actor,
                )
            except ValueError:
                _force_cancel_job(
                    job,
                    actor_user_id=actor,
                    remark=_DUPLICATE_SO_CANCEL_REMARK,
                )
            db.session.commit()
            result['cancelled'].append({**entry, 'status': 'cancelled'})
        except Exception as e:
            db.session.rollback()
            result['errors'].append({**entry, 'error': str(e)})

    return result


def validate_so_numbers_for_new_job(
    so_numbers: Iterable,
    *,
    job_series: Optional[str] = None,
) -> Optional[str]:
    """Return user-facing error text, or None if all SO numbers are available."""
    if (job_series or '').strip() == 'Rejection':
        return None
    seen: set[str] = set()
    for raw in so_numbers:
        norm = normalize_so_number(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        msg = so_already_used_message(norm)
        if msg:
            return msg
    return None
