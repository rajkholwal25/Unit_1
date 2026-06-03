from types import SimpleNamespace

from flask import Blueprint, current_app, render_template, request
from flask_login import login_required
from sqlalchemy import desc, or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.job import JobMaster, JobDetailLine, JobHeaderLine
from app.models.audit import IntegrationEvent, JobStatusHistory
from app.models.mfg_bom import Bom, BomStep
from app.services.sap_mfg_snapshot import fetch_sap_manufacturing_snapshot

mfg_dashboard_bp = Blueprint('mfg_dashboard', __name__, url_prefix='/manufacturing')


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


def _build_dashboard_activity(limit: int = 40):
    items = []
    try:
        events = (
            IntegrationEvent.query.order_by(desc(IntegrationEvent.updated_at)).limit(80).all()
        )
    except Exception:
        db.session.rollback()
        events = []
    for ev in events:
        items.append({
            'kind': 'integration',
            'target_system': (ev.target_system or 'SAP_B1').strip(),
            'int_state': (ev.state or '').strip(),
            'at': ev.updated_at,
            'job_no': ev.job_id,
            'title': (ev.action or 'integration').replace('_', ' ').title(),
            'detail': _integration_event_detail(ev),
        })
    try:
        history_rows = (
            JobStatusHistory.query.options(joinedload(JobStatusHistory.user))
            .order_by(desc(JobStatusHistory.changed_at))
            .limit(80)
            .all()
        )
    except Exception:
        db.session.rollback()
        history_rows = []
    for h in history_rows:
        fr = (h.from_status or '—').replace('_', ' ').title()
        to = (h.to_status or '—').replace('_', ' ').title()
        rm = (h.remark or '').strip()
        items.append({
            'kind': 'status',
            'at': h.changed_at,
            'job_no': h.job_id,
            'title': f'{fr} → {to}',
            'detail': rm or 'No note.',
        })
    items.sort(key=lambda x: x['at'], reverse=True)
    items = items[:limit]
    job_nos = {i['job_no'] for i in items if i.get('job_no')}
    id_by_no = {}
    if job_nos:
        try:
            id_by_no = {
                j.job_no: j.id
                for j in JobMaster.query.filter(JobMaster.job_no.in_(job_nos)).all()
            }
        except Exception:
            db.session.rollback()
    for i in items:
        i['job_id'] = id_by_no.get(i.get('job_no'))
    return items


@mfg_dashboard_bp.route('/')
@login_required
def index():
    stats = {'total': JobMaster.query.count()}
    mfg_statuses = ['open', 'staged', 'released', 'closed', 'cancelled']
    mfg_status_counts = {
        s: JobMaster.query.filter_by(overall_status=s).count() for s in mfg_statuses
    }
    q_raw = (request.args.get('q') or '').strip()
    q_like = f'%{q_raw}%'
    jobs_q = JobMaster.query
    if q_raw:
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
    jobs = jobs_q.order_by(JobMaster.created_at.desc()).limit(50).all()
    job_rows = [SimpleNamespace(job=j, first_line=j.header_lines.first()) for j in jobs]
    sap_configured = bool(
        current_app.config.get('SAP_SERVICE_LAYER_URL') or current_app.config.get('SAP_BASE_URL')
    )
    sap_snapshot = None
    if sap_configured:
        try:
            sap_snapshot = fetch_sap_manufacturing_snapshot(po_limit=25, so_limit=15)
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('SAP manufacturing snapshot failed')
            sap_snapshot = {
                'configured': True,
                'connected': False,
                'error': str(exc),
                'mirror': {},
                'production_orders': [],
                'open_sales_orders': [],
            }
    return render_template(
        'mfg_dashboard/index.html',
        q=q_raw,
        job_rows=job_rows,
        stats=stats,
        mfg_status_counts=mfg_status_counts,
        activity_items=_build_dashboard_activity(limit=40),
        sap_configured=sap_configured,
        sap_snapshot=sap_snapshot,
    )
