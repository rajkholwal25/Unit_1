from flask import Blueprint, jsonify, render_template, request, current_app

from ..extensions import db
from ..models import (
    Pattern,
    MaterialType,
    CoatingType,
    GeneratedFGItem,
    SapPushLog,
)
from ..services.item_code_generator import ItemCodeGeneratorService
from ..services.unit1_item_naming import resolve_fg_display_name
from ..services.sap_push_service import SapPushService
from ..services.item_master_service import sync_from_generator_save, mark_sap_pushed
from ..utils.thickness import parse_thickness

generator_bp = Blueprint('generator', __name__)


def _active_coating_codes():
    return {
        c.code.upper()
        for c in CoatingType.query.filter_by(is_active=True).all()
    }


@generator_bp.route('/', methods=['GET'])
def index():
    materials = MaterialType.query.filter_by(is_active=True).all()
    coatings = CoatingType.query.filter_by(is_active=True).order_by(CoatingType.code).all()
    return render_template(
        'generator/index.html',
        materials=materials,
        coatings=coatings,
    )


@generator_bp.route('/generate', methods=['POST'])
def generate():
    """Generate FG item code only. Routing/process codes are created at job + BOM time."""
    data = request.form
    material = data.get('material_type')
    thickness = parse_thickness(data.get('thickness'))
    coating = (data.get('coating') or '').strip().upper()
    pattern_id = data.get('pattern_id')
    if not all([material, thickness is not None, coating, pattern_id]):
        return jsonify({'error': 'invalid input — select material, thickness, pattern, and coating'}), 400
    if coating not in _active_coating_codes():
        return jsonify({'error': 'invalid or inactive coating'}), 400
    pattern = Pattern.query.get(int(pattern_id))
    if not pattern:
        return jsonify({'error': 'pattern not found'}), 404
    try:
        fg_code = ItemCodeGeneratorService.generate_fg_code(
            material, thickness, pattern.pattern_code, coating
        )
        fg_name = ItemCodeGeneratorService.generate_fg_display_name(
            material, thickness, pattern.pattern_name, coating
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    gen_payload = {
        'fg_code': fg_code,
        'fg_name': fg_name,
        'process_items': [],
        'material_type': material,
        'thickness': thickness,
        'coating': coating,
        'pattern_id': int(pattern_id),
    }
    sap_preview = SapPushService.preview_item_payloads(gen_payload, current_app.config)
    return jsonify({
        'fg_code': fg_code,
        'fg_name': fg_name,
        'process_items': [],
        'coating': coating,
        'bom_chain': [],
        'sap_item_payloads': sap_preview,
    })


@generator_bp.route('/save', methods=['POST'])
def save_local():
    """Save FG code to local catalog (no BOM template / no process routing items)."""
    payload = request.json or {}
    fg_code = payload.get('fg_code')
    if not fg_code:
        return jsonify({'error': 'fg_code required'}), 400

    fg = GeneratedFGItem.query.filter_by(
        item_code=fg_code,
        bom_template_id=None,
    ).first()
    thickness = parse_thickness(payload.get('thickness'))
    if thickness is None and payload.get('thickness') not in (None, ''):
        return jsonify({'error': 'thickness must be a number'}), 400

    if fg:
        fg.material_type = payload.get('material_type') or fg.material_type
        if thickness is not None:
            fg.thickness = thickness
        fg.coating = payload.get('coating') or fg.coating
        fg.pattern_id = payload.get('pattern_id') or fg.pattern_id
        db.session.add(fg)
        db.session.flush()
    else:
        if thickness is None:
            return jsonify({'error': 'thickness is required'}), 400
        fg = GeneratedFGItem(
            item_code=fg_code,
            material_type=payload.get('material_type', ''),
            thickness=thickness,
            coating=payload.get('coating', ''),
            pattern_id=payload.get('pattern_id'),
            bom_template_id=None,
        )
        db.session.add(fg)
        db.session.flush()

    new_codes = sync_from_generator_save(payload, fg.id, current_app.config)
    db.session.commit()
    msg = f'Saved FG {fg_code}'
    if new_codes:
        msg += f'. New in Item Master: {", ".join(new_codes)}'
    else:
        msg += '. Already in Item Master (no duplicates).'
    return jsonify({'status': 'saved', 'fg_id': fg.id, 'new_item_codes': new_codes, 'message': msg})


@generator_bp.route('/push', methods=['POST'])
def push_to_sap():
    """Push FG code to SAP Item Master only (no routing components)."""
    payload = request.json or {}
    if not current_app.config.get('SAP_BASE_URL'):
        return jsonify({
            'error': (
                'SAP is not configured. Set SAP_SERVICE_LAYER_URL, SAP_COMPANY_DB, '
                'SAP_USERNAME, and SAP_PASSWORD in your .env file.'
            ),
        }), 400
    client = SapPushService(current_app.config)
    try:
        push_payload = dict(payload)
        push_payload['process_items'] = []
        log = client.push_item_master(push_payload)
        fg = GeneratedFGItem.query.filter_by(
            item_code=payload.get('fg_code'),
            bom_template_id=None,
        ).first()
        sync_from_generator_save(push_payload, fg.id if fg else None, current_app.config)
        pushed_codes = [
            r.get('item')
            for r in (log.get('responses') or [])
            if r.get('item') and r.get('status') in ('created', 'updated', 'skipped')
        ]
        mark_sap_pushed(pushed_codes)
        l = SapPushLog(
            request_payload=push_payload,
            response_payload=log,
            status=log.get('status', 'completed'),
        )
        db.session.add(l)
        db.session.commit()
        return jsonify({'status': 'ok', 'log': log})
    except Exception as e:
        current_app.logger.exception('SAP push failed')
        return jsonify({'error': str(e)}), 500
