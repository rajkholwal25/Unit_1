from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request

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
from ..services.bom_service import (
    bom_preview_dict,
    bom_lines_for_sap,
    default_yield_loss_pct,
    rebuild_bom_for_fg,
    search_raw_materials,
)
from ..services.sap_push_service import SapPushService
from ..services.item_master_service import sync_from_generator_save, mark_sap_pushed, item_exists
from ..utils.thickness import parse_thickness
from ..services.sap_item_sync_service import SapItemSyncService
from ..services.bom_generation import warehouse_for_parent, RM_WAREHOUSE
from ..services.generated_items import delete_generated_fg_item

bom_builder_bp = Blueprint('bom_builder', __name__)


def _active_coating_codes():
    return {c.code.upper() for c in CoatingType.query.filter_by(is_active=True).all()}


@bom_builder_bp.route('/', methods=['GET'])
def index():
    materials = MaterialType.query.filter_by(is_active=True).all()
    coatings = CoatingType.query.filter_by(is_active=True).order_by(CoatingType.code).all()
    patterns = Pattern.query.order_by(Pattern.pattern_name).all()
    templates = BomTemplate.query.all()
    return render_template(
        'bom_builder/index.html',
        materials=materials,
        coatings=coatings,
        patterns=patterns,
        templates=templates,
        yield_default=default_yield_loss_pct(current_app.config),
        sap_configured=bool(current_app.config.get('SAP_BASE_URL')),
    )


@bom_builder_bp.route('/generate', methods=['POST'])
def generate():
    data = request.form
    material = data.get('material_type')
    thickness = parse_thickness(data.get('thickness'))
    coating = (data.get('coating') or '').strip().upper()
    pattern_id = data.get('pattern_id')
    template_id = data.get('template_id')
    if not all([material, thickness is not None, coating, pattern_id, template_id]):
        return jsonify({'error': 'invalid input — thickness must be a number'}), 400
    if coating not in _active_coating_codes():
        return jsonify({'error': 'invalid or inactive coating'}), 400
    pattern = Pattern.query.get(int(pattern_id))
    template = BomTemplate.query.get(int(template_id))
    if not pattern or not template:
        return jsonify({'error': 'pattern or template not found'}), 404
    try:
        fg_code = ItemCodeGeneratorService.generate_fg_code(
            material, thickness, pattern.pattern_code, coating,
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    processes = template.process_sequence
    process_items = [f'{fg_code}-{p}' for p in processes]
    loss = default_yield_loss_pct(current_app.config)
    rm = (data.get('raw_material_item_code') or '').strip()

    chain = BomGenerationService.generate_chain(
        fg_code, processes,
        raw_material_code=rm or None,
        yield_loss_pct=loss,
    )

    return jsonify({
        'fg_code': fg_code,
        'process_items': process_items,
        'processes': processes,
        'template_id': int(template_id),
        'material_type': material,
        'thickness': thickness,
        'coating': coating,
        'pattern_id': int(pattern_id),
        'bom': bom_preview_dict(chain, yield_loss_pct=loss),
    })


@bom_builder_bp.route('/raw-materials', methods=['GET'])
def raw_materials_search():
    fg_code = request.args.get('fg_code', '').strip()
    if not fg_code:
        return jsonify({'error': 'fg_code required'}), 400
    process_items = request.args.getlist('process') or []
    if not process_items:
        raw = request.args.get('process_items', '')
        if raw:
            process_items = [x.strip() for x in raw.split(',') if x.strip()]
    q = request.args.get('q', '').strip()
    # Local search is already case-insensitive (ILIKE). If the user hasn't synced
    # the SAP catalog yet, fall back to a live SAP search and upsert results.
    results = search_raw_materials(fg_code, process_items, q)
    if q and not results and current_app.config.get('SAP_BASE_URL'):
        try:
            svc = SapItemSyncService(current_app.config)
            uom = (current_app.config.get('SAP_UOM_CODE') or 'KGS').strip().upper()
            # First try exact ItemCode fetch (fast for codes like RMC0000001).
            exact = svc.get_item(q)
            if exact:
                svc._upsert_from_sap(exact, uom)  # noqa: SLF001
            else:
                chunk = svc.list_items(skip=0, top=50, search=q)
                for row in chunk.get('items') or []:
                    svc._upsert_from_sap(row, uom)  # noqa: SLF001
            db.session.commit()
            results = search_raw_materials(fg_code, process_items, q)
        except Exception:
            # If SAP is unreachable, still return local results (empty).
            pass
    return jsonify({'results': results})


@bom_builder_bp.route('/sync-item-master', methods=['POST'])
def sync_item_master_from_sap():
    if not current_app.config.get('SAP_BASE_URL'):
        return jsonify({'error': 'SAP is not configured in .env'}), 400
    try:
        svc = SapItemSyncService(current_app.config)
        result = svc.sync_all()
        return jsonify({'status': 'ok', **result})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502


@bom_builder_bp.route('/created', methods=['GET'])
def created_boms_list():
    rows = GeneratedFGItem.query.order_by(GeneratedFGItem.created_at.desc()).limit(200).all()
    out = []
    for fg in rows:
        tmpl = BomTemplate.query.get(fg.bom_template_id) if fg.bom_template_id else None
        out.append({
            'fg_id': fg.id,
            'fg_code': fg.item_code,
            'template': tmpl.template_name if tmpl else '—',
            'raw_material_item_code': fg.raw_material_item_code,
            'yield_loss_pct': float(fg.yield_loss_pct or 2),
            'created_at': fg.created_at.isoformat() if fg.created_at else None,
            'sap_bom_pushed_at': fg.sap_bom_pushed_at.isoformat() if fg.sap_bom_pushed_at else None,
        })
    return jsonify({'items': out})


@bom_builder_bp.route('/created/<int:fg_id>', methods=['GET'])
def created_bom_detail(fg_id: int):
    fg = GeneratedFGItem.query.get(fg_id)
    if not fg:
        return jsonify({'error': 'BOM not found'}), 404
    lines = (
        BomStructure.query.filter_by(generated_fg_id=fg.id)
        .order_by(BomStructure.sort_order)
        .all()
    )
    out = []
    for r in lines:
        qty = float(r.quantity) if r.quantity is not None else None
        child_wh = r.warehouse_code or (RM_WAREHOUSE if (r.line_type or '') == 'raw_material' else '—')
        out.append({
            'parent': r.parent_item_code,
            'child': r.child_item_code,
            'line_type': r.line_type or 'process',
            'quantity': qty,
            'parent_warehouse': warehouse_for_parent(r.parent_item_code, fg.item_code),
            'child_warehouse': child_wh,
        })
    return jsonify({
        'fg_id': fg.id,
        'fg_code': fg.item_code,
        'raw_material_item_code': fg.raw_material_item_code,
        'yield_loss_pct': float(fg.yield_loss_pct or 2),
        'lines': out,
    })


@bom_builder_bp.route('/created/<int:fg_id>/delete', methods=['POST'])
def created_bom_delete(fg_id: int):
    """
    Delete created BOM:
    - local: remove saved variant (GeneratedFGItem + structures + process items)
    - sap: delete ProductTrees for all parent nodes in this BOM (BOM only, not items)
    """
    mode = (request.get_json() or {}).get('mode') or 'local'
    mode = str(mode).lower().strip()
    if mode not in ('local', 'sap', 'both'):
        return jsonify({'error': 'mode must be local|sap|both'}), 400

    fg = GeneratedFGItem.query.get(fg_id)
    if not fg:
        return jsonify({'error': 'BOM not found'}), 404

    sap_result = None
    if mode in ('sap', 'both'):
        if not current_app.config.get('SAP_BASE_URL'):
            return jsonify({'error': 'SAP is not configured in .env'}), 400
        # Delete ProductTrees for every parent in the BOM chain.
        parent_codes = sorted({
            r.parent_item_code
            for r in BomStructure.query.filter_by(generated_fg_id=fg.id).all()
            if r.parent_item_code
        })
        try:
            sap_result = SapPushService(current_app.config).delete_bom_trees(parent_codes)
        except Exception as exc:
            current_app.logger.exception('SAP BOM delete failed')
            return jsonify({'error': str(exc)}), 502

    local_result = None
    if mode in ('local', 'both'):
        ok, err = delete_generated_fg_item(fg)
        if not ok:
            return jsonify({'error': err or 'Local delete failed'}), 400
        local_result = {'status': 'deleted', 'fg_id': fg_id}

    return jsonify({'status': 'ok', 'mode': mode, 'sap': sap_result, 'local': local_result})


@bom_builder_bp.route('/preview', methods=['POST'])
def preview_bom():
    data = request.get_json() or {}
    fg_code = data.get('fg_code')
    processes = data.get('processes') or []
    rm = (data.get('raw_material_item_code') or '').strip()
    if not fg_code or not processes:
        return jsonify({'error': 'fg_code and processes required'}), 400
    if not rm:
        return jsonify({'error': 'raw_material_item_code required'}), 400
    try:
        loss = float(data.get('yield_loss_pct', default_yield_loss_pct(current_app.config)))
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid yield_loss_pct'}), 400
    chain = BomGenerationService.generate_chain(
        fg_code, processes, raw_material_code=rm, yield_loss_pct=loss,
    )
    return jsonify(bom_preview_dict(chain, yield_loss_pct=loss))


@bom_builder_bp.route('/save', methods=['POST'])
def save_local():
    payload = request.get_json() or {}
    fg_code = payload.get('fg_code')
    template_id = payload.get('template_id')
    rm = (payload.get('raw_material_item_code') or '').strip()
    if not fg_code or not template_id:
        return jsonify({'error': 'fg_code and template_id required'}), 400
    if not rm:
        return jsonify({'error': 'raw_material_item_code required'}), 400
    if not item_exists(rm):
        # Try pulling the item from SAP and upserting into local mirror.
        if current_app.config.get('SAP_BASE_URL'):
            try:
                svc = SapItemSyncService(current_app.config)
                uom = (current_app.config.get('SAP_UOM_CODE') or 'KGS').strip().upper()
                exact = svc.get_item(rm)
                if exact:
                    svc._upsert_from_sap(exact, uom)  # noqa: SLF001
                    db.session.commit()
            except Exception:
                pass
        if not item_exists(rm):
            return jsonify({'error': f'"{rm}" not in Item Master — sync from SAP first'}), 400

    template_id = int(template_id)
    thickness = parse_thickness(payload.get('thickness'))
    if thickness is None:
        return jsonify({'error': 'thickness required'}), 400

    fg = GeneratedFGItem.query.filter_by(item_code=fg_code, bom_template_id=template_id).first()
    if fg:
        fg.material_type = payload.get('material_type') or fg.material_type
        fg.thickness = thickness
        fg.coating = payload.get('coating') or fg.coating
        fg.pattern_id = payload.get('pattern_id') or fg.pattern_id
        GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).delete(synchronize_session=False)
    else:
        fg = GeneratedFGItem(
            item_code=fg_code,
            material_type=payload.get('material_type', ''),
            thickness=thickness,
            coating=payload.get('coating', ''),
            pattern_id=payload.get('pattern_id'),
            bom_template_id=template_id,
        )
        db.session.add(fg)
        db.session.flush()

    for pi in payload.get('process_items', []):
        db.session.add(GeneratedProcessItem(
            fg_item_id=fg.id,
            process_code=pi.split('-')[-1],
            item_code=pi,
        ))

    loss = float(payload.get('yield_loss_pct', default_yield_loss_pct(current_app.config)))
    rebuild_bom_for_fg(fg, raw_material_code=rm, yield_loss_pct=loss, config=current_app.config)
    sync_from_generator_save(payload, fg.id, current_app.config)
    db.session.commit()
    return jsonify({
        'status': 'saved',
        'fg_id': fg.id,
        'message': f'Saved {fg_code} with BOM ({len(bom_lines_for_sap(fg))} levels)',
    })


@bom_builder_bp.route('/push', methods=['POST'])
def push_sap():
    if not current_app.config.get('SAP_BASE_URL'):
        return jsonify({'error': 'SAP is not configured in .env'}), 400

    payload = request.get_json() or {}
    fg_code = payload.get('fg_code')
    template_id = payload.get('template_id')
    rm = (payload.get('raw_material_item_code') or '').strip()
    if not all([fg_code, template_id, rm]):
        return jsonify({'error': 'fg_code, template_id, raw_material_item_code required'}), 400

    fg = GeneratedFGItem.query.filter_by(
        item_code=fg_code, bom_template_id=int(template_id),
    ).first()
    if not fg:
        return jsonify({'error': 'Save BOM locally before pushing to SAP'}), 400
    if not fg.raw_material_item_code:
        rebuild_bom_for_fg(
            fg, raw_material_code=rm,
            yield_loss_pct=float(payload.get('yield_loss_pct', default_yield_loss_pct(current_app.config))),
            config=current_app.config,
        )
        db.session.commit()

    lines = bom_lines_for_sap(fg)
    if not lines:
        return jsonify({'error': 'No BOM lines'}), 400

    payload['raw_material_item_code'] = fg.raw_material_item_code or rm
    if not payload.get('process_items'):
        payload['process_items'] = [
            r.item_code for r in GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).all()
        ]

    try:
        svc = SapPushService(current_app.config)
        log = svc.push_items_and_bom(payload, lines)
        sync_from_generator_save(payload, fg.id, current_app.config)
        pushed = [r.get('item') for r in log.get('items', {}).get('responses', []) if r.get('status') == 'created']
        mark_sap_pushed(pushed)
        fg.sap_bom_pushed_at = datetime.utcnow()
        db.session.add(SapPushLog(
            request_payload={'fg_code': fg_code, 'bom_levels': len(lines)},
            response_payload=log,
            status='completed',
        ))
        db.session.commit()
        return jsonify({'status': 'ok', 'log': log})
    except Exception as exc:
        current_app.logger.exception('BOM builder SAP push failed')
        return jsonify({'error': str(exc)}), 502
