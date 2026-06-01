from flask import Blueprint, jsonify, render_template, request, current_app

from ..extensions import db
from ..models import (
    Pattern,
    MaterialType,
    CoatingType,
    BomTemplate,
    GeneratedFGItem,
    GeneratedProcessItem,
    BomStructure,
    SapPushLog,
)
from ..services.item_code_generator import ItemCodeGeneratorService
from ..services.bom_generation import BomGenerationService
from ..services.sap_push_service import SapPushService
from ..services.item_master_service import sync_from_generator_save, mark_sap_pushed

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
    patterns = Pattern.query.order_by(Pattern.pattern_name).all()
    templates = BomTemplate.query.all()
    return render_template(
        'generator/index.html',
        materials=materials,
        coatings=coatings,
        patterns=patterns,
        templates=templates,
    )


@generator_bp.route('/generate', methods=['POST'])
def generate():
    data = request.form
    material = data.get('material_type')
    thickness = data.get('thickness')
    coating = (data.get('coating') or '').strip().upper()
    pattern_id = data.get('pattern_id')
    template_id = data.get('template_id')
    if not all([material, thickness, coating, pattern_id, template_id]):
        return jsonify({'error': 'invalid input'}), 400
    if coating not in _active_coating_codes():
        return jsonify({'error': 'invalid or inactive coating'}), 400
    pattern = Pattern.query.get(int(pattern_id))
    template = BomTemplate.query.get(int(template_id))
    if not pattern or not template:
        return jsonify({'error': 'pattern or template not found'}), 404
    fg_code = ItemCodeGeneratorService.generate_fg_code(
        material, thickness, pattern.pattern_code, coating
    )
    processes = template.process_sequence
    process_items = [f"{fg_code}-{p}" for p in processes]
    bom_chain = BomGenerationService.generate_chain(fg_code, processes)
    gen_payload = {
        'fg_code': fg_code,
        'process_items': process_items,
        'material_type': material,
        'thickness': thickness,
        'coating': coating,
    }
    sap_preview = SapPushService.preview_item_payloads(gen_payload, current_app.config)
    return jsonify({
        'fg_code': fg_code,
        'process_items': process_items,
        'coating': coating,
        'bom_chain': bom_chain,
        'sap_item_payloads': sap_preview,
    })

@generator_bp.route('/save', methods=['POST'])
def save_local():
    payload = request.json
    fg_code = payload.get('fg_code')
    template_id = payload.get('template_id')
    if not fg_code:
        return jsonify({'error': 'fg_code required'}), 400
    if not template_id:
        return jsonify({'error': 'template_id required'}), 400

    template_id = int(template_id)
    fg = GeneratedFGItem.query.filter_by(
        item_code=fg_code,
        bom_template_id=template_id,
    ).first()
    if fg:
        fg.material_type = payload.get('material_type') or fg.material_type
        fg.thickness = payload.get('thickness') or fg.thickness
        fg.coating = payload.get('coating') or fg.coating
        fg.pattern_id = payload.get('pattern_id') or fg.pattern_id
        db.session.add(fg)
        db.session.flush()
        GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).delete(synchronize_session=False)
    else:
        fg = GeneratedFGItem(
            item_code=fg_code,
            material_type=payload.get('material_type', ''),
            thickness=payload.get('thickness', ''),
            coating=payload.get('coating', ''),
            pattern_id=payload.get('pattern_id'),
            bom_template_id=template_id,
        )
        db.session.add(fg)
        db.session.flush()

    for pi in payload.get('process_items', []):
        gp = GeneratedProcessItem(
            fg_item_id=fg.id,
            process_code=pi.split('-')[-1],
            item_code=pi,
        )
        db.session.add(gp)

    BomStructure.query.filter_by(generated_fg_id=fg.id).delete(synchronize_session=False)
    chain = payload.get('bom_chain') or payload.get('bom_pairs') or []
    for node in chain:
        if isinstance(node, dict):
            parent = node.get('parent')
            child = node.get('child')
            proc = node.get('process')
            seq = [proc] if proc else None
        elif isinstance(node, (list, tuple)) and len(node) >= 3:
            parent, child, seq = node[0], node[1], node[2]
        else:
            continue
        b = BomStructure(
            generated_fg_id=fg.id,
            parent_item_code=parent,
            child_item_code=child,
            process_sequence=seq,
        )
        db.session.add(b)

    new_codes = sync_from_generator_save(payload, fg.id, current_app.config)
    db.session.commit()
    template = BomTemplate.query.get(template_id)
    msg = f'Saved {fg_code} / {template.template_name if template else template_id}'
    if new_codes:
        msg += f'. New in Item Master: {", ".join(new_codes)}'
    else:
        msg += '. All codes already in Item Master (no duplicates).'
    return jsonify({'status': 'saved', 'fg_id': fg.id, 'new_item_codes': new_codes, 'message': msg})

@generator_bp.route('/push', methods=['POST'])
def push_to_sap():
    """Push FG + component codes to SAP Item Master only (no BOM)."""
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
        log = client.push_item_master(payload)
        template_id = payload.get('template_id')
        fg = None
        if template_id:
            fg = GeneratedFGItem.query.filter_by(
                item_code=payload.get('fg_code'),
                bom_template_id=int(template_id),
            ).first()
        sync_from_generator_save(payload, fg.id if fg else None, current_app.config)
        pushed_codes = [
            r.get('item')
            for r in (log.get('responses') or [])
            if r.get('item') and r.get('status') in ('created', 'updated', 'skipped')
        ]
        mark_sap_pushed(pushed_codes)
        l = SapPushLog(
            request_payload=payload,
            response_payload=log,
            status=log.get('status', 'completed'),
        )
        db.session.add(l)
        db.session.commit()
        return jsonify({'status': 'ok', 'log': log})
    except Exception as e:
        current_app.logger.exception('SAP push failed')
        return jsonify({'error': str(e)}), 500
