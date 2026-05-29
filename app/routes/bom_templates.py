from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..extensions import db
from ..models import BomTemplate
import json

templates_bp = Blueprint('bom_templates', __name__, template_folder='templates')

@templates_bp.route('/', methods=['GET','POST'])
def list_templates():
    if request.method == 'POST':
        name = request.form.get('template_name','').strip()
        seq = request.form.get('process_sequence','').strip()
        if not name or not seq:
            flash('Template name and process sequence required','danger')
            return redirect(url_for('bom_templates.list_templates'))
        try:
            processes = [p.strip().upper() for p in seq.split(',') if p.strip()]
        except Exception:
            flash('Invalid process sequence format','danger')
            return redirect(url_for('bom_templates.list_templates'))
        t = BomTemplate(template_name=name, process_sequence=processes)
        db.session.add(t)
        db.session.commit()
        flash('Template saved','success')
        return redirect(url_for('bom_templates.list_templates'))
    templates = BomTemplate.query.order_by(BomTemplate.id).all()
    return render_template('bom_templates/list.html', templates=templates)


@templates_bp.route('/ajax_update', methods=['POST'])
def ajax_update_template():
    data = request.get_json() or {}
    tid = data.get('id')
    name = (data.get('template_name') or '').strip()
    seq = data.get('process_sequence')
    if not tid or not name or seq is None:
        return ({'error':'id, template_name and process_sequence required'}, 400)
    t = BomTemplate.query.get(int(tid))
    if not t:
        return ({'error':'template not found'}, 404)
    # expect sequence as comma-separated string or list
    if isinstance(seq, str):
        processes = [p.strip().upper() for p in seq.split(',') if p.strip()]
    elif isinstance(seq, list):
        processes = [str(p).strip().upper() for p in seq if str(p).strip()]
    else:
        return ({'error':'invalid process_sequence'}, 400)
    t.template_name = name
    t.process_sequence = processes
    db.session.commit()
    return ({'id': t.id, 'template_name': t.template_name, 'process_sequence': t.process_sequence}, 200)


@templates_bp.route('/ajax_delete', methods=['POST'])
def ajax_delete_template():
    data = request.get_json() or {}
    tid = data.get('id')
    identifier = (data.get('identifier') or '').strip()
    t = None
    if tid:
        try:
            t = BomTemplate.query.get(int(tid))
        except Exception:
            t = None
    elif identifier:
        # delete by exact template name
        t = BomTemplate.query.filter(BomTemplate.template_name == identifier).first()
    else:
        return ({'error':'id or identifier required'}, 400)
    if not t:
        return ({'error':'template not found'}, 404)
    deleted_id = t.id
    db.session.delete(t)
    db.session.commit()
    return ({'deleted_id': deleted_id}, 200)
