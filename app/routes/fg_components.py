"""Combined hub for material, pattern, and coating master data (FG item code building blocks)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..extensions import db
from ..models import CoatingType, MaterialType, Pattern

fg_components_bp = Blueprint('fg_components', __name__)

VALID_TABS = frozenset({'materials', 'patterns', 'coatings'})


def _tab_from_request() -> str:
    tab = (request.args.get('tab') or request.form.get('_tab') or 'materials').strip().lower()
    return tab if tab in VALID_TABS else 'materials'


def _redirect_hub(tab: str):
    return redirect(url_for('fg_components.index', tab=tab))


@fg_components_bp.route('/', methods=['GET', 'POST'])
def index():
    tab = _tab_from_request()

    if request.method == 'POST':
        form_tab = (request.form.get('_tab') or tab).strip().lower()
        if form_tab == 'materials':
            _post_add_material()
            return _redirect_hub('materials')
        if form_tab == 'coatings':
            _post_add_coating()
            return _redirect_hub('coatings')
        if form_tab == 'patterns':
            _post_add_pattern()
            return _redirect_hub('patterns')
        return _redirect_hub(tab)

    return render_template(
        'fg_components/index.html',
        tab=tab,
        materials=MaterialType.query.order_by(MaterialType.code).all(),
        patterns=Pattern.query.order_by(Pattern.created_at.desc()).all(),
        coatings=CoatingType.query.order_by(CoatingType.code).all(),
    )


def _post_add_material():
    code = request.form.get('code', '').strip().upper()
    name = request.form.get('name', '').strip()
    if not code:
        flash('Code required', 'danger')
        return
    if not name:
        name = code
    if MaterialType.query.filter_by(code=code).first():
        flash('Material type already exists', 'warning')
        return
    db.session.add(MaterialType(code=code, name=name))
    db.session.commit()
    flash('Material type added', 'success')


def _post_add_coating():
    code = request.form.get('code', '').strip().upper()
    name = request.form.get('name', '').strip()
    if not code:
        flash('Code required', 'danger')
        return
    if not name:
        name = code
    if CoatingType.query.filter_by(code=code).first():
        flash('Coating type already exists', 'warning')
        return
    db.session.add(CoatingType(code=code, name=name))
    db.session.commit()
    flash('Coating type added', 'success')


def _post_add_pattern():
    name = request.form.get('pattern_name', '').strip()
    if not name:
        flash('Pattern name required', 'danger')
        return
    existing = Pattern.query.filter(db.func.lower(Pattern.pattern_name) == name.lower()).first()
    if existing:
        flash('Pattern already exists', 'warning')
        return
    last = Pattern.query.order_by(Pattern.id.desc()).first()
    next_code = int(last.pattern_code) + 1 if last and last.pattern_code.isdigit() else 1001
    db.session.add(Pattern(pattern_code=str(next_code), pattern_name=name))
    db.session.commit()
    flash('Pattern created', 'success')
