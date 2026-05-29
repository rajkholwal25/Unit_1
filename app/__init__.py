from flask import Flask
from .extensions import db, migrate
from .routes.patterns import patterns_bp
from .routes.material_types import material_bp
from .routes.bom_templates import templates_bp
from .routes.generator import generator_bp
from .routes.sap_logs import sap_logs_bp


def create_app(config_name=None):
    app = Flask(__name__, template_folder='templates', static_folder='static')
    cfg = config_name or 'development'
    from config import config as app_config
    app.config.from_object(app_config[cfg])

    # extensions
    db.init_app(app)
    migrate.init_app(app, db)

    # blueprints
    app.register_blueprint(patterns_bp, url_prefix='/patterns')
    app.register_blueprint(material_bp, url_prefix='/material-types')
    app.register_blueprint(templates_bp, url_prefix='/bom-templates')
    app.register_blueprint(generator_bp, url_prefix='/item-generator')
    app.register_blueprint(sap_logs_bp, url_prefix='/sap-logs')
    # auth blueprint (login/users)
    from .routes.auth import auth_bp
    app.register_blueprint(auth_bp)
    # item codes quick view
    from .routes.item_codes import item_codes_bp
    app.register_blueprint(item_codes_bp, url_prefix='/item-codes')

    # settings blueprint
    from .routes.settings import settings_bp
    app.register_blueprint(settings_bp, url_prefix='/settings')

    # load simple settings file and expose to templates
    import json, os
    settings_path = os.path.join(app.root_path, '..', 'settings.json')
    try:
        with open(settings_path, 'r', encoding='utf-8') as fh:
            app.config['SITE_SETTINGS'] = json.load(fh)
    except Exception:
        app.config['SITE_SETTINGS'] = {'site_title': 'Unit 1'}

    @app.context_processor
    def inject_site_title():
        return {'site_title': app.config.get('SITE_SETTINGS', {}).get('site_title', 'Unit 1')}

    @app.template_filter('fmt_json')
    def fmt_json(value):
        """Pretty-print JSON/dict for display in templates."""
        import json
        if value is None:
            return '—'
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return value
        try:
            return json.dumps(value, indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)

    @app.template_filter('fmt_seq')
    def fmt_seq(value):
        """Format BOM process_sequence (list or comma-separated string)."""
        if value is None:
            return ''
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return ', '.join(str(x).strip() for x in value if str(x).strip())
        return str(value)

    @app.template_filter('fmt_dt')
    def fmt_dt(value):
        if not value:
            return '—'
        try:
            return value.strftime('%Y-%m-%d %H:%M')
        except AttributeError:
            return str(value)

    # enforce login for most pages
    from flask import request, session, redirect
    def is_allowed_path(path):
        allow = ['/login', '/logout', '/static', '/favicon.ico']
        if any(path.startswith(a) for a in allow):
            return True
        return False

    @app.before_request
    def require_login():
        path = request.path or '/'
        if is_allowed_path(path):
            return None
        if not session.get('user_id'):
            return redirect('/login')

    # simple index
    @app.route('/')
    def index():
        from flask import render_template
        return render_template('index.html')

    # create default manager user if DB available
    try:
        with app.app_context():
            from .models import User
            u = User.query.filter_by(email='manager@test.com').first()
            if not u:
                u = User(email='manager@test.com', role='manager')
                u.set_password('test@123')
                db.session.add(u)
                db.session.commit()
                app.logger.info('Created default manager user manager@test.com')
    except Exception:
        # ignore if DB not present or migrations not run
        pass

    # setup file logging for easier debugging
    try:
        import logging
        from logging.handlers import RotatingFileHandler
        logdir = os.path.join(app.root_path, '..', 'logs')
        os.makedirs(logdir, exist_ok=True)
        fh = RotatingFileHandler(os.path.join(logdir, 'app.log'), maxBytes=2_000_000, backupCount=5)
        fh.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]')
        fh.setFormatter(formatter)
        if not app.logger.handlers:
            app.logger.addHandler(fh)
        else:
            # ensure our handler is present
            app.logger.handlers.append(fh)
        app.logger.setLevel(logging.INFO)
        app.logger.info('Application startup')
    except Exception:
        pass

    return app
