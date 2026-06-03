"""Prinect JDF integration — stub until a real endpoint / SDK is wired."""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import current_app

from app.logging_config import get_logger

_log = get_logger('prinect')


def build_jdf_xml_stub(job_card) -> str:
    """Minimal JDF-like XML for tracing; replace with real JDF from job data."""
    jc = job_card.job_card_number
    pn = (job_card.product_name or '')[:120]
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<JDF xmlns="http://www.CIP4.org/JDFSchema_1_1">'
        f'<Comment>stub JDF for {jc} — {pn}</Comment>'
        f'</JDF>'
    )


def push_jdf_for_job_card(job_card) -> Dict[str, Any]:
    """Generate JDF and optionally POST to Prinect. Returns result dict."""
    cfg = current_app.config
    if not cfg.get('PRINECT_ENABLED'):
        _log.info('Prinect disabled; skip JDF for JC %s', getattr(job_card, 'job_card_number', ''))
        return {'ok': True, 'skipped': True, 'reason': 'PRINECT_ENABLED is off'}

    xml = build_jdf_xml_stub(job_card)
    endpoint = (cfg.get('PRINECT_JDF_ENDPOINT') or '').strip()
    if not endpoint:
        _log.warning(
            'PRINECT_ENABLED but PRINECT_JDF_ENDPOINT empty; JDF generated only in logs for %s',
            getattr(job_card, 'job_card_number', ''),
        )
        if _log.isEnabledFor(logging.DEBUG):
            _log.debug('JDF stub: %s', xml[:500])
        return {'ok': True, 'stub_only': True, 'bytes': len(xml.encode('utf-8'))}

    try:
        import requests

        r = requests.post(
            endpoint,
            data=xml.encode('utf-8'),
            headers={'Content-Type': 'application/xml'},
            timeout=60,
        )
        if r.status_code >= 400:
            return {'ok': False, 'error': f'HTTP {r.status_code}: {r.text[:400]}'}
        return {'ok': True, 'status_code': r.status_code}
    except Exception as e:
        _log.exception('Prinect push failed')
        return {'ok': False, 'error': str(e)}
