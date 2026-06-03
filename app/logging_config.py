"""Application file logging (rotating) for diagnostics and SAP traceability."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask


def get_logger(name: str) -> logging.Logger:
    """Namespaced loggers under ``jobcard.*`` (e.g. ``jobcard.sap``, ``jobcard.mjd1``)."""
    return logging.getLogger(f'jobcard.{name}')


def init_logging(app: Flask) -> None:
    """Attach a rotating file handler and optional console handler."""
    if app.config.get('_LOGGING_INITIALIZED'):
        return

    log_dir = app.config['APP_LOG_DIR']
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, app.config['APP_LOG_FILE'])

    level_name = str(app.config.get('APP_LOG_LEVEL', 'INFO')).upper()
    level = getattr(logging, level_name, logging.INFO)
    max_bytes = int(app.config.get('APP_LOG_MAX_BYTES', 2 * 1024 * 1024))
    backup_count = int(app.config.get('APP_LOG_BACKUP_COUNT', 5))

    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8',
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)

    root_jobcard = logging.getLogger('jobcard')
    root_jobcard.setLevel(level)
    if not any(type(h) is RotatingFileHandler for h in root_jobcard.handlers):
        root_jobcard.addHandler(file_handler)
    root_jobcard.propagate = False

    app.logger.setLevel(level)
    if not any(type(h) is RotatingFileHandler for h in app.logger.handlers):
        app.logger.addHandler(file_handler)
    if app.debug or app.config.get('APP_LOG_CONSOLE', False):
        console = logging.StreamHandler()
        console.setLevel(level)
        console.setFormatter(fmt)
        if not any(type(h) is logging.StreamHandler for h in app.logger.handlers):
            app.logger.addHandler(console)

    app.logger.info('Logging initialized; file=%s level=%s', log_path, level_name)
    app.config['_LOGGING_INITIALIZED'] = True
