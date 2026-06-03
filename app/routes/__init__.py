"""Register all Flask blueprints (Unit 1 master data + Unit 2 manufacturing)."""


def register_blueprints(app):
    from .auth import auth_bp
    from .bom_templates import templates_bp
    from .dashboard import dashboard_bp
    from .coating_types import coating_bp
    from .generator import generator_bp
    from .bom_builder import bom_builder_bp
    from .item_codes import item_codes_bp
    from .material_types import material_bp
    from .patterns import patterns_bp
    from .sap_logs import sap_logs_bp
    from .settings import settings_bp

    from .mfg_dashboard import mfg_dashboard_bp
    from .jobs import jobs_bp
    from .job_cards import job_cards_bp
    from .admin import admin_bp
    from .sap_integration import sap_bp
    from .api.reference import ref_api_bp
    from .api.sap import sap_api_bp
    from .api.jobs_api import jobs_api_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(patterns_bp, url_prefix='/patterns')
    app.register_blueprint(material_bp, url_prefix='/material-types')
    app.register_blueprint(coating_bp, url_prefix='/coating-types')
    app.register_blueprint(templates_bp, url_prefix='/bom-templates')
    app.register_blueprint(generator_bp, url_prefix='/item-generator')
    app.register_blueprint(bom_builder_bp, url_prefix='/bom-builder')
    app.register_blueprint(sap_logs_bp, url_prefix='/sap-logs')
    app.register_blueprint(item_codes_bp, url_prefix='/item-codes')
    app.register_blueprint(settings_bp, url_prefix='/settings')

    app.register_blueprint(mfg_dashboard_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(job_cards_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(sap_bp)
    app.register_blueprint(ref_api_bp)
    app.register_blueprint(sap_api_bp)
    app.register_blueprint(jobs_api_bp)
