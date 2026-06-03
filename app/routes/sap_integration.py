from flask import Blueprint, current_app, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.extensions import db
from app.models.sap_mirror import SapCustomerMirror, SapItemMirror
from app.services.sap_service import SAPServiceLayer
from app.services.sap_mfg_snapshot import fetch_sap_manufacturing_snapshot

sap_bp = Blueprint('sap', __name__, url_prefix='/sap')


def admin_required(f):
    from functools import wraps

    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Access denied. Manager or admin role required.', 'danger')
            return redirect(url_for('sap.index'))
        return f(*args, **kwargs)
    return decorated


@sap_bp.route('/')
@login_required
def index():
    """SAP Integration hub: connection, sync mirror, live PO/SO snapshot."""
    sap = SAPServiceLayer()
    connected = False
    conn_error = None
    try:
        connected = sap.test_connection()
    except Exception as e:
        conn_error = str(e)

    sap_configured = bool(
        current_app.config.get('SAP_SERVICE_LAYER_URL') or current_app.config.get('SAP_BASE_URL')
    )
    sap_snapshot = None
    if sap_configured:
        try:
            sap_snapshot = fetch_sap_manufacturing_snapshot(po_limit=20, so_limit=10)
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('SAP integration snapshot failed')
            sap_snapshot = {
                'configured': True,
                'connected': False,
                'error': str(exc),
                'mirror': {},
                'production_orders': [],
                'open_sales_orders': [],
            }

    return render_template(
        'sap/integration.html',
        connected=connected,
        conn_error=conn_error,
        sap_configured=sap_configured,
        sap_snapshot=sap_snapshot,
        mirror_customers=SapCustomerMirror.query.count(),
        mirror_items=SapItemMirror.query.count(),
    )


@sap_bp.route('/status')
@admin_required
def status():
    sap = SAPServiceLayer()
    connected = False
    error_msg = None

    try:
        connected = sap.test_connection()
    except Exception as e:
        error_msg = str(e)

    return render_template('sap/status.html', connected=connected, error_msg=error_msg)


@sap_bp.route('/sync-customers', methods=['GET', 'POST'])
@admin_required
def sync_customers():
    if request.method == 'POST':
        from app.services.sap_mirror_sync import sync_customers_from_sap
        try:
            count = sync_customers_from_sap()
            flash(f'Successfully synced {count} customers from SAP into the mirror.', 'success')
        except Exception as e:
            flash(f'Error syncing customers: {str(e)}', 'danger')

        return redirect(url_for('sap.index', _anchor='sap-sync'))

    total_customers = SapCustomerMirror.query.count()
    total_items = SapItemMirror.query.count()
    return render_template('sap/sync_customers.html',
                           with_sap=total_customers, without_sap=0, total_items=total_items)

@sap_bp.route('/sync-items', methods=['POST'])
@admin_required
def sync_items():
    from app.services.sap_mirror_sync import sync_items_from_sap
    try:
        count = sync_items_from_sap()
        flash(f'Successfully synced {count} items from SAP into the mirror.', 'success')
    except Exception as e:
        flash(f'Error syncing items: {str(e)}', 'danger')

    return redirect(url_for('sap.index', _anchor='sap-sync'))
