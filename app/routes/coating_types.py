from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from ..core.db_errors import safe_commit_delete
from ..extensions import db
from ..models import CoatingType, GeneratedFGItem

coating_bp = Blueprint('coating_types', __name__)


@coating_bp.route('/', methods=['GET', 'POST'])
def list_coatings():
    if request.method == 'GET':
        return redirect(url_for('fg_components.index', tab='coatings'))
    if request.method == 'POST':
        code = request.form.get('code', '').strip().upper()
        name = request.form.get('name', '').strip()
        if not code:
            flash('Code required', 'danger')
            return redirect(url_for('fg_components.index', tab='coatings'))
        if not name:
            name = code
        if CoatingType.query.filter_by(code=code).first():
            flash('Coating type already exists', 'warning')
            return redirect(url_for('fg_components.index', tab='coatings'))
        db.session.add(CoatingType(code=code, name=name))
        db.session.commit()
        flash('Coating type added', 'success')
        return redirect(url_for('fg_components.index', tab='coatings'))

    return redirect(url_for('fg_components.index', tab='coatings'))


@coating_bp.route('/ajax_update', methods=['POST'])
def ajax_update_coating():
    data = request.get_json() or {}
    cid = data.get('id')
    code = (data.get('code') or '').strip().upper()
    name = (data.get('name') or '').strip()
    is_active = data.get('is_active')
    if not cid or not code:
        return jsonify({'error': 'id and code required'}), 400

    coating = CoatingType.query.get(int(cid))
    if not coating:
        return jsonify({'error': 'coating not found'}), 404

    dup = CoatingType.query.filter(CoatingType.code == code, CoatingType.id != coating.id).first()
    if dup:
        return jsonify({'error': 'duplicate code'}), 409

    coating.code = code
    coating.name = name if name else code
    if isinstance(is_active, bool):
        coating.is_active = is_active
    db.session.commit()
    return jsonify({
        'id': coating.id,
        'code': coating.code,
        'name': coating.name,
        'is_active': coating.is_active,
    }), 200


@coating_bp.route('/ajax_delete', methods=['POST'])
def ajax_delete_coating():
    data = request.get_json() or {}
    cid = data.get('id')
    identifier = (data.get('identifier') or '').strip().upper()
    coating = None

    if cid:
        try:
            coating = CoatingType.query.get(int(cid))
        except (TypeError, ValueError):
            coating = None
    elif identifier:
        coating = CoatingType.query.filter(
            (CoatingType.code == identifier) | (CoatingType.name == identifier)
        ).first()
    else:
        return jsonify({'error': 'id or identifier required'}), 400

    if not coating:
        return jsonify({'error': 'coating not found'}), 404

    in_use = GeneratedFGItem.query.filter_by(coating=coating.code).count()
    if in_use:
        return jsonify({
            'error': f'Cannot delete: {in_use} generated item(s) use coating "{coating.code}".',
        }), 409

    deleted_id = coating.id
    ok, err = safe_commit_delete(coating)
    if not ok:
        return jsonify({'error': err}), 409
    return jsonify({'deleted_id': deleted_id}), 200
