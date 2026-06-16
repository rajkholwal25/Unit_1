"""SAP warehouse codes (OWHS) for PO / BOM line dropdowns."""

from __future__ import annotations

import time
from typing import Optional

from flask import current_app

from app.logging_config import get_logger

_log = get_logger('sap.warehouses')

_CACHE: dict = {'codes': [], 'fetched_at': 0.0}
_CACHE_TTL_SEC = 300


def _merge_unique_codes(*groups) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group or []:
            w = (raw or '').strip()
            if w and w not in seen:
                out.append(w)
                seen.add(w)
    return out


def sap_warehouse_codes(*, extra: Optional[list[str]] = None) -> list[str]:
    """Sorted SAP warehouse codes with short-lived in-process cache."""
    now = time.monotonic()
    cached = list(_CACHE.get('codes') or [])
    if cached and now - float(_CACHE.get('fetched_at') or 0) < _CACHE_TTL_SEC:
        return _merge_unique_codes(cached, extra)

    if not current_app.config.get('SAP_SERVICE_LAYER_URL'):
        return _merge_unique_codes(extra)

    from app.services.sap_job_client import SAPClient, SAPClientError

    codes: list[str] = []
    try:
        client = SAPClient()
        try:
            rows = client.fetch_warehouses()
        finally:
            client.logout()
        for row in rows:
            code = (row.get('WarehouseCode') or '').strip()
            if code:
                codes.append(code)
        codes = sorted(set(codes))
        if codes:
            _CACHE['codes'] = codes
            _CACHE['fetched_at'] = now
    except SAPClientError as e:
        _log.warning('SAP warehouse list fetch failed: %s', str(e)[:200])
    except Exception as e:
        _log.warning('SAP warehouse list unexpected error: %s', str(e)[:200])

    base = codes if codes else cached
    return _merge_unique_codes(base, extra)
