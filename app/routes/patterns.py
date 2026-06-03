from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..extensions import db
from ..models import Pattern
from sqlalchemy import or_

patterns_bp = Blueprint('patterns', __name__)

@patterns_bp.route('/', methods=['GET','POST'])
def list_patterns():
    if request.method == 'GET':
        return redirect(url_for('fg_components.index', tab='patterns'))
    if request.method == 'POST':
        name = request.form.get('pattern_name','').strip()
        if not name:
            flash('Pattern name required','danger')
            return redirect(url_for('fg_components.index', tab='patterns'))
        # case-insensitive duplicate prevention
        existing = Pattern.query.filter(db.func.lower(Pattern.pattern_name)==name.lower()).first()
        if existing:
            flash('Pattern already exists','warning')
            return redirect(url_for('fg_components.index', tab='patterns'))
        # generate code (start at 1001)
        last = Pattern.query.order_by(Pattern.id.desc()).first()
        next_code = int(last.pattern_code) + 1 if last and last.pattern_code.isdigit() else 1001
        p = Pattern(pattern_code=str(next_code), pattern_name=name)
        db.session.add(p)
        db.session.commit()
        flash('Pattern created','success')
        return redirect(url_for('fg_components.index', tab='patterns'))
    return redirect(url_for('fg_components.index', tab='patterns'))


@patterns_bp.route('/ajax_create', methods=['POST'])
def ajax_create_pattern():
    # Accept JSON or form
    name = None
    if hasattr(request, 'json') and request.is_json:
        name = (request.json.get('pattern_name') or '').strip()
    if not name:
        name = (request.form.get('pattern_name') or '').strip()
    if not name:
        return ({'error': 'pattern_name required'}, 400)
    # case-insensitive duplicate prevention
    existing = Pattern.query.filter(db.func.lower(Pattern.pattern_name) == name.lower()).first()
    if existing:
        return ({'id': existing.id, 'pattern_code': existing.pattern_code, 'pattern_name': existing.pattern_name, 'message': 'exists'}, 200)
    last = Pattern.query.order_by(Pattern.id.desc()).first()
    next_code = int(last.pattern_code) + 1 if last and last.pattern_code.isdigit() else 1001
    p = Pattern(pattern_code=str(next_code), pattern_name=name)
    db.session.add(p)
    db.session.commit()
    return ({'id': p.id, 'pattern_code': p.pattern_code, 'pattern_name': p.pattern_name}, 201)


@patterns_bp.route('/search')
def search_patterns():
    q = (request.args.get('q') or '')
    q = q.strip()
    # if empty query, return recent/top patterns (limit 200)
    if not q:
        # only include active patterns for selection when no query provided
        results = Pattern.query.filter(Pattern.is_active.is_(True)).order_by(Pattern.pattern_code).limit(200)
    else:
        qlow = q.lower()
        # search by name (case-insensitive, partial) OR by code (partial match)
        # only include active patterns for selection
        results = Pattern.query.filter(
            Pattern.is_active.is_(True),
            or_(
                db.func.lower(Pattern.pattern_name).like(f"%{qlow}%"),
                Pattern.pattern_code.like(f"%{q}%")
            )
        )
    out = [{'id': r.id, 'pattern_name': r.pattern_name, 'pattern_code': r.pattern_code} for r in results.limit(50).all()]
    return ({'results': out}, 200)


@patterns_bp.route('/ajax_update', methods=['POST'])
def ajax_update_pattern():
    data = request.get_json() or {}
    pid = data.get('id')
    name = (data.get('pattern_name') or '').strip()
    if not pid or not name:
        return ({'error':'id and pattern_name required'}, 400)
    p = Pattern.query.get(int(pid))
    if not p:
        return ({'error':'pattern not found'}, 404)
    # check duplicate (case-insensitive) excluding current id
    dup = Pattern.query.filter(db.func.lower(Pattern.pattern_name)==name.lower(), Pattern.id!=p.id).first()
    if dup:
        return ({'error':'duplicate pattern name'}, 409)
    p.pattern_name = name
    # optional active flag
    if 'is_active' in data:
        try:
            p.is_active = bool(data.get('is_active'))
        except Exception:
            p.is_active = p.is_active
    db.session.commit()
    return ({'id': p.id, 'pattern_code': p.pattern_code, 'pattern_name': p.pattern_name, 'is_active': p.is_active}, 200)


@patterns_bp.route('/ajax_toggle_active', methods=['POST'])
def ajax_toggle_active():
    data = request.get_json() or {}
    pid = data.get('id')
    if not pid:
        return ({'error':'id required'}, 400)
    p = Pattern.query.get(int(pid))
    if not p:
        return ({'error':'pattern not found'}, 404)
    # toggle or set explicitly
    if 'is_active' in data:
        p.is_active = bool(data.get('is_active'))
    else:
        p.is_active = not p.is_active
    db.session.commit()
    return ({'id': p.id, 'is_active': p.is_active}, 200)


@patterns_bp.route('/print')
def print_patterns():
    patterns = Pattern.query.order_by(Pattern.pattern_code).all()
    from flask import render_template
    return render_template('patterns/print.html', patterns=patterns)
