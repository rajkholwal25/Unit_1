from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models import MaterialType
from app.services.roll_grn_service import (
    create_roll_grn_from_form,
    get_roll_grn_by_number,
    list_roll_grns,
)
from app.utils.auth import role_required

roll_grn_bp = Blueprint('roll_grn', __name__, url_prefix='/grn')


def _material_choices():
    return (
        MaterialType.query.filter_by(is_active=True)
        .order_by(MaterialType.code)
        .all()
    )


@roll_grn_bp.route('/create', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'planner', 'operator')
def create():
    materials = _material_choices()
    if request.method == 'POST':
        try:
            entry = create_roll_grn_from_form(
                request.form,
                created_by_id=current_user.id,
            )
            flash(f'GRN {entry.grn_number} created successfully.', 'success')
            return redirect(url_for('roll_grn.view', grn_number=entry.grn_number))
        except ValueError as e:
            flash(str(e), 'danger')
        except Exception as e:
            flash(f'Could not create GRN: {str(e)[:200]}', 'danger')

    return render_template(
        'roll_grn/create.html',
        materials=materials,
        form_data=request.form,
    )


@roll_grn_bp.route('/')
@login_required
def index():
    entries = list_roll_grns()
    return render_template('roll_grn/list.html', entries=entries)


@roll_grn_bp.route('/<grn_number>')
@login_required
def view(grn_number: str):
    entry = get_roll_grn_by_number(grn_number)
    if not entry:
        flash('GRN not found.', 'warning')
        return redirect(url_for('roll_grn.index'))
    return render_template('roll_grn/view.html', entry=entry)
