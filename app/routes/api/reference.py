"""api/reference.py — Internal JSON API endpoints for frontend typeahead/dropdown use.

These are NOT public APIs — they serve the frontend JS only.
All return JSON. All require login.
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required

from app.models.reference import ProcessMaster
from app.models.sap_mirror import SapCustomerMirror, SapItemMirror

ref_api_bp = Blueprint('ref_api', __name__, url_prefix='/api/ref')


@ref_api_bp.route('/customers')
@login_required
def customers():
    """Search customers by name or code. Returns JSON list."""
    q = request.args.get('q', '').strip()
    limit = min(int(request.args.get('limit', 30)), 100)

    query = SapCustomerMirror.query
    if q:
        query = query.filter(
            SapCustomerMirror.card_name.ilike(f'%{q}%') |
            SapCustomerMirror.card_code.ilike(f'%{q}%')
        )
    results = query.order_by(SapCustomerMirror.card_name).limit(limit).all()

    return jsonify([
        {'code': c.card_code, 'name': c.card_name, 'phone': c.phone}
        for c in results
    ])


@ref_api_bp.route('/items')
@login_required
def items():
    """Search items by code or name. Optional ?type=fg|raw_material|consumable.

    Rows come from ``sap_item_mirror``, which is populated from SAP OITM with
    **active** items only (``Valid`` = ``tYES``) after the last item sync.
    """
    q = request.args.get('q', '').strip()
    item_type = request.args.get('type', '').strip()
    limit = min(int(request.args.get('limit', 50)), 200)

    query = SapItemMirror.query
    if item_type:
        query = query.filter_by(item_type=item_type)
    if q:
        query = query.filter(
            SapItemMirror.item_name.ilike(f'%{q}%') |
            SapItemMirror.item_code.ilike(f'%{q}%')
        )
    results = query.order_by(SapItemMirror.item_name).limit(limit).all()

    return jsonify([
        {'code': i.item_code, 'name': i.item_name, 'uom': i.uom, 'type': i.item_type}
        for i in results
    ])


@ref_api_bp.route('/processes')
@login_required
def processes():
    """Return Unit 1 active processes (EMB, SLT, MET, COT by default)."""
    from app.services.unit1_processes import UNIT1_PROCESS_CODES

    category = request.args.get('category', '').strip()
    query = ProcessMaster.query.filter_by(is_active=True)
    query = query.filter(ProcessMaster.process_code.in_(UNIT1_PROCESS_CODES))
    if category:
        query = query.filter_by(category=category)
    results = query.order_by(ProcessMaster.process_code).all()

    return jsonify([
        {
            'code': p.process_code,
            'name': p.name,
            'category': p.category,
            'workcenter': p.default_workcenter,
        }
        for p in results
    ])
