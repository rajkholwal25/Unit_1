"""Flask CLI entry point: `flask db migrate`, `flask run`, etc."""

from app import create_app

app = create_app()
