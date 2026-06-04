"""Context builder for the manufacturing dashboard (home page)."""

from __future__ import annotations

from types import SimpleNamespace

from flask import current_app
from sqlalchemy import desc, or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.audit import IntegrationEvent, JobStatusHistory
from app.models.job import JobDetailLine, JobHeaderLine, JobMaster
from app.models.mfg_bom import Bom, BomStep


def _integration_event_detail(ev: IntegrationEvent) -> str:
    if (ev.error_message or '').strip():
        return (ev.error_message or '').strip()
    rp = ev.response_payload
    if isinstance(rp, dict):
        m = (rp.get('message') or '').strip()
        if m:
            return m
    if ev.state == 'pending':
        return 'Pending…'
    return (ev.state or '').replace('_', ' ').title()


def _user_display_name(user) -> str:
    if not user:
        return 'System'
    if (user.username or '').strip():
        return user.username.strip()
    email = (user.email or '').strip()
    if email and '@' in email:
        return email.split('@', 1)[0]
    return 'User'


def build_dashboard_activity(limit: int = 40) -> list[dict]:
    """Recent status changes and SAP/app integration events."""
    try:
        items: list[dict] = []
        for ev in (
            IntegrationEvent.query.order_by(desc(IntegrationEvent.updated_at)).limit(80).all()
        ):
            ts = (ev.target_system or 'SAP_B1').strip().lower()
            kind = 'status' if ts == 'app' else 'sap'
            items.append({
                'kind': kind,
                'at': ev.updated_at,
                'job_no': ev.job_id,
                'title': (ev.action or 'integration').replace('_', ' ').title(),
                'detail': _integration_event_detail(ev),
            })
        for h in (
            JobStatusHistory.query.options(joinedload(JobStatusHistory.user))
            .order_by(desc(JobStatusHistory.changed_at))
            .limit(80)
            .all()
        ):
            fr = (h.from_status or '—').replace('_', ' ').title()
            to = (h.to_status or '—').replace('_', ' ').title()
            rm = (h.remark or '').strip()
            by = _user_display_name(h.user)
            detail = f'By {by}. {rm or "No note."}'
            items.append({
                'kind': 'status',
                'at': h.changed_at,
                'job_no': h.job_id,
                'title': f'{fr} → {to}',
                'detail': detail,
            })
        items.sort(key=lambda x: x['at'], reverse=True)
        items = items[:limit]
        job_nos = {i['job_no'] for i in items if i.get('job_no')}
        id_by_no = {}
        if job_nos:
            id_by_no = {
                j.job_no: j.id
                for j in JobMaster.query.filter(JobMaster.job_no.in_(job_nos)).all()
            }
        for i in items:
            i['job_id'] = id_by_no.get(i.get('job_no'))
        return items
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Dashboard activity log query failed')
        return []


def build_mfg_dashboard_context(q_raw: str | None = None, *, jobs_limit: int = 50) -> dict:
    """Template context for ``mfg_dashboard/index.html``."""
    q_raw = (q_raw or '').strip()
    stats = {'total': JobMaster.query.count()}
    mfg_statuses = ['open', 'staged', 'released', 'closed', 'cancelled']
    mfg_status_counts = {
        s: JobMaster.query.filter_by(overall_status=s).count() for s in mfg_statuses
    }

    jobs_q = JobMaster.query
    if q_raw:
        q_like = f'%{q_raw}%'
        filters = [
            JobMaster.job_no.ilike(q_like),
            JobMaster.header_lines.any(
                or_(
                    JobHeaderLine.sap_fg_item_code.ilike(q_like),
                    JobHeaderLine.sap_fg_item_name_snap.ilike(q_like),
                )
            ),
        ]
        if q_raw.isdigit():
            po_num = int(q_raw)
            filters.append(
                JobMaster.detail_lines.any(
                    JobDetailLine.boms.any(Bom.steps.any(BomStep.sap_doc_num == po_num))
                )
            )
        jobs_q = jobs_q.filter(or_(*filters))

    jobs = jobs_q.order_by(JobMaster.created_at.desc()).limit(jobs_limit).all()
    job_rows = [SimpleNamespace(job=j, first_line=j.header_lines.first()) for j in jobs]

    return {
        'q': q_raw,
        'job_rows': job_rows,
        'stats': stats,
        'mfg_status_counts': mfg_status_counts,
        'activity_items': build_dashboard_activity(limit=40),
        'sap_configured': bool(
            current_app.config.get('SAP_SERVICE_LAYER_URL')
            or current_app.config.get('SAP_BASE_URL')
        ),
        'sap_snapshot': None,
    }
