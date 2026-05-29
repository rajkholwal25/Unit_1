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
from .extensions import db, migrate
from .routes import register_blueprints


def create_app(config_name=None):
    app = Flask(__name__, template_folder='templates', static_folder='static')

    cfg = config_name or 'development'
    from config import config as app_config
    app.config.from_object(app_config[cfg])

    db.init_app(app)
    migrate.init_app(app, db)

    # Import models so SQLAlchemy metadata is registered (migrations, CLI).
    from . import models  # noqa: F401

    register_blueprints(app)
    load_site_settings(app)
    register_context_processors(app)
    register_template_filters(app)
    register_auth_middleware(app)
    ensure_default_manager(app)
    setup_logging(app)

    return app
