"""Live SAP Service Layer JSON endpoints for manufacturing job creation."""
from __future__ import annotations

import threading
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from flask_login import login_required

from typing import Optional

from app.extensions import db
from app.logging_config import get_logger
from app.models.sap_mirror import SapCustomerMirror, SapItemMirror, SapMirrorSyncState
from app.services.sap_job_client import SAPClient, SAPClientError
from app.services.sap_mjd1 import fetch_job_card_prefill_payload, mjd1_error_payload

_log = get_logger('api.sap')

sap_api_bp = Blueprint('sap_api', __name__, url_prefix='/api/sap')


def _upsert_customer_mirror(
    card_code: str,
    card_name: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> None:
    now = datetime.utcnow()
    row = SapCustomerMirror.query.get(card_code)
    if row:
        if card_name:
            row.card_name = card_name[:200]
        if phone is not None:
            row.phone = (phone or '')[:100] or None
        if email is not None:
            row.email = (email or '')[:120] or None
        row.synced_at = now
        return
    db.session.add(
        SapCustomerMirror(
            card_code=(card_code or '')[:30],
            card_name=(card_name or card_code)[:200],
            phone=(phone or '')[:100] or None,
            email=(email or '')[:120] or None,
            synced_at=now,
        )
    )


def _configured() -> bool:
    return bool(current_app.config.get('SAP_SERVICE_LAYER_URL'))


def _item_mirror_ready() -> bool:
    st = _mirror_state()
    return st is not None and st.last_item_sync_at is not None


def _mirror_items_search(q: str, limit: int, *, warehouse: str = '') -> list[dict]:
    """Search local SAP item mirror (code or name contains query)."""
    term = (q or '').strip()
    like = f'%{term}%'
    query = SapItemMirror.query.filter(
        db.or_(
            SapItemMirror.item_code.ilike(like),
            SapItemMirror.item_name.ilike(like),
        )
    )
    wh = (warehouse or '').strip().upper()
    if wh:
        query = query.filter(
            db.or_(
                SapItemMirror.default_warehouse == wh,
                SapItemMirror.default_warehouse.is_(None),
                SapItemMirror.item_type == 'raw_material',
            )
        )
    rows = query.order_by(SapItemMirror.item_code).limit(limit).all()
    return [
        {
            'ItemCode': r.item_code,
            'ItemName': r.item_name or '',
            'UoM': r.uom or '',
            'DefaultWarehouse': r.default_warehouse or '',
        }
        for r in rows
    ]


def _mirror_state() -> Optional[SapMirrorSyncState]:
    return SapMirrorSyncState.query.get(1)


def _customer_mirror_ready() -> bool:
    st = _mirror_state()
    return st is not None and st.last_customer_sync_at is not None


def _customer_rows_from_mirror(q: str = '') -> list[dict]:
    q = (q or '').strip().lower()
    out = []
    for r in SapCustomerMirror.query.order_by(SapCustomerMirror.card_name).all():
        code = r.card_code or ''
        name = r.card_name or ''
        if q and q not in name.lower() and q not in code.lower():
            continue
        out.append({
            'code': code,
            'name': name,
            'phone': r.phone,
            'email': r.email,
        })
    return out





def _json_date(d):
    if d is None:
        return None
    return d.isoformat()


@sap_api_bp.route('/customers')
@login_required
def customers():
    """Customers from DB mirror when synced; otherwise live SAP. Optional ?q= filter."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    q = request.args.get('q', '').strip().lower()

    if _customer_mirror_ready():
        return jsonify(_customer_rows_from_mirror(q))

    try:
        client = SAPClient()
        try:
            rows = client.fetch_customers()
        finally:
            client.logout()
    except SAPClientError as e:
        mirror_rows = _customer_rows_from_mirror(q)
        if mirror_rows:
            _log.warning('GET /api/sap/customers using mirror fallback: %s', e)
            return jsonify(mirror_rows)
        return jsonify({'error': str(e)}), 502

    out = []
    for r in rows:
        code = r.get('CardCode')
        if not code:
            continue
        name = r.get('CardName') or ''
        if q and q not in name.lower() and q not in code.lower():
            continue
        out.append({
            'code': code,
            'name': name,
            'phone': r.get('Phone1'),
            'email': r.get('EmailAddress'),
        })
    return jsonify(out)


@sap_api_bp.route('/customers/<path:card_code>')
@login_required
def customer_detail(card_code: str):
    """Single Business Partner from SAP; upserts cache."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    try:
        client = SAPClient()
        try:
            bp = client.fetch_business_partner(card_code)
        finally:
            client.logout()
    except SAPClientError as e:
        row = SapCustomerMirror.query.get(card_code)
        if row:
            _log.warning('GET /api/sap/customers/%s using mirror fallback: %s', card_code, e)
            return jsonify({
                'code': row.card_code,
                'name': row.card_name,
                'phone': row.phone,
                'email': row.email,
                'contact': None,
                'address': None,
                'city': None,
                'zip': None,
                'country': None,
            })
        return jsonify({'error': str(e)}), 502

    _upsert_customer_mirror(
        bp.get('CardCode') or card_code,
        card_name=bp.get('CardName'),
        phone=bp.get('Phone1'),
        email=bp.get('EmailAddress'),
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    return jsonify({
        'code': bp.get('CardCode'),
        'name': bp.get('CardName'),
        'phone': bp.get('Phone1'),
        'email': bp.get('EmailAddress'),
        'contact': bp.get('ContactPerson'),
        'address': bp.get('Address'),
        'city': bp.get('City'),
        'zip': bp.get('ZipCode'),
        'country': bp.get('Country'),
    })


@sap_api_bp.route('/orders')
@login_required
def orders():
    """Open Sales Orders for a CardCode."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    card_code = request.args.get('card_code', '').strip()
    if not card_code:
        return jsonify({'error': 'card_code is required.'}), 400

    try:
        client = SAPClient()
        try:
            raw = client.fetch_open_sales_orders(card_code)
        finally:
            client.logout()
    except SAPClientError as e:
        return jsonify({'error': str(e)}), 502

    out = []
    for o in raw:
        out.append({
            'doc_entry': o.get('DocEntry'),
            'doc_num': o.get('DocNum'),
            'doc_date': o.get('DocDate'),
            'doc_due_date': o.get('DocDueDate'),
            'card_code': o.get('CardCode'),
            'card_name': o.get('CardName'),
        })
    return jsonify(out)


@sap_api_bp.route('/sales-orders/open-by-card')
@login_required
def sales_orders_open_by_card():
    """Open Sales Orders from live SAP Service Layer."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    card_code = request.args.get('card_code', '').strip()
    if not card_code:
        return jsonify({'error': 'card_code is required.'}), 400

    try:
        client = SAPClient()
        try:
            rows = client.fetch_open_sales_orders_ordr(card_code)
        finally:
            client.logout()
    except SAPClientError as e:
        return jsonify({'error': str(e)}), 502

    return jsonify(rows)


@sap_api_bp.route('/mjd1/job-card')
@login_required
def mjd1_job_card():
    """Live SAP OMJD/MJD1/MJD2 job-card prefill lookup."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    doc_num = request.args.get('doc_num', '').strip()
    series = request.args.get('series', '').strip()
    if not doc_num:
        return jsonify({'error': 'doc_num is required.'}), 400

    try:
        client = SAPClient()
        try:
            payload = fetch_job_card_prefill_payload(client, doc_num, series or None)
        finally:
            client.logout()
    except SAPClientError as e:
        return jsonify(mjd1_error_payload(str(e))), 502

    return jsonify(payload)


@sap_api_bp.route('/items')
@login_required
def items_search():
    """Item master search for raw material autocomplete.

    Returns:
      [{item_code, item_name, uom?}]

    Notes:
    - Live SAP search returns **active** items only (``Valid`` = ``tYES``).
    - Live Service Layer search may not include UoM fields depending on build.
      We enrich with local SapItemMirror.uom when available.
    """
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])

    warehouse = (request.args.get('warehouse') or '').strip()
    rows: list = []
    if _item_mirror_ready():
        rows = _mirror_items_search(q, limit=100, warehouse=warehouse)

    if not rows:
        try:
            client = SAPClient()
            try:
                rows = client.search_items(q, limit=100)
            finally:
                client.logout()
        except SAPClientError as e:
            _log.warning('SAP items_search live OData failed, using mirror: %s', e)
            rows = _mirror_items_search(q, limit=100, warehouse=warehouse)

    if not rows:
        rows = _mirror_items_search(q, limit=100, warehouse=warehouse)

    # Enrich with UoM from mirror (fast batch lookup).
    codes: list[str] = []
    for r in rows:
        c = r.get('ItemCode')
        if c:
            codes.append(str(c).strip())
    mirror_uom_by_code: dict[str, str] = {}
    if codes:
        mrows = (
            SapItemMirror.query.filter(SapItemMirror.item_code.in_(codes))
            .all()
        )
        mirror_uom_by_code = {m.item_code: (m.uom or '') for m in mrows if m and m.item_code}

    out = []
    for r in rows:
        code = (r.get('ItemCode') or '').strip()
        if not code:
            continue
        # Prefer UoM from mirror; fall back to any UoM-like field from live payload.
        uom = (
            mirror_uom_by_code.get(code)
            or r.get('UoM')
            or r.get('InventoryUOM')
            or r.get('SalesUnit')
            or ''
        )
        out.append({
            'item_code': code,
            'item_name': r.get('ItemName') or '',
            'uom': uom or '',
        })
    return jsonify(out)


@sap_api_bp.route('/orders/<int:doc_entry>/lines')
@login_required
def order_lines(doc_entry: int):
    """Sales order lines (items + qty) for FG selection."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    try:
        client = SAPClient()
        try:
            data = client.fetch_order_with_lines(doc_entry)
        finally:
            client.logout()
    except SAPClientError as e:
        return jsonify({'error': str(e)}), 502

    return jsonify(data)


@sap_api_bp.route('/orders/<int:doc_entry>/rdr1-fg-lines')
@login_required
def rdr1_fg_lines(doc_entry: int):
    """RDR1-style FG lines from live SAP Service Layer."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    try:
        client = SAPClient()
        try:
            rows = client.fetch_rdr1_fg_lines(doc_entry)
        finally:
            client.logout()
    except SAPClientError as e:
        return jsonify({'error': str(e)}), 502

    return jsonify(rows)


@sap_api_bp.route('/manufacturing-snapshot')
@login_required
def manufacturing_snapshot():
    """Live SAP overview for manufacturing dashboard (production orders + open SO)."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    from app.services.sap_mfg_snapshot import fetch_sap_manufacturing_snapshot

    try:
        po_limit = min(int(request.args.get('po_limit', 25)), 50)
    except ValueError:
        po_limit = 25
    try:
        so_limit = min(int(request.args.get('so_limit', 15)), 50)
    except ValueError:
        so_limit = 15

    try:
        return jsonify(fetch_sap_manufacturing_snapshot(po_limit=po_limit, so_limit=so_limit))
    except SAPClientError as e:
        return jsonify({'error': str(e), 'connected': False}), 502
    except Exception:
        _log.exception('GET /api/sap/manufacturing-snapshot')
        return jsonify({'error': 'Unexpected error loading SAP snapshot.'}), 500


@sap_api_bp.route('/mirror/status')
@login_required
def mirror_status():
    """Last SAP mirror sync timestamps and row counts."""
    st = _mirror_state()
    if not st:
        return jsonify({
            'configured': _configured(),
            'last_full_sync_at': None,
            'last_customer_sync_at': None,
            'last_order_sync_at': None,
            'last_item_sync_at': None,
            'customer_row_count': None,
            'order_line_row_count': None,
            'item_row_count': None,
            'last_error': None,
        })
    return jsonify({
        'configured': _configured(),
        'last_full_sync_at': st.last_full_sync_at.isoformat() if st.last_full_sync_at else None,
        'last_customer_sync_at': st.last_customer_sync_at.isoformat() if st.last_customer_sync_at else None,
        'last_order_sync_at': (
            st.last_order_sync_at.isoformat()
            if getattr(st, 'last_order_sync_at', None)
            else None
        ),
        'last_item_sync_at': st.last_item_sync_at.isoformat() if st.last_item_sync_at else None,
        'customer_row_count': st.customer_row_count,
        'order_line_row_count': getattr(st, 'order_line_row_count', None),
        'item_row_count': st.item_row_count,
        'last_error': st.last_error,
    })


@sap_api_bp.route('/mirror/refresh')
@login_required
def mirror_refresh():
    """Kick off a background full (or scoped) refresh of SAP mirror tables."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    scope = (request.args.get('scope') or 'all').strip().lower()
    card_code = (request.args.get('card_code') or '').strip()
    if scope not in ('all', 'customers', 'customers_force'):
        scope = 'all'

    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            try:
                from app.services.sap_mirror_sync import (
                    run_full_mirror_sync,
                    sync_customers_full_and_new_customer_orders,
                )
                if scope == 'customers_force':
                    sync_customers_full_and_new_customer_orders()
                else:
                    run_full_mirror_sync(app, scope=scope)
            except Exception:
                _log.exception('SAP mirror refresh failed scope=%s', scope)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'started': True, 'scope': scope})


@sap_api_bp.route('/mirror/merged-fg-lines')
@login_required
def mirror_merged_fg_lines():
    """All open SO FG lines for a CardCode from live SAP."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    card_code = request.args.get('card_code', '').strip()
    if not card_code:
        return jsonify({'error': 'card_code is required.'}), 400

    try:
        client = SAPClient()
        try:
            # Fetch all open SOs for this customer
            orders = client.fetch_open_sales_orders_ordr(card_code)
            lines = []
            for order in orders:
                doc_entry = order.get('doc_entry')
                if not doc_entry:
                    continue
                due = order.get('doc_due_date')
                fg_lines = client.fetch_rdr1_fg_lines(doc_entry)
                for fl in fg_lines:
                    val = order.get('doc_num') or order.get('so_no')
                    fl['so_no'] = str(val) if val is not None else ''
                    fl['doc_entry'] = doc_entry
                    fl['doc_due_date'] = due
                    w = fl.get('carton_width_mm')
                    h = fl.get('carton_height_mm')
                    fl['sap_carton_width_mm'] = w
                    fl['sap_carton_height_mm'] = h
                    lines.append(fl)
        finally:
            client.logout()
    except SAPClientError as e:
        return jsonify({'error': str(e)}), 502

    return jsonify({'mirror_ready': True, 'lines': lines})


@sap_api_bp.route('/ocrd-customers')
@login_required
def ocrd_customers():
    """OCRD via Service Layer: CardCode + CardName (Series filter from SAP_OCRD_SERIES)."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    from app.services.sap_mjd1 import distinct_ocrd_series_customers

    try:
        client = SAPClient()
        try:
            rows = distinct_ocrd_series_customers(client)
        finally:
            client.logout()
    except SAPClientError as e:
        _log.error('GET /api/sap/ocrd-customers SAPClientError: %s', e)
        mirror_rows = [
            {'code': row['code'], 'name': row['name'] or row['code']}
            for row in _customer_rows_from_mirror()
            if row.get('code')
        ]
        if mirror_rows:
            _log.warning('GET /api/sap/ocrd-customers using mirror fallback')
            return jsonify(mirror_rows)
        return jsonify({'error': str(e)}), 502
    except Exception:
        _log.exception('GET /api/sap/ocrd-customers unexpected error')
        return jsonify({'error': 'Unexpected error loading OCRD customers.'}), 500

    return jsonify(rows)


# --- SAP UDT @MJD1 (OData U_MJD1) / OMJD+MJD1Collection: customer → open U_SoNo → U_FGCode ---


@sap_api_bp.route('/mjd1/customers')
@login_required
def mjd1_customers():
    """Customer names for the MJD1 dropdown (see SAP_MJD1_CUSTOMER_LIST_SOURCE)."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    from app.services.sap_mjd1 import (
        customer_name_from_row,
        distinct_bp_customer_names,
        distinct_ocrd_series_customers,
        fetch_mjd1_rows,
        mjd1_customer_list_for_dropdown,
    )

    list_src = (current_app.config.get('SAP_MJD1_CUSTOMER_LIST_SOURCE') or 'mjd1').strip().lower()
    rows = []
    try:
        client = SAPClient()
        try:
            if list_src == 'ocrd_series':
                names = distinct_ocrd_series_customers(client)
            elif list_src == 'business_partners':
                names = distinct_bp_customer_names(client)
            else:
                rows = fetch_mjd1_rows(client)
                names = mjd1_customer_list_for_dropdown(client, rows)
        finally:
            client.logout()
    except SAPClientError as e:
        _log.error('GET /api/sap/mjd1/customers SAPClientError: %s', e)
        return jsonify(mjd1_error_payload(str(e))), 502
    except Exception:
        _log.exception('GET /api/sap/mjd1/customers unexpected error')
        return jsonify({'error': 'Unexpected error loading MJD1 customers.'}), 500

    _log.info(
        'mjd1/customers: list_source=%s -> %s name(s); MJD1 rows=%s',
        list_src,
        len(names),
        len(rows),
    )
    if rows and not names:
        r0 = rows[0]
        _log.warning(
            'mjd1/customers: no customer names resolved; check SAP_MJD1_FIELD_CUSTOMER_NAME and '
            'SAP_MJD1_CUSTOMER_NAME_FALLBACKS. sample_keys=%s sample_resolved=%r',
            list(r0.keys())[:45],
            customer_name_from_row(r0),
        )
    return jsonify(names)


@sap_api_bp.route('/mjd1/sales-orders')
@login_required
def mjd1_sales_orders():
    """Open sales orders (U_SoNo) for a customer, filtered by SAP Orders bost_Open."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    customer = request.args.get('customer', '').strip()
    if not customer:
        return jsonify({'error': 'customer is required.'}), 400

    from app.services.sap_mjd1 import fetch_mjd1_rows, open_sales_orders_for_customer

    try:
        client = SAPClient()
        try:
            rows = fetch_mjd1_rows(client)
            out = open_sales_orders_for_customer(client, rows, customer)
        finally:
            client.logout()
    except SAPClientError as e:
        _log.error(
            'GET /api/sap/mjd1/sales-orders SAPClientError customer=%r: %s',
            customer,
            e,
        )
        return jsonify(mjd1_error_payload(str(e))), 502
    except Exception:
        _log.exception(
            'GET /api/sap/mjd1/sales-orders unexpected error customer=%r',
            customer,
        )
        return jsonify({'error': 'Unexpected error loading sales orders.'}), 500

    return jsonify(out)


@sap_api_bp.route('/mjd1/fg-lines')
@login_required
def mjd1_fg_lines():
    """FG lines (U_FGCode, qty, name) for a customer + U_SoNo."""
    if not _configured():
        return jsonify({'error': 'SAP Service Layer URL is not configured.'}), 503

    customer = request.args.get('customer', '').strip()
    card_code = request.args.get('card_code', '').strip()
    so_no = request.args.get('so_no', '').strip()
    if (not customer and not card_code) or not so_no:
        return jsonify({'error': 'customer (or card_code) and so_no are required.'}), 400

    from app.services.sap_mjd1 import fetch_mjd1_rows, fg_lines_for_customer_so

    try:
        client = SAPClient()
        try:
            rows = fetch_mjd1_rows(client)
        finally:
            client.logout()
    except SAPClientError as e:
        _log.error(
            'GET /api/sap/mjd1/fg-lines SAPClientError customer=%r so_no=%r: %s',
            customer,
            so_no,
            e,
        )
        return jsonify(mjd1_error_payload(str(e))), 502
    except Exception:
        _log.exception(
            'GET /api/sap/mjd1/fg-lines unexpected error customer=%r so_no=%r',
            customer,
            so_no,
        )
        return jsonify({'error': 'Unexpected error loading FG lines.'}), 500

    return jsonify(fg_lines_for_customer_so(rows, customer, so_no, card_code or None))


@sap_api_bp.route('/items/batch-create', methods=['POST'])
@login_required
def batch_create_items():
    """Create multiple item codes in SAP.
    Expects JSON: { "items": [ {"item_code": "...", "item_name": "...", "base_fg_code": "..."}, ... ] }
    """
    if not _configured():
        return jsonify({'error': 'SAP not configured'}), 503

    payload = request.get_json() or {}
    items = payload.get('items', [])
    if not items:
        return jsonify({'error': 'No items provided'}), 400

    try:
        sap_client = SAPClient()
    except Exception as e:
        return jsonify({'error': f'SAP Connection failed: {e}'}), 503

    created_count = 0
    errors = []

    _log.info(f"Batch creating {len(items)} items in SAP.")
    for item in items:
        try:
            _log.debug(f"Ensuring item exists: {item.get('item_code')}")
            res = sap_client.ensure_item_exists(
                item_code=item.get('item_code', ''),
                item_name=item.get('item_name', ''),
                base_fg_code=item.get('base_fg_code', ''),
                item_group_code=115,
                sales_uom=item.get('uom', 'PCS')
            )
            if res.get('created'):
                _log.info(f"Item {item.get('item_code')} created successfully.")
                created_count += 1
            else:
                _log.info(f"Item {item.get('item_code')} already exists or verified.")
        except Exception as e:
            _log.error(f"Failed to create item {item.get('item_code')}: {e}")
            errors.append({'item': item.get('item_code'), 'error': str(e)})

    try:
        sap_client.logout()
    except Exception:
        pass

    return jsonify({
        'success': True,
        'created_count': created_count,
        'processed_count': len(items),
        'errors': errors
    })


@sap_api_bp.route('/items/<path:item_code>/stock')
@login_required
def item_stock(item_code: str):
    """Get current stock for an item from SAP."""
    if not _configured():
        return jsonify({'error': 'SAP not configured'}), 503

    try:
        client = SAPClient()
        try:
            item = client.fetch_item(item_code)
            # QuantityOnStock is the total stock across all warehouses
            stock = item.get('QuantityOnStock', 0)
            return jsonify({
                'item_code': item_code,
                'stock': stock
            })
        finally:
            client.logout()
    except SAPClientError as e:
        return jsonify({'error': str(e)}), 502
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {e}'}), 500
