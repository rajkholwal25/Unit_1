from functools import wraps

from flask import flash, redirect, session, url_for
from flask_login import login_user as fl_login_user
from flask_login import logout_user as fl_logout_user


def login_user(user):
    session.clear()
    session['user_id'] = user.id
    session['user_email'] = user.email
    session['role'] = user.role
    if not getattr(user, 'username', None):
        user.username = (user.email or '').split('@')[0] or f'user{user.id}'
    fl_login_user(user, remember=True)


def logout_user():
    fl_logout_user()
    session.clear()


def requires_role(*roles):
    expanded = set(roles)
    expanded.update({'admin'} if 'manager' in expanded else set())
    expanded.update({'manager'} if 'admin' in expanded else set())

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get('user_id'):
                return redirect(url_for('auth.login'))
            if session.get('role') not in expanded:
                flash('Access denied', 'danger')
                return redirect(url_for('dashboard.index'))
            return fn(*args, **kwargs)
        return wrapper
    return decorator
