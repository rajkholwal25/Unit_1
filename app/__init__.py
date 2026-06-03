import os

from flask import Flask

from .core import (
    ensure_default_manager,
    load_site_settings,
    register_auth_middleware,
    register_context_processors,
    register_template_filters,
    setup_logging,
)
from .extensions import csrf, db, login_manager, migrate
from .routes import register_blueprints

# Unit 2 code uses ``from app.extensions import db``
from . import extensions  # noqa: F401


def _apply_mfg_config(app):
    """Merge Unit 2 job-card settings; keep Unit 1 DB URI and warehouse defaults."""
    try:
        from .mfg_config import Config as MfgConfig
    except ImportError:
        return
    skip = {
        'SQLALCHEMY_DATABASE_URI',
        'SQLALCHEMY_TRACK_MODIFICATIONS',
        'SQLALCHEMY_ENGINE_OPTIONS',
        'SECRET_KEY',
        'SAP_DEFAULT_WAREHOUSE',
    }
    for key in dir(MfgConfig):
        if not key.isupper() or key in skip:
            continue
        val = getattr(MfgConfig, key)
        if key not in app.config or app.config[key] in (None, ''):
            app.config[key] = val


def _init_sap_mirror_scheduler(app):
    if not app.config.get('SAP_MIRROR_AUTO_SYNC_ENABLED', True):
        return
    if not (app.config.get('SAP_SERVICE_LAYER_URL') or app.config.get('SAP_BASE_URL')):
        return
    try:
        from datetime import datetime, timedelta

        from apscheduler.schedulers.background import BackgroundScheduler

        from .services.sap_mirror_sync import run_full_mirror_sync
    except ImportError:
        return

    hours = int(app.config.get('SAP_MIRROR_SYNC_INTERVAL_HOURS', 24) or 24)

    def _job():
        with app.app_context():
            try:
                run_full_mirror_sync(app, scope='all')
            except Exception:
                app.logger.exception('SAP mirror scheduled sync failed')

    sched = BackgroundScheduler(daemon=True)
    sched.add_job(_job, 'interval', hours=hours, id='sap_mirror_sync', replace_existing=True)
    if app.config.get('SAP_MIRROR_SYNC_ON_STARTUP'):
        sched.add_job(
            _job,
            'date',
            run_date=datetime.utcnow() + timedelta(seconds=120),
            id='sap_mirror_sync_startup',
            replace_existing=True,
        )
    sched.start()
    app.sap_mirror_scheduler = sched


def create_app(config_name=None):
    app = Flask(__name__, template_folder='templates', static_folder='static')

    cfg = config_name or 'development'
    from config import config as app_config

    app.config.from_object(app_config[cfg])
    _apply_mfg_config(app)

    db.init_app(app)
    _db_uri = str(app.config.get('SQLALCHEMY_DATABASE_URI', ''))
    migrate.init_app(app, db, render_as_batch=_db_uri.startswith('sqlite'))
    login_manager.init_app(app)
    csrf.init_app(app)

    from .models import User  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    register_blueprints(app)
    load_site_settings(app)
    register_context_processors(app)
    register_template_filters(app)
    register_auth_middleware(app)
    ensure_default_manager(app)
    setup_logging(app)

    try:
        from .logging_config import init_logging

        init_logging(app)
    except Exception:
        pass

    from .mfg_cli import register_mfg_commands

    register_mfg_commands(app)
    _init_sap_mirror_scheduler(app)

    return app
