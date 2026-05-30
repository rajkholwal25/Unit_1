from flask import Blueprint, jsonify, render_template, request

from ..models import BomTemplate, GeneratedFGItem, GeneratedProcessItem, Pattern
from ..services.generated_items import delete_generated_fg_item

item_codes_bp = Blueprint('item_codes', __name__)


def _build_item_rows():
    fg_items = (
        GeneratedFGItem.query
        .order_by(GeneratedFGItem.created_at.desc())
        .all()
    )
    rows = []
    for fg in fg_items:
        process_count = GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).count()
        pattern = Pattern.query.get(fg.pattern_id) if fg.pattern_id else None
        template = BomTemplate.query.get(fg.bom_template_id) if fg.bom_template_id else None
        rows.append({
            'id': fg.id,
            'item_code': fg.item_code,
            'material_type': fg.material_type,
            'thickness': fg.thickness,
            'pattern': pattern.pattern_name if pattern else '—',
            'template': template.template_name if template else '—',
            'process_count': process_count,
            'created_at': fg.created_at,
        })
    return rows


@item_codes_bp.route('/')
def list_item_codes():
    return render_template('item_codes/list.html', items=_build_item_rows())


@item_codes_bp.route('/ajax_delete', methods=['POST'])
def ajax_delete_item():
    data = request.get_json() or {}
    item_id = data.get('id')
    if not item_id:
        return jsonify({'error': 'id required'}), 400

    fg = GeneratedFGItem.query.get(int(item_id))
    if not fg:
        return jsonify({'error': 'Generated item not found'}), 404

    deleted_code = fg.item_code
    ok, err = delete_generated_fg_item(fg)
    if not ok:
        return jsonify({'error': err or 'Delete failed'}), 400

    return jsonify({'deleted_id': int(item_id), 'deleted_code': deleted_code}), 200
