"""Append-only activity rows for the dashboard (reuses ``IntegrationEvent`` for app-side edits)."""

from __future__ import annotations

from typing import Any, Optional

from app.extensions import db
from app.models.audit import IntegrationEvent


def log_app_integration_event(
    job_no: str,
    action: str,
    *,
    success: bool,
    message: str = '',
    payload: Optional[dict[str, Any]] = None,
) -> None:
    """Record a job-scoped app event (BOM save, job edit, etc.) for the Activity & integration log.

    Uses ``target_system='app'`` to distinguish from SAP Service Layer traffic. Does not commit.
    """
    jn = (job_no or '').strip()
    if not jn:
        return
    act = (action or 'app_event').strip()[:50]
    msg = (message or '').strip()
    ev = IntegrationEvent(
        job_id=jn,
        target_system='app',
        action=act,
        state='success' if success else 'failed',
        request_payload=payload,
        error_message=None if success else (msg or 'Failed')[:16000],
        response_payload={'message': (msg or 'OK')[:2000]} if success else None,
    )
    db.session.add(ev)
