from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.extensions import db
from app.models.sap_mirror import SapCustomerMirror, SapItemMirror
from app.services.sap_service import SAPServiceLayer

sap_bp = Blueprint('sap', __name__, url_prefix='/sap')


def admin_required(f):
    from functools import wraps

    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('mfg_dashboard.index'))
        return f(*args, **kwargs)
    return decorated


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

        return redirect(url_for('sap.sync_customers'))

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

    return redirect(url_for('sap.sync_customers'))
