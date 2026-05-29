from flask import Blueprint, render_template
from ..models import SapPushLog

sap_logs_bp = Blueprint('sap_logs', __name__)

@sap_logs_bp.route('/')
def list_logs():
    logs = SapPushLog.query.order_by(SapPushLog.created_at.desc()).limit(200).all()
    return render_template('sap_logs/list.html', logs=logs)
