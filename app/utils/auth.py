from functools import wraps
from flask import abort, flash, redirect, url_for
from flask_login import current_user


def _role_allowed(user_role: str, allowed: tuple[str, ...]) -> bool:
    """Unit 1 ``manager`` maps to full manufacturing access (admin + planner + operator)."""
    if not user_role:
        return False
    allowed_set = set(allowed)
    if user_role == 'manager':
        return bool(allowed_set & {'admin', 'planner', 'operator', 'quality', 'manager'})
    if user_role == 'admin':
        return bool(allowed_set & {'admin', 'planner', 'operator', 'quality'})
    return user_role in allowed_set


def role_required(*roles: str):
    """Decorator that restricts a route to users with one of the given roles."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.is_active:
                flash('Your account has been deactivated.', 'danger')
                return redirect(url_for('auth.logout'))
            if not _role_allowed(current_user.role, roles):
                flash('You do not have permission for this action.', 'danger')
                return redirect(url_for('dashboard.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def admin_required(f):
    """Shorthand decorator for admin-only routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not _role_allowed(current_user.role, ('admin',)):
            flash('Admin access required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function
