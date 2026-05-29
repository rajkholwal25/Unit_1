from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app, jsonify
from ..extensions import db
from ..models import User

auth_bp = Blueprint('auth', __name__, template_folder='templates')


def login_user(user):
    session.clear()
    session['user_id'] = user.id
    session['user_email'] = user.email
    session['role'] = user.role


def logout_user():
    session.clear()


def requires_role(*roles):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            if not session.get('user_id'):
                return redirect(url_for('auth.login'))
            if session.get('role') not in roles:
                flash('Access denied', 'danger')
                return redirect(url_for('index'))
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Logged in', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid credentials', 'danger')
            return render_template('auth/login.html', email=email)
    return render_template('auth/login.html')


@auth_bp.route('/logout')
def logout():
    logout_user()
    flash('Logged out', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/users', methods=['GET', 'POST'])
@requires_role('manager')
def users():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        role = (request.form.get('role') or 'viewer').strip()
        password = request.form.get('password') or ''
        if not email or not password:
            flash('email and password required', 'danger')
            return redirect(url_for('auth.users'))
        if User.query.filter_by(email=email).first():
            flash('User already exists', 'warning')
            return redirect(url_for('auth.users'))
        u = User(email=email, role=role)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        flash('User created', 'success')
        return redirect(url_for('auth.users'))
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('auth/users.html', users=users)


@auth_bp.route('/users/delete/<int:uid>', methods=['POST'])
@requires_role('manager')
def delete_user(uid):
    if session.get('user_id') == uid:
        flash('Cannot delete yourself', 'warning')
        return redirect(url_for('auth.users'))
    u = User.query.get(uid)
    if not u:
        flash('User not found', 'danger')
        return redirect(url_for('auth.users'))
    db.session.delete(u)
    db.session.commit()
    flash('User deleted', 'success')
    return redirect(url_for('auth.users'))
