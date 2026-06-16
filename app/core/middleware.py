from flask import flash, redirect, request, session, url_for
from flask_login import current_user
from flask_login import logout_user as fl_logout_user

PUBLIC_PATH_PREFIXES = ('/login', '/logout', '/static', '/favicon.ico')


def register_auth_middleware(app):
    @app.before_request
    def require_login():
        path = request.path or '/'
        if any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES):
            return None

        if not session.get('user_id'):
            if current_user.is_authenticated:
                fl_logout_user()
                flash('Your session has expired. Please sign in again.', 'warning')
            return redirect(url_for('auth.login'))

        if not current_user.is_authenticated:
            from ..models import User
            from .auth import restore_flask_login

            user = User.query.get(session.get('user_id'))
            if user and user.is_active:
                restore_flask_login(user)
            else:
                session.clear()
                flash('Your session has expired. Please sign in again.', 'warning')
                return redirect(url_for('auth.login'))
