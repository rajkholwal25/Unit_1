from flask import redirect, request, session

PUBLIC_PATH_PREFIXES = ('/login', '/logout', '/static', '/favicon.ico')


def register_auth_middleware(app):
    @app.before_request
    def require_login():
        path = request.path or '/'
        if any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES):
            return None
        if not session.get('user_id'):
            return redirect('/login')
