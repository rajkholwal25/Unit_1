from flask import Blueprint, render_template, request
from flask_login import login_required

from app.models.job import JobHeaderLine, JobMaster

mfg_dashboard_bp = Blueprint('mfg_dashboard', __name__, url_prefix='/manufacturing')


def _primary_fg_codes_by_job_no(jobs: list[JobMaster]) -> dict[str, str]:
    """First header line FG item code per job (for list display)."""
    if not jobs:
        return {}
    job_nos = [j.job_no for j in jobs]
    lines = (
        JobHeaderLine.query.filter(JobHeaderLine.job_id.in_(job_nos))
        .order_by(JobHeaderLine.job_id, JobHeaderLine.line_no)
        .all()
    )
    out: dict[str, str] = {}
    for hl in lines:
        if hl.job_id in out:
            continue
        code = (hl.sap_fg_item_code or '').strip()
        if code:
            out[hl.job_id] = code
    return out

MFG_JOB_STATUSES = ('open', 'staged', 'released', 'closed', 'cancelled')


@mfg_dashboard_bp.route('/')
@login_required
def index():
    """Manufacturing jobs list with filters (full list; home dashboard is ``/``)."""
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    customer_filter = request.args.get('customer', '').strip()

    query = JobMaster.query.order_by(JobMaster.created_at.desc())
    if status_filter:
        query = query.filter_by(overall_status=status_filter)
    if customer_filter:
        query = query.filter(
            JobMaster.sap_customer_name_snap.ilike(f'%{customer_filter}%')
        )

    pagination = query.paginate(page=page, per_page=20, error_out=False)
    primary_fg_by_job = _primary_fg_codes_by_job_no(pagination.items)

    return render_template(
        'jobs/unified.html',
        jobs=pagination.items,
        pagination=pagination,
        status_filter=status_filter,
        customer_filter=customer_filter,
        priority_filter='',
        primary_fg_by_job=primary_fg_by_job,
        list_endpoint='mfg_dashboard.index',
        page_title='Manufacturing',
        status_choices=MFG_JOB_STATUSES,
    )
