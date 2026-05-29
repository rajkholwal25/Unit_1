# SAP B1 BOM Automation - Flask

Minimal instructions to run:

1. Create a Python virtualenv and install requirements:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and set `DATABASE_URL` and SAP credentials.

3. Initialize DB and run migrations:

```bash
flask db init
flask db migrate -m "init"
flask db upgrade
```

4. Run the app:

```bash
flask run
```

What I need from you:
- Access to a PostgreSQL connection string to set `DATABASE_URL`.
- SAP Service Layer base URL and credentials to test `Push to SAP`.
- Any company-specific naming conventions for `ItemName` templates.

