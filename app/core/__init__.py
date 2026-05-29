"""App-wide setup: filters, middleware, settings, logging."""

from .auth import login_user, logout_user, requires_role
from .bootstrap import ensure_default_manager
from .filters import register_template_filters
from .logging_config import setup_logging
from .middleware import register_auth_middleware
from .settings import load_site_settings, register_context_processors

__all__ = [
    'login_user',
    'logout_user',
    'requires_role',
    'ensure_default_manager',
    'register_template_filters',
    'setup_logging',
    'register_auth_middleware',
    'load_site_settings',
    'register_context_processors',
]
