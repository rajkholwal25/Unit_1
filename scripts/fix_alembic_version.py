from app import create_app
from app.extensions import db

app = create_app()
with app.app_context():
    db.session.execute(db.text("UPDATE alembic_version SET version_num='bb8141a0713b'"))
    db.session.commit()
    print("alembic_version:", db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar())
