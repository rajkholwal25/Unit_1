from functools import wraps

from flask import flash, redirect, session, url_for


def login_user(user):
    session.clear()
    session['user_id'] = user.id
    session['user_email'] = user.email
    session['role'] = user.role


def logout_user():
    session.clear()


def requires_role(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get('user_id'):
                return redirect(url_for('auth.login'))
            if session.get('role') not in roles:
                flash('Access denied', 'danger')
                return redirect(url_for('dashboard.index'))
            return fn(*args, **kwargs)
        return wrapper
    return decorator
