"""Print active DB from .env and schema checks."""
import os

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"), override=True)

from app import create_app
from app.extensions import db

app = create_app()
with app.app_context():
    uri = str(db.engine.url)
    print("DB:", uri.split("@")[-1] if "@" in uri else uri)
    row = db.session.execute(
        db.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='job_detail_line' AND column_name='yield_loss_pct'"
        )
    ).fetchone()
    print("yield_loss_pct exists:", bool(row))
    jobs = db.session.execute(db.text("SELECT COUNT(*) FROM job_master")).scalar()
    print("job_master rows:", jobs)
    ver = db.session.execute(db.text("SELECT version_num FROM alembic_version")).fetchone()
    print("alembic_version:", ver[0] if ver else None)
