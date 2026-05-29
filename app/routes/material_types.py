from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..extensions import db
from ..models import MaterialType

material_bp = Blueprint('material_types', __name__, template_folder='templates')

@material_bp.route('/', methods=['GET','POST'])
def list_materials():
    if request.method == 'POST':
        code = request.form.get('code','').strip().upper()
        name = request.form.get('name','').strip()
        if not code:
            flash('Code required','danger')
            return redirect(url_for('material_types.list_materials'))
        # make name optional; default to code if not provided
        if not name:
            name = code
        existing = MaterialType.query.filter_by(code=code).first()
        if existing:
            flash('Material type already exists','warning')
            return redirect(url_for('material_types.list_materials'))
        m = MaterialType(code=code, name=name)
        db.session.add(m)
        db.session.commit()
        flash('Material type added','success')
        return redirect(url_for('material_types.list_materials'))
    materials = MaterialType.query.order_by(MaterialType.code).all()
    return render_template('material_types/list.html', materials=materials)


@material_bp.route('/ajax_update', methods=['POST'])
def ajax_update_material():
    data = request.get_json() or {}
    mid = data.get('id')
    code = (data.get('code') or '').strip().upper()
    name = (data.get('name') or '').strip()
    is_active = data.get('is_active')
    if not mid or not code:
        return ({'error':'id and code required'}, 400)
    m = MaterialType.query.get(int(mid))
    if not m:
        return ({'error':'material not found'}, 404)
    # check duplicate code
    dup = MaterialType.query.filter(MaterialType.code==code, MaterialType.id!=m.id).first()
    if dup:
        return ({'error':'duplicate code'}, 409)
    m.code = code
    # allow empty name; if empty default to code
    m.name = name if name else code
    if isinstance(is_active, bool):
        m.is_active = is_active
    db.session.commit()
    return ({'id': m.id, 'code': m.code, 'name': m.name, 'is_active': m.is_active}, 200)


@material_bp.route('/ajax_delete', methods=['POST'])
def ajax_delete_material():
    data = request.get_json() or {}
    mid = data.get('id')
    identifier = (data.get('identifier') or '').strip()
    m = None
    if mid:
        try:
            m = MaterialType.query.get(int(mid))
        except Exception:
            m = None
    elif identifier:
        # allow delete by exact code or exact name
        m = MaterialType.query.filter((MaterialType.code == identifier) | (MaterialType.name == identifier)).first()
    else:
        return ({'error':'id or identifier required'}, 400)
    if not m:
        return ({'error':'material not found'}, 404)
    deleted_id = m.id
    db.session.delete(m)
    db.session.commit()
    return ({'deleted_id': deleted_id}, 200)
