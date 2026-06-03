from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from functools import wraps

from app.extensions import db
from app.models.user import USER_ROLE_CHOICES, USER_ROLES, User

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def _render_user_form(user=None):
    return render_template(
        'admin/user_form.html',
        user=user,
        role_choices=USER_ROLE_CHOICES,
    )


def _requested_role() -> str:
    role = request.form.get('role', 'viewer')
    return role if role in USER_ROLES else 'viewer'


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/users')
@admin_required
def user_list():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/user_list.html', users=users)


@admin_bp.route('/users/create', methods=['GET', 'POST'])
@admin_required
def user_create():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = _requested_role()

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return _render_user_form()

        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'danger')
            return _render_user_form()

        user = User(username=username, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash(f'User "{username}" created successfully.', 'success')
        return redirect(url_for('admin.user_list'))

    return _render_user_form()


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def user_edit(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == 'POST':
        user.username = request.form.get('username', '').strip()
        user.email = request.form.get('email', '').strip()
        user.role = _requested_role()
        user.is_active_user = request.form.get('is_active') == 'on'

        new_password = request.form.get('password', '').strip()
        if new_password:
            user.set_password(new_password)

        db.session.commit()
        flash(f'User "{user.username}" updated.', 'success')
        return redirect(url_for('admin.user_list'))

    return _render_user_form(user=user)
