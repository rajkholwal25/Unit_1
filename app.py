import os
from app import create_app

if __name__ == '__main__':
    # ensure .env is loaded by config.py (it loads .env at import time)
    app = create_app()
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV', 'production') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
