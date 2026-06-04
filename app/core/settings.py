import json
import os


def load_site_settings(app):
    settings_path = os.path.join(app.root_path, '..', 'settings.json')
    try:
        with open(settings_path, encoding='utf-8') as fh:
            app.config['SITE_SETTINGS'] = json.load(fh)
    except OSError:
        app.config['SITE_SETTINGS'] = {'site_title': 'Unit 1'}


def register_context_processors(app):
    @app.context_processor
    def inject_site_title():
        return {
            'site_title': app.config.get('SITE_SETTINGS', {}).get('site_title', 'Unit 1'),
        }

    @app.context_processor
    def inject_unit1_pattern_map():
        """Pattern code → name for client-side FG labels (``PET-12-1009-TR`` → ``PET 12MM Triangle TR``)."""
        try:
            from app.models import Pattern

            rows = Pattern.query.all()
            return {
                'unit1_pattern_by_code': {
                    str(r.pattern_code): (r.pattern_name or '')
                    for r in rows
                    if r.pattern_code
                },
            }
        except Exception:
            return {'unit1_pattern_by_code': {}}
