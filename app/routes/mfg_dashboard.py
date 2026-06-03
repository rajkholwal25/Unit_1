from flask import Blueprint, render_template, request
from flask_login import login_required

from app.models.job import JobMaster

mfg_dashboard_bp = Blueprint('mfg_dashboard', __name__, url_prefix='/manufacturing')

MFG_JOB_STATUSES = ('open', 'staged', 'released', 'closed', 'cancelled')


@mfg_dashboard_bp.route('/')
@login_required
def index():
    """Manufacturing jobs only — list with filters (no SAP PO/SO snapshot)."""
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    customer_filter = request.args.get('customer', '').strip()
    priority_filter = request.args.get('priority', '')

    query = JobMaster.query.order_by(JobMaster.created_at.desc())
    if status_filter:
        query = query.filter_by(overall_status=status_filter)
    if customer_filter:
        query = query.filter(
            JobMaster.sap_customer_name_snap.ilike(f'%{customer_filter}%')
        )
    if priority_filter:
        query = query.filter_by(priority=priority_filter)

    pagination = query.paginate(page=page, per_page=20, error_out=False)

    return render_template(
        'jobs/unified.html',
        jobs=pagination.items,
        pagination=pagination,
        status_filter=status_filter,
        customer_filter=customer_filter,
        priority_filter=priority_filter,
        list_endpoint='mfg_dashboard.index',
        page_title='Manufacturing',
        status_choices=MFG_JOB_STATUSES,
    )
