"""Populate ``sap_customer_mirror`` from SAP Service Layer."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from flask import Flask, current_app

from app.extensions import db
from app.models.sap_mirror import SapCustomerMirror, SapMirrorSyncState, SapItemMirror
from app.services.sap_job_client import SAPClient, SAPClientError

if TYPE_CHECKING:
    pass

_log_name = 'sap_mirror_sync'


def _sync_state() -> SapMirrorSyncState:
    row = SapMirrorSyncState.query.get(1)
    if row is None:
        row = SapMirrorSyncState(id=1)
        db.session.add(row)
        db.session.commit()
    return row


def sync_customers_from_sap() -> int:
    """Replace mirror customer rows from OCRD by series 1 and 89. Returns row count."""
    client = SAPClient()
    try:
        rows = []
        seen_codes: set[str] = set()
        for series in (1, 89):
            for row in client.fetch_business_partners_by_series(series):
                code = (row.get('CardCode') or '').strip()
                if not code:
                    continue
                key = code.casefold()
                if key in seen_codes:
                    continue
                seen_codes.add(key)
                rows.append(row)
    finally:
        client.logout()

    now = datetime.utcnow()
    SapCustomerMirror.query.delete()
    count = 0
    for r in rows:
        code = (r.get('CardCode') or '').strip()
        if not code:
            continue
        db.session.add(
            SapCustomerMirror(
                card_code=code[:30],
                card_name=(r.get('CardName') or '')[:200] or None,
                phone=(r.get('Phone1') or '')[:100] or None,
                email=(r.get('EmailAddress') or '')[:120] or None,
                synced_at=now,
            )
        )
        count += 1
    st = _sync_state()
    st.last_customer_sync_at = now
    st.customer_row_count = count
    st.last_error = None
    db.session.commit()
    return count


def _classify_item_type(item: dict) -> str:
    """Best-effort item classifier for mirror filtering."""
    code = (item.get('ItemCode') or '').upper()
    grp = str(item.get('ItemsGroupCode') or '')
    name = (item.get('ItemName') or '').lower()
    if code.startswith('FG') or 'finished' in name:
        return 'fg'
    if 'ink' in name or 'adhesive' in name or 'glue' in name:
        return 'consumable'
    if 'service' in name:
        return 'service'
    if grp in {'100', '200'}:
        return 'raw_material'
    return 'raw_material'


def sync_items_from_sap() -> int:
    """Replace mirror item rows from SAP OITM (``/Items``).

    Only **active** items (``Valid`` = ``tYES``) are pulled from SAP and stored.
    """
    client = SAPClient()
    try:
        rows = client.fetch_items()
    finally:
        client.logout()

    now = datetime.utcnow()
    SapItemMirror.query.delete()
    count = 0
    for item in rows:
        if not SAPClient._item_is_active_row(item):
            continue
        code = (item.get('ItemCode') or '').strip()
        if not code:
            continue
        wh = (item.get('DefaultWarehouse') or '').strip()[:20] or None
        db.session.add(
            SapItemMirror(
                item_code=code[:50],
                item_name=(item.get('ItemName') or '')[:200] or None,
                item_type=_classify_item_type(item),
                uom=(item.get('SalesUnit') or '')[:10] or None,
                default_warehouse=wh,
                synced_at=now,
            )
        )
        count += 1

    st = _sync_state()
    st.last_item_sync_at = now
    st.item_row_count = count
    st.last_error = None
    db.session.commit()
    return count


def sync_customers_full_and_new_customer_orders() -> dict:
    """Force full customers refresh."""
    customer_count = sync_customers_from_sap()
    return {
        'customers': customer_count,
    }


def run_full_mirror_sync(app: Flask, scope: str = 'all') -> dict:
    """Run customer and item sync. ``scope``: ``all`` | ``customers``."""
    scope = (scope or 'all').strip().lower()
    out: dict = {'scope': scope, 'customers': None, 'items': None}
    try:
        if scope in ('all', 'customers'):
            out['customers'] = sync_customers_from_sap()
            out['items'] = sync_items_from_sap()
        st = _sync_state()
        if scope == 'all':
            st.last_full_sync_at = datetime.utcnow()
        st.last_error = None
        db.session.commit()
    except Exception as e:
        st = _sync_state()
        st.last_error = str(e)[:2000]
        db.session.commit()
        raise
    return out
