"""SAP data snapshot for the manufacturing dashboard."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Flask

from app.extensions import db
from app.models.sap_mirror import SapCustomerMirror, SapItemMirror, SapMirrorSyncState
from app.services.sap_job_client import SAPClient, SAPClientError


def _mirror_stats() -> dict[str, Any]:
    """Read mirror counts; never leave the SQLAlchemy session in a failed state."""
    empty = {
        'customers': 0,
        'items': 0,
        'last_customer_sync_at': None,
        'last_item_sync_at': None,
        'last_error': None,
    }
    try:
        st = SapMirrorSyncState.query.get(1)
        return {
            'customers': SapCustomerMirror.query.count(),
            'items': SapItemMirror.query.count(),
            'last_customer_sync_at': (
                st.last_customer_sync_at.isoformat() if st and st.last_customer_sync_at else None
            ),
            'last_item_sync_at': (
                st.last_item_sync_at.isoformat() if st and st.last_item_sync_at else None
            ),
            'last_error': (st.last_error or '')[:300] if st else None,
        }
    except Exception:
        db.session.rollback()
        return dict(empty)


def fetch_sap_manufacturing_snapshot(
    *,
    po_limit: int = 25,
    so_limit: int = 15,
) -> dict[str, Any]:
    """Load live SAP manufacturing overview (production orders + open SO)."""
    from flask import current_app

    url = (current_app.config.get('SAP_SERVICE_LAYER_URL') or '').strip()
    company = (current_app.config.get('SAP_COMPANY_DB') or '').strip()
    out: dict[str, Any] = {
        'configured': bool(url),
        'connected': False,
        'company_db': company,
        'service_url': url,
        'error': None,
        'fetched_at': datetime.utcnow().isoformat() + 'Z',
        'mirror': _mirror_stats(),
        'production_orders': [],
        'open_sales_orders': [],
    }
    if not url:
        out['error'] = 'SAP Service Layer URL is not configured.'
        return out

    client = SAPClient()
    try:
        client.login()
        out['connected'] = True
        out['production_orders'] = client.fetch_production_orders_recent(limit=po_limit)
        out['open_sales_orders'] = client.fetch_recent_open_sales_orders(limit=so_limit)
    except SAPClientError as e:
        out['error'] = str(e)
    except Exception as e:
        out['error'] = f'{type(e).__name__}: {e}'
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return out


def fetch_sap_manufacturing_snapshot_app(
    app: Flask,
    *,
    po_limit: int = 25,
    so_limit: int = 15,
) -> dict[str, Any]:
    with app.app_context():
        return fetch_sap_manufacturing_snapshot(po_limit=po_limit, so_limit=so_limit)
