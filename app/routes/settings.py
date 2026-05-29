from flask import Blueprint, request, current_app, jsonify
import os, json

settings_bp = Blueprint('settings', __name__)

@settings_bp.route('/update', methods=['POST'])
def update_settings():
    data = request.get_json() or {}
    title = data.get('site_title')
    if title is None:
        return jsonify({'error':'site_title required'}), 400
    # write to settings.json (project root)
    settings_path = os.path.join(current_app.root_path, '..', 'settings.json')
    try:
        with open(settings_path, 'w', encoding='utf-8') as fh:
            json.dump({'site_title': title}, fh)
        # update app config in memory
        current_app.config['SITE_SETTINGS'] = {'site_title': title}
        return jsonify({'site_title': title})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
