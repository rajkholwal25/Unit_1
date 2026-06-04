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


def _parse_generate_request():
    """Read generate form/JSON fields."""
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form
    return data


def _resolve_pattern_for_generate(data) -> tuple[Pattern | None, str | None]:
    """Resolve pattern by id, code, or name (active or inactive). Returns (row, error_message)."""
    pattern_id = data.get('pattern_id')
    pattern_row = None
    if pattern_id not in (None, ''):
        try:
            pattern_row = Pattern.query.get(int(pattern_id))
        except (TypeError, ValueError):
            pattern_row = None
    if pattern_row:
        return pattern_row, None

    hint = (
        (data.get('pattern_name') or data.get('pattern_input') or '').strip()
    )
    if not hint:
        return None, 'Select a pattern from the list or press Enter to create one'

    by_code = Pattern.query.filter(Pattern.pattern_code == hint).first()
    if by_code:
        return by_code, None
    by_name = Pattern.query.filter(
        db.func.lower(Pattern.pattern_name) == hint.lower()
    ).first()
    if by_name:
        return by_name, None
    partial = Pattern.query.filter(
        Pattern.is_active.is_(True),
        db.func.lower(Pattern.pattern_name).like(f'%{hint.lower()}%'),
    ).order_by(Pattern.pattern_code).first()
    if partial:
        return partial, None
    return None, f'Pattern not found: {hint}'


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
    data = _parse_generate_request()
    material = (data.get('material_type') or '').strip()
    thickness = parse_thickness(data.get('thickness'))
    coating = (data.get('coating') or '').strip().upper()

    if not material:
        return jsonify({'error': 'Select a material type'}), 400
    if thickness is None:
        return jsonify({'error': 'Thickness must be a valid number'}), 400
    if not coating:
        return jsonify({'error': 'Select a coating'}), 400
    if coating not in _active_coating_codes():
        return jsonify({'error': f'Coating "{coating}" is not active. Choose TR, NTR, etc.'}), 400

    pattern, pat_err = _resolve_pattern_for_generate(data)
    if pat_err:
        return jsonify({'error': pat_err}), 400
    if not pattern:
        return jsonify({'error': 'Pattern not found'}), 404

    try:
        fg_code = ItemCodeGeneratorService.generate_fg_code(
            material, thickness, pattern.pattern_code, coating
        )
        default_fg_name = ItemCodeGeneratorService.generate_fg_display_name(
            material, thickness, pattern.pattern_name, coating
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    if not default_fg_name:
        return jsonify({'error': 'Could not build FG name — check material, thickness, pattern, and coating'}), 400

    fg_name_hint = (data.get('fg_name') or '').strip()
    fg_name = fg_name_hint if fg_name_hint else default_fg_name

    gen_payload = {
        'fg_code': fg_code,
        'fg_name': fg_name,
        'prefer_fg_name': True,
        'process_items': [],
        'material_type': material,
        'thickness': thickness,
        'coating': coating,
        'pattern_id': int(pattern.id),
        'pattern_name': pattern.pattern_name,
        'pattern_code': pattern.pattern_code,
    }
    sap_preview = SapPushService.preview_item_payloads(gen_payload, current_app.config)
    return jsonify({
        'fg_code': fg_code,
        'fg_name': fg_name,
        'fg_name_default': default_fg_name,
        'pattern_name': pattern.pattern_name,
        'pattern_code': pattern.pattern_code,
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

    if payload.get('fg_name'):
        payload['prefer_fg_name'] = True
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
        if push_payload.get('fg_name'):
            push_payload['prefer_fg_name'] = True
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
