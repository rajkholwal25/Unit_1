import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(app):
    try:
        logdir = os.path.join(app.root_path, '..', 'logs')
        os.makedirs(logdir, exist_ok=True)
        logfile = os.path.join(logdir, 'app.log')
        handler = RotatingFileHandler(logfile, maxBytes=2_000_000, backupCount=5)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        if not app.logger.handlers:
            app.logger.addHandler(handler)
        else:
            app.logger.handlers.append(handler)
        app.logger.setLevel(logging.INFO)
        app.logger.info('Application startup')
    except OSError:
        pass
