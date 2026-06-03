from flask import redirect, request, session
from flask_login import current_user

PUBLIC_PATH_PREFIXES = ('/login', '/logout', '/static', '/favicon.ico')


def register_auth_middleware(app):
    @app.before_request
    def require_login():
        path = request.path or '/'
        if any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES):
            return None
        if session.get('user_id') and not current_user.is_authenticated:
            from ..models import User
            from .auth import login_user

            user = User.query.get(session.get('user_id'))
            if user and user.is_active:
                login_user(user)
        if not session.get('user_id') and not current_user.is_authenticated:
            return redirect('/login')
