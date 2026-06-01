from flask import Blueprint, current_app, jsonify, render_template, request

from ..models import BomTemplate, GeneratedFGItem, GeneratedProcessItem, ItemMaster, Pattern
from ..services.generated_items import delete_generated_fg_item
from ..services.item_master_service import item_exists, search_items
from ..services.sap_item_sync_service import SapItemSyncService

item_codes_bp = Blueprint('item_codes', __name__)


def _sap_configured():
    return bool(current_app.config.get('SAP_BASE_URL'))


def _sap_service():
    return SapItemSyncService(current_app.config)


def _row_dict(row):
    pattern = Pattern.query.get(row.pattern_id) if row.pattern_id else None
    template = BomTemplate.query.get(row.bom_template_id) if row.bom_template_id else None
    return {
        'id': row.id,
        'item_code': row.item_code,
        'item_name': row.item_name,
        'item_type': row.item_type,
        'parent_fg_code': row.parent_fg_code or '—',
        'process_code': row.process_code or '—',
        'material_type': row.material_type or '—',
        'thickness': row.thickness or '—',
        'coating': row.coating or '—',
        'pattern': pattern.pattern_name if pattern else '—',
        'template': template.template_name if template else '—',
        'warehouse_code': row.warehouse_code or '—',
        'items_group_code': row.items_group_code,
        'uom': row.invntry_uom or 'KGS',
        'sap_pushed': bool(row.sap_pushed),
        'generated_fg_id': row.generated_fg_id,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }


def _build_bom_variants():
    variants = []
    for fg in GeneratedFGItem.query.order_by(GeneratedFGItem.created_at.desc()).all():
        template = BomTemplate.query.get(fg.bom_template_id) if fg.bom_template_id else None
        proc_count = GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).count()
        variants.append({
            'id': fg.id,
            'item_code': fg.item_code,
            'template': template.template_name if template else '—',
            'process_count': proc_count,
            'created_at': fg.created_at,
        })
    return variants


@item_codes_bp.route('/')
def list_item_codes():
    q = request.args.get('q', '').strip()
    tab = request.args.get('tab', 'local')
    items = [_row_dict(r) for r in search_items(q)]
    total = ItemMaster.query.count()
    return render_template(
        'item_codes/list.html',
        items=items,
        search_q=q,
        total_count=total,
        bom_variants=_build_bom_variants(),
        active_tab=tab,
        sap_configured=_sap_configured(),
        sap_company=current_app.config.get('SAP_COMPANY_DB') or '',
    )


@item_codes_bp.route('/ajax_search', methods=['GET'])
def ajax_search():
    q = request.args.get('q', '').strip()
    code = request.args.get('code', '').strip()
    if code:
        exists = item_exists(code)
        return jsonify({
            'code': code,
            'exists': exists,
            'message': 'Item exists in Item Master' if exists else 'Item not found in Item Master',
        })
    rows = [_row_dict(r) for r in search_items(q, limit=50)]
    return jsonify({'results': rows, 'count': len(rows)})


@item_codes_bp.route('/ajax_delete', methods=['POST'])
def ajax_delete_item():
    """Delete one saved BOM variant; Item Master catalog entries are kept."""
    data = request.get_json() or {}
    fg_id = data.get('fg_id') or data.get('id')
    if not fg_id:
        return jsonify({'error': 'fg_id required'}), 400

    fg = GeneratedFGItem.query.get(int(fg_id))
    if not fg:
        return jsonify({'error': 'FG item not found'}), 404

    deleted_code = fg.item_code
    ok, err = delete_generated_fg_item(fg)
    if not ok:
        return jsonify({'error': err or 'Delete failed'}), 400

    return jsonify({'deleted_fg_id': int(fg_id), 'deleted_code': deleted_code}), 200


@item_codes_bp.route('/sap/list', methods=['GET'])
def sap_list_items():
    if not _sap_configured():
        return jsonify({'error': 'SAP is not configured in .env'}), 400
    try:
        skip = int(request.args.get('skip', 0))
        top = min(int(request.args.get('top', 50)), 100)
        search = request.args.get('q', '').strip()
        data = _sap_service().list_items(skip=skip, top=top, search=search)
        return jsonify(data)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502


@item_codes_bp.route('/sap/sync', methods=['POST'])
def sap_sync():
    if not _sap_configured():
        return jsonify({'error': 'SAP is not configured in .env'}), 400
    try:
        result = _sap_service().sync_all()
        return jsonify({'status': 'ok', **result})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502


@item_codes_bp.route('/sap/update', methods=['POST'])
def sap_update_item():
    if not _sap_configured():
        return jsonify({'error': 'SAP is not configured in .env'}), 400
    data = request.get_json() or {}
    code = (data.get('item_code') or '').strip()
    if not code:
        return jsonify({'error': 'item_code required'}), 400
    try:
        result = _sap_service().update_item(
            code,
            item_name=data.get('item_name'),
            items_group_code=data.get('items_group_code'),
        )
        return jsonify({'status': 'ok', **result})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502


@item_codes_bp.route('/sap/delete', methods=['POST'])
def sap_delete_item():
    if not _sap_configured():
        return jsonify({'error': 'SAP is not configured in .env'}), 400
    data = request.get_json() or {}
    code = (data.get('item_code') or data.get('fg_code') or '').strip()
    cascade = bool(data.get('cascade', data.get('delete_components', False)))
    if not code:
        return jsonify({'error': 'item_code or fg_code required'}), 400
    try:
        svc = _sap_service()
        if cascade:
            result = svc.delete_fg_with_components(code)
        else:
            result = svc.delete_item(code)
        return jsonify({'status': 'ok', **result})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502


@item_codes_bp.route('/sap/components', methods=['GET'])
def sap_list_components():
    fg = request.args.get('fg_code', '').strip()
    if not fg:
        return jsonify({'error': 'fg_code required'}), 400
    if not _sap_configured():
        return jsonify({'error': 'SAP is not configured'}), 400
    try:
        codes = _sap_service().find_component_codes(fg)
        return jsonify({'fg_code': fg, 'components': codes})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502
