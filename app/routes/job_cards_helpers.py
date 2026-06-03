"""Helpers for job card SAP PO doc entries and numeric form parsing."""
from __future__ import annotations

import json
from typing import List, Optional

from flask import Request


def parse_float(form: Request, key: str, default: Optional[float] = None) -> Optional[float]:
    v = form.get(key)
    if v is None or str(v).strip() == '':
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_int(form: Request, key: str, default: Optional[int] = None) -> Optional[int]:
    v = form.get(key)
    if v is None or str(v).strip() == '':
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def sap_po_doc_entries_list(job_card) -> List[int]:
    raw = getattr(job_card, 'sap_po_doc_entries_json', None) or ''
    if not raw.strip():
        out: List[int] = []
        po = getattr(job_card, 'sap_production_order', None)
        if po:
            try:
                out.append(int(str(po).strip()))
            except ValueError:
                pass
        return out
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        result = []
        for x in data:
            try:
                result.append(int(x))
            except (TypeError, ValueError):
                continue
        return result
    except Exception:
        return []


def append_sap_po_doc_entry(job_card, doc_entry: int) -> None:
    if doc_entry is None:
        return
    try:
        de = int(doc_entry)
    except (TypeError, ValueError):
        return
    entries: List[int] = []
    raw = getattr(job_card, 'sap_po_doc_entries_json', None) or ''
    if raw.strip():
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                for x in data:
                    try:
                        entries.append(int(x))
                    except (TypeError, ValueError):
                        pass
        except Exception:
            pass
    if not entries and getattr(job_card, 'sap_production_order', None):
        try:
            entries = [int(str(job_card.sap_production_order).strip())]
        except ValueError:
            pass
    if de not in entries:
        entries.append(de)
    job_card.sap_po_doc_entries_json = json.dumps(entries)
