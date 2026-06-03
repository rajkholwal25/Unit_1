"""Ensure job_detail_line.yield_loss_pct exists on DATABASE_URL from .env only."""
import os

import psycopg2
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"), override=True)

ALTER_SQL = (
    "ALTER TABLE job_detail_line "
    "ADD COLUMN IF NOT EXISTS yield_loss_pct NUMERIC(5, 2) DEFAULT 2"
)


def main() -> None:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("ERROR: Set DATABASE_URL in .env (use unit1_combined).")
        raise SystemExit(1)
    dbname = url.rsplit("/", 1)[-1]
    if dbname == "unit1":
        print("WARNING: DATABASE_URL points at legacy DB 'unit1'.")
        print("         Update .env to unit1_combined and restart the app.")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='job_detail_line' AND column_name='yield_loss_pct'"
    )
    if cur.fetchone() is None:
        cur.execute(ALTER_SQL)
        print(f"[{dbname}] Added yield_loss_pct")
    else:
        print(f"[{dbname}] yield_loss_pct OK")
    cur.execute("SELECT version_num FROM alembic_version")
    print(f"[{dbname}] alembic:", cur.fetchone()[0])
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
