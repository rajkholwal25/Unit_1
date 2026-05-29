#!/usr/bin/env python3
"""Create DB tables and default manager user quickly (no alembic).

Usage (PowerShell):
$env:PYTHONPATH="C:/Users/Mohit/Downloads/Unit_1"; $env:DATABASE_URL="postgresql://..."; python scripts/init_db.py

Or with Flask-Migrate you can run migrations instead (preferred for production).
"""
import os
from app import create_app
from app.extensions import db

app = create_app()

with app.app_context():
    print('Creating database tables (this uses SQLAlchemy create_all)...')
    db.create_all()
    # create default manager user if missing
    try:
        from app.models import User
        u = User.query.filter_by(email='manager@test.com').first()
        if not u:
            u = User(email='manager@test.com', role='manager')
            u.set_password('test@123')
            db.session.add(u)
            db.session.commit()
            print('Created default manager: manager@test.com / test@123')
        else:
            print('Default manager already exists')
    except Exception as e:
        print('Warning: could not create default user:', e)

    print('Done')
