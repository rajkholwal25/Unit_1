"""Register all Flask blueprints."""


def register_blueprints(app):
    from .auth import auth_bp
    from .bom_templates import templates_bp
    from .dashboard import dashboard_bp
    from .coating_types import coating_bp
    from .generator import generator_bp
    from .item_codes import item_codes_bp
    from .material_types import material_bp
    from .patterns import patterns_bp
    from .sap_logs import sap_logs_bp
    from .settings import settings_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(patterns_bp, url_prefix='/patterns')
    app.register_blueprint(material_bp, url_prefix='/material-types')
    app.register_blueprint(coating_bp, url_prefix='/coating-types')
    app.register_blueprint(templates_bp, url_prefix='/bom-templates')
    app.register_blueprint(generator_bp, url_prefix='/item-generator')
    app.register_blueprint(sap_logs_bp, url_prefix='/sap-logs')
    app.register_blueprint(item_codes_bp, url_prefix='/item-codes')
    app.register_blueprint(settings_bp, url_prefix='/settings')
