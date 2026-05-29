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
