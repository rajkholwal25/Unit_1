from flask import Blueprint, render_template, request
from flask_login import login_required

from app.services.mfg_dashboard_data import build_mfg_dashboard_context

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def index():
    """Home dashboard: job stats, search, latest jobs, activity log."""
    q = (request.args.get('q') or '').strip()
    return render_template(
        'mfg_dashboard/index.html',
        **build_mfg_dashboard_context(q),
    )
