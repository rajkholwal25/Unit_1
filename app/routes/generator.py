from flask import Blueprint, render_template, request, jsonify, current_app, flash, redirect, url_for
from ..extensions import db
from ..models import Pattern, MaterialType, BomTemplate, GeneratedFGItem, GeneratedProcessItem, BomStructure, SapPushLog
from ..services.item_code_generator import ItemCodeGeneratorService
from ..services.bom_generation import BomGenerationService
from ..services.sap_push_service import SapPushService
from datetime import datetime

generator_bp = Blueprint('generator', __name__, template_folder='templates')

@generator_bp.route('/', methods=['GET'])
def index():
    materials = MaterialType.query.filter_by(is_active=True).all()
    patterns = Pattern.query.order_by(Pattern.pattern_name).all()
    templates = BomTemplate.query.all()
    return render_template('generator/index.html', materials=materials, patterns=patterns, templates=templates)

@generator_bp.route('/generate', methods=['POST'])
def generate():
    data = request.form
    material = data.get('material_type')
    thickness = data.get('thickness')
    pattern_id = data.get('pattern_id')
    template_id = data.get('template_id')
    # basic validation
    if not all([material, thickness, pattern_id, template_id]):
        return jsonify({'error':'invalid input'}), 400
    pattern = Pattern.query.get(int(pattern_id))
    template = BomTemplate.query.get(int(template_id))
    fg_code = ItemCodeGeneratorService.generate_fg_code(material, thickness, pattern.pattern_code)
    processes = template.process_sequence
    process_items = [f"{fg_code}-{p}" for p in processes]
    # generate hierarchy
    bom_chain = BomGenerationService.generate_chain(fg_code, processes)
    return jsonify({'fg_code':fg_code,'process_items':process_items,'bom_chain':bom_chain})

@generator_bp.route('/save', methods=['POST'])
def save_local():
    payload = request.json
    fg_code = payload.get('fg_code')
    if not fg_code:
        return jsonify({'error':'fg_code required'}), 400
    # upsert FG item: if exists, update fields; otherwise create
    fg = GeneratedFGItem.query.filter_by(item_code=fg_code).first()
    if fg:
        fg.material_type = payload.get('material_type') or fg.material_type
        fg.thickness = payload.get('thickness') or fg.thickness
        fg.pattern_id = payload.get('pattern_id') or fg.pattern_id
        fg.bom_template_id = payload.get('template_id') or fg.bom_template_id
        db.session.add(fg)
        db.session.flush()
        # remove existing process items for this FG and re-add
        GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).delete(synchronize_session=False)
    else:
        fg = GeneratedFGItem(item_code=fg_code, material_type=payload.get('material_type',''), thickness=payload.get('thickness',''), pattern_id=payload.get('pattern_id'), bom_template_id=payload.get('template_id'))
        db.session.add(fg)
        db.session.flush()

    for pi in payload.get('process_items', []):
        gp = GeneratedProcessItem(fg_item_id=fg.id, process_code=pi.split('-')[-1], item_code=pi)
        db.session.add(gp)

    # build bom_structures: accept 'bom_chain' (list of dicts parent/child/process) or 'bom_pairs'
    # delete existing structures where this FG is the top parent
    BomStructure.query.filter_by(parent_item_code=fg_code).delete(synchronize_session=False)
    chain = payload.get('bom_chain') or payload.get('bom_pairs') or []
    # if chain is list of tuples (parent, child, seq), normalize
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
        b = BomStructure(parent_item_code=parent, child_item_code=child, process_sequence=seq)
        db.session.add(b)

    db.session.commit()
    return jsonify({'status':'saved','fg_id': fg.id})

@generator_bp.route('/push', methods=['POST'])
def push_to_sap():
    payload = request.json
    client = SapPushService(current_app.config)
    try:
        log = client.push_full_bom(payload)
        # store log
        l = SapPushLog(request_payload=payload, response_payload=log.get('responses'), status=log.get('status'))
        db.session.add(l)
        db.session.commit()
        return jsonify({'status':'ok','log':log})
    except Exception as e:
        return jsonify({'error':str(e)}), 500
