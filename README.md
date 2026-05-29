# Unit 1 — SAP B1 BOM Automation

Flask app for pattern/material/BOM management, item code generation, and SAP push logging.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, set `DATABASE_URL` and SAP credentials, then:

```bash
flask db upgrade
python app.py
```

Open http://127.0.0.1:5000 — default login: `manager@test.com` / `test@123`

## Project structure

```
Unit_1/
├── app.py                 # Dev entry point
├── config.py              # Environment config (dev / production)
├── settings.json          # Site title (editable in UI)
├── requirements.txt
├── migrations/            # Alembic DB migrations
├── logs/                  # Application log files
├── scripts/               # One-off scripts (e.g. init_db)
└── app/
    ├── __init__.py        # Application factory (create_app)
    ├── extensions.py      # SQLAlchemy, Migrate
    ├── cli.py             # Seed helpers
    ├── core/              # Cross-cutting app setup
    │   ├── auth.py        # Login session, role decorator
    │   ├── bootstrap.py   # Default admin user
    │   ├── filters.py     # Jinja filters (fmt_json, fmt_seq, fmt_dt)
    │   ├── middleware.py  # Login required
    │   ├── settings.py    # Site settings loader
    │   └── logging_config.py
    ├── models/            # Database models (one file per domain)
    │   ├── user.py
    │   ├── pattern.py
    │   ├── material.py
    │   ├── bom.py
    │   └── sap.py
    ├── routes/            # HTTP blueprints (one module per area)
    │   ├── __init__.py    # register_blueprints()
    │   ├── dashboard.py
    │   ├── auth.py
    │   ├── patterns.py
    │   ├── material_types.py
    │   ├── bom_templates.py
    │   ├── generator.py
    │   ├── sap_logs.py
    │   ├── item_codes.py
    │   └── settings.py
    ├── services/          # Business logic & SAP client
    │   ├── bom_generation.py
    │   ├── item_code_generator.py
    │   ├── sap_client.py
    │   ├── sap_push_service.py
    │   └── warehouse_mapping.py
    ├── templates/
    │   ├── layouts/       # base.html
    │   ├── partials/      # Shared fragments (flash messages)
    │   ├── dashboard/
    │   ├── auth/
    │   ├── patterns/
    │   ├── material_types/
    │   ├── bom_templates/
    │   ├── generator/
    │   ├── sap_logs/
    │   └── item_codes/
    └── static/
        ├── css/main.css
        └── js/main.js
```

## Where to change things

| Task | Location |
|------|----------|
| New page / API route | `app/routes/` + template under `app/templates/` |
| Database table | `app/models/` → `flask db migrate` |
| Item code rules | `app/services/item_code_generator.py` |
| BOM chain logic | `app/services/bom_generation.py` |
| SAP API calls | `app/services/sap_client.py`, `sap_push_service.py` |
| Global UI / nav | `app/templates/layouts/base.html`, `static/css/main.css` |
| Auth / roles | `app/core/auth.py` |

## Environment

See `.env.example` for `DATABASE_URL`, `SAP_BASE_URL`, `SAP_USER`, `SAP_PASSWORD`, and `SECRET_KEY`.
