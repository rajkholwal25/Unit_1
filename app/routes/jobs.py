from __future__ import annotations

import os
import re
import json
import subprocess
import zipfile
from collections import defaultdict, deque
from decimal import Decimal
from datetime import datetime as dt, date, timedelta
from pathlib import Path
from typing import Optional, Any
from xml.etree import ElementTree as ET

from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, request, abort, jsonify, current_app, session,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.job import JobMaster, JobHeaderLine, JobDetailLine, JobDetailLineFgInvolved
from app.models.mfg_bom import Bom, BomStep, BomStepInput
from app.models.reference import ProcessMaster
from app.models.sap_mirror import SapCustomerMirror, SapItemMirror
from app.services.job_service import (
    create_job, add_header_line, add_header_line_only,
    transition_job_status, can_transition,
    sync_detail_line_fg_involved,
)
from app.services.mfg_bom_service import create_bom, add_step, add_input
from app.services.bom_edit_payload import (
    SAP_OUTSOURCE_LINK_WAREHOUSE,
    bom_block_from_saved_bom,
    detail_material_row_index,
    ensure_final_fg_section,
    fg_planned_qty_for_bom_step,
    fg_planned_qty_pcs,
    gross_sheet_planned_for_detail,
    persist_bom_payload_block,
    planner_line_sequences_from_form,
    slip_process_sequence_json_from_planner_and_sections,
)
from app.services.sap_job_client import SAPClient, SAPClientError
from app.services.sap_mjd1 import upsert_omjd_job_card, find_omjd_by_ver_entry
from app.services.job_so_guard import validate_so_numbers_for_new_job
from app.utils.auth import role_required
from app.utils.sales_rep import resolve_sales_rep_display_name

jobs_bp = Blueprint('jobs', __name__, url_prefix='/jobs')


def _unit1_default_uom() -> str:
    return (
        current_app.config.get('UNIT1_DEFAULT_UOM')
        or current_app.config.get('SAP_BOM_PROCESS_ITEM_UOM')
        or 'KGS'
    ).strip().upper() or 'KGS'


def _default_new_job_delivery_date_iso() -> str:
    """Legacy helper; new job forms leave delivery date empty until SO lines are picked."""
    return ''


def _validate_process_sequence_and_bom(
    detail_indices: list[int],
    process_sequence_raw: str,
    bom_payload: list,
) -> Optional[str]:
    """Require planner sequence and BOM sections for every material/detail row."""
    if not detail_indices:
        return 'Add at least one detail line with raw material (SAP).'
    planner = planner_line_sequences_from_form(process_sequence_raw)
    bom_by_idx: dict[int, dict] = {}
    if isinstance(bom_payload, list):
        for block in bom_payload:
            if not isinstance(block, dict):
                continue
            try:
                li = int(block.get('line_index'))
            except (TypeError, ValueError):
                continue
            bom_by_idx[li] = block
    for idx in detail_indices:
        seq = planner.get(idx) or []
        if not seq:
            return (
                f'Detail line {idx + 1}: select a process sequence before creating the job card.'
            )
        block = bom_by_idx.get(idx)
        sections = (block or {}).get('sections') if isinstance(block, dict) else None
        if not isinstance(sections, list) or not sections:
            return (
                f'Detail line {idx + 1}: build the BOM (process sequence + Create BOM) '
                'before creating the job card.'
            )
    return None


def _bom_input_display_names_by_id(job: JobMaster) -> dict[int, str]:
    """Map ``BomStepInput.id`` → display name for BOM view.

    Process / FG-linked codes use ``unit1_process_item_description`` (pattern
    **name** + process label, e.g. ``PET-12-Rectangle-TR-EMB Embossing``).
    Raw materials use mirror → stored description → live SAP ItemName.
    """
    from sqlalchemy import func

    rows_meta: list[tuple[Any, str, str, JobDetailLine]] = []  # (inp, code_show, code_u, detail)
    codes: set[str] = set()
    code_u_to_fetch_key: dict[str, str] = {}

    for jdl in job.detail_lines.all():
        bom = jdl.active_bom
        if not bom:
            continue
        for step in bom.steps.all():
            for inp in step.inputs.all():
                code_show = (inp.sap_item_code or '').strip()
                code_u = code_show.upper()
                rows_meta.append((inp, code_show, code_u, jdl))
                if not code_u:
                    continue
                codes.add(code_u)
                code_u_to_fetch_key.setdefault(code_u, code_show)

    mirror_map: dict[str, str] = {}
    if codes:
        rows = SapItemMirror.query.filter(
            func.upper(SapItemMirror.item_code).in_(list(codes))
        ).all()
        mirror_map = {
            (r.item_code or '').strip().upper(): (r.item_name or '').strip()
            for r in rows
        }

    by_id: dict[int, str] = {}
    missing_for_sap: set[str] = set()

    for inp, code_show, code_u, jdl in rows_meta:
        synth = _synthetic_display_name_for_process_item_code(job, jdl, code_show)
        if synth:
            by_id[inp.id] = synth
            continue

        nm = (mirror_map.get(code_u, '') if code_u else '').strip()
        desc = (inp.description or '').strip()
        if not nm and desc and code_u and desc.upper() != code_u:
            nm = desc
        if code_u and not nm:
            missing_for_sap.add(code_u)
        by_id[inp.id] = nm

    if missing_for_sap and current_app.config.get('SAP_SERVICE_LAYER_URL'):
        client: SAPClient | None = None
        try:
            client = SAPClient()
            for code_u in list(missing_for_sap)[:40]:
                key = (code_u_to_fetch_key.get(code_u) or code_u).strip()
                if not key:
                    continue
                try:
                    row = client.fetch_item(key)
                except SAPClientError:
                    continue
                if not isinstance(row, dict):
                    continue
                label = (row.get('ItemName') or row.get('ItemDescription') or '').strip()
                if not label:
                    continue
                for inp2, _cs, cu, _jdl in rows_meta:
                    if cu == code_u and not (by_id.get(inp2.id) or '').strip():
                        by_id[inp2.id] = label
        except Exception as e:
            current_app.logger.warning('BOM item name SAP backfill failed: %s', e)
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass

    return by_id


def _normalize_paper_supplied_by(v: str | None) -> str:
    """Map legacy/UI values to DB enum values for JobDetailLine.paper_supplied_by."""
    s = (v or '').strip().lower()
    if s in ('company', 'customer'):
        return s
    # Legacy value seen in older job cards / cached client code
    if s == 'press':
        return 'company'
    # Safe fallback (matches db.Enum default)
    return 'company'


def _safe_windows_filename_segment(value: str | None, fallback: str, max_len: int) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '', (value or '').strip())
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .')
    return (cleaned or fallback)[:max_len].rstrip(' .')


def _job_upload_stem(job: JobMaster) -> str:
    first_line = job.header_lines.first()
    job_name = (
        job.sap_job_card_title_snap
        or (first_line.fg_display_label if first_line else None)
        or job.sap_customer_name_snap
        or 'Job'
    )
    job_no = _safe_windows_filename_segment(job.job_no, 'Job', 40)
    job_name = _safe_windows_filename_segment(job_name, 'Job', 140)
    return f'{job_no}-{job_name}'


def _unc_share_root(path: Path) -> str | None:
    raw = str(path)
    if not raw.startswith('\\\\'):
        return None
    parts = [part for part in raw.split('\\') if part]
    if len(parts) < 2:
        return None
    return f'\\\\{parts[0]}\\{parts[1]}'


def _synology_share_path(share_name: str, extra_parts: list[str] | None = None) -> Path:
    """Resolve a Synology shared folder case-insensitively under /volume1."""
    volume = Path('/volume1')
    extra_parts = extra_parts or []
    if not share_name:
        return volume.joinpath(*extra_parts)
    if volume.exists():
        try:
            for child in volume.iterdir():
                if child.name.lower() == share_name.lower():
                    return child.joinpath(*extra_parts)
        except OSError:
            pass
    return volume.joinpath(share_name, *extra_parts)


def _resolve_job_pdf_upload_dir(raw_path: str | None) -> Path:
    """Return the OS-native path for the configured PDF/ZIP output directory."""
    raw = (raw_path or '').strip()
    if not raw:
        raise KeyError('JOB_PDF_UPLOAD_DIR')

    if os.name == 'nt':
        return Path(raw)

    normalized = raw.replace('\\', '/')
    if normalized.startswith('//'):
        parts = [part for part in normalized.split('/') if part]
        if len(parts) >= 2:
            # //server/share[/subdir] is the Windows view of /volume1/share[/subdir] on Synology.
            return _synology_share_path(parts[1], parts[2:])

    volume_prefix = '/volume1/'
    if normalized.lower().startswith(volume_prefix):
        parts = [part for part in normalized[len(volume_prefix):].split('/') if part]
        if parts:
            return _synology_share_path(parts[0], parts[1:])

    return Path(normalized)


def _ensure_job_pdf_share_access(upload_dir: Path) -> None:
    if os.name != 'nt':
        return
    username = (current_app.config.get('JOB_PDF_SHARE_USERNAME') or '').strip()
    password = current_app.config.get('JOB_PDF_SHARE_PASSWORD') or ''
    if not username or not password:
        return
    share_root = _unc_share_root(upload_dir)
    if not share_root:
        return

    try:
        existing = subprocess.run(
            ['net', 'use', share_root],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if existing.returncode == 0:
            return
        result = subprocess.run(
            ['net', 'use', share_root, password, f'/user:{username}', '/persistent:no'],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise OSError(f'Could not connect to {share_root}: {e}') from e

    if result.returncode != 0:
        msg = (result.stderr or result.stdout or '').strip()
        raise OSError(f'Could not connect to {share_root}: {msg or "net use failed"}')


def _format_ptk_number(value: Any, fallback: str = '0') -> str:
    if value is None:
        return fallback
    try:
        dec = Decimal(str(value))
    except Exception:
        return fallback
    if dec == dec.to_integral():
        return str(int(dec))
    return format(dec.normalize(), 'f')


def _indent_xml(element: ET.Element, space: str = '    ') -> None:
    """Pretty-print XML on Python versions that predate ElementTree.indent."""
    indent = getattr(ET, 'indent', None)
    if indent is not None:
        indent(element, space=space)
        return

    def apply_indent(node: ET.Element, level: int = 0) -> None:
        children = list(node)
        if not children:
            return
        child_indent = '\n' + (space * (level + 1))
        if not node.text or not node.text.strip():
            node.text = child_indent
        for child in children:
            apply_indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = child_indent
        if not children[-1].tail or not children[-1].tail.strip():
            children[-1].tail = '\n' + (space * level)

    apply_indent(element)


def _first_job_detail_for_line(job: JobMaster, line: JobHeaderLine | None) -> JobDetailLine | None:
    if line is None:
        return job.detail_lines.first()
    return JobDetailLine.query.filter_by(job_id=job.job_no, detail_no=line.line_no).first()


def _job_display_name(job: JobMaster, line: JobHeaderLine | None = None) -> str:
    return (
        job.sap_job_card_title_snap
        or (line.fg_display_label if line else None)
        or job.sap_customer_name_snap
        or job.job_no
    )


def _job_process_upload_stem(job: JobMaster, step: BomStep) -> str:
    first_line = job.header_lines.first()
    production_order = _safe_windows_filename_segment(
        str(step.sap_doc_num or step.sap_doc_entry or ''),
        'ProductionOrder',
        40,
    )
    job_no = _safe_windows_filename_segment(job.job_no, 'Job', 40)
    job_name = _safe_windows_filename_segment(_job_display_name(job, first_line), 'Job', 140)
    return f'{production_order}#{job_no}-{job_name}'


def _safe_pdf_zip_member_name(filename: str | None, fallback_stem: str) -> str:
    raw_name = Path(filename or '').name
    raw_stem = Path(raw_name).stem if raw_name else ''
    safe_stem = _safe_windows_filename_segment(raw_stem, fallback_stem, 160)
    return f'{safe_stem}.pdf'


def _build_printtalk_ptk(
    job: JobMaster,
    line: JobHeaderLine | None,
    pdf_zip_path: str,
    *,
    job_id: str | None = None,
    descriptive_name: str | None = None,
) -> bytes:
    detail = _first_job_detail_for_line(job, line)
    delivery = job.delivery_date or date.today()
    finished_dimensions = ' '.join([
        _format_ptk_number(getattr(line, 'length', None)),
        _format_ptk_number(getattr(line, 'width', None)),
        _format_ptk_number(getattr(line, 'height', None)),
    ])
    media_quality = (
        getattr(detail, 'paper_brand', None)
        or getattr(detail, 'raw_material_item_code', None)
        or current_app.config.get('PRINECT_PTK_MEDIA_QUALITY')
        or 'UNKNOWN'
    )
    sides = 'TwoSided' if getattr(detail, 'back_colours', None) else 'OneSided'
    priority_map = {'urgent': '90', 'normal': '50', 'low': '10'}

    ET.register_namespace('ptk', 'http://www.printtalk.org/schema_20')
    ET.register_namespace('xjdf', 'http://www.CIP4.org/JDFSchema_2_0')
    ptk_ns = 'http://www.printtalk.org/schema_20'
    xjdf_ns = 'http://www.CIP4.org/JDFSchema_2_0'
    effective_job_id = str(job_id or job.job_no)

    root = ET.Element(
        f'{{{ptk_ns}}}PrintTalk',
        {'timestamp': dt.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')},
    )
    header = ET.SubElement(root, f'{{{ptk_ns}}}Header')
    for tag, domain, identity in (
        ('From', 'EasyXJDF', current_app.config.get('PRINECT_PTK_FROM_IDENTITY', 'FromIdentity')),
        ('To', 'TargetSystem', current_app.config.get('PRINECT_PTK_TO_IDENTITY', 'ToIdentity')),
    ):
        party = ET.SubElement(header, f'{{{ptk_ns}}}{tag}')
        credential = ET.SubElement(party, f'{{{ptk_ns}}}Credential', {'domain': domain})
        ET.SubElement(credential, f'{{{ptk_ns}}}Identity').text = identity

    request_el = ET.SubElement(root, f'{{{ptk_ns}}}Request', {'BusinessID': effective_job_id})
    purchase_order = ET.SubElement(
        request_el,
        f'{{{ptk_ns}}}PurchaseOrder',
        {'Expires': f'{delivery.isoformat()}T00:00:00Z'},
    )
    xjdf = ET.SubElement(
        purchase_order,
        f'{{{xjdf_ns}}}XJDF',
        {
            'Category': 'Web2Print',
            'DescriptiveName': descriptive_name or _job_display_name(job, line),
            'JobID': effective_job_id,
            'Types': 'Product',
        },
    )
    ET.SubElement(
        xjdf,
        'GeneralID',
        {
            'IDUsage': 'CatalogID',
            'IDValue': current_app.config.get('PRINECT_PTK_CATALOG_ID', 'SM74_4'),
        },
    )

    product_list = ET.SubElement(xjdf, f'{{{xjdf_ns}}}ProductList')
    product = ET.SubElement(
        product_list,
        f'{{{xjdf_ns}}}Product',
        {'Amount': _format_ptk_number(getattr(line, 'dispatch_qty', None), '1')},
    )
    media_intent = ET.SubElement(product, f'{{{xjdf_ns}}}Intent', {'Name': 'MediaIntent'})
    ET.SubElement(media_intent, f'{{{xjdf_ns}}}MediaIntent', {'MediaQuality': str(media_quality)})
    layout_intent = ET.SubElement(product, f'{{{xjdf_ns}}}Intent', {'Name': 'LayoutIntent'})
    ET.SubElement(
        layout_intent,
        f'{{{xjdf_ns}}}LayoutIntent',
        {'FinishedDimensions': finished_dimensions, 'Sides': sides},
    )

    customer_set = ET.SubElement(xjdf, f'{{{xjdf_ns}}}ResourceSet', {'Name': 'CustomerInfo'})
    customer_resource = ET.SubElement(customer_set, f'{{{xjdf_ns}}}Resource')
    ET.SubElement(
        customer_resource,
        f'{{{xjdf_ns}}}CustomerInfo',
        {'CustomerID': job.sap_customer_code or job.sap_customer_name_snap or ''},
    )

    node_set = ET.SubElement(xjdf, f'{{{xjdf_ns}}}ResourceSet', {'Name': 'NodeInfo', 'Usage': 'Input'})
    node_resource = ET.SubElement(node_set, f'{{{xjdf_ns}}}Resource')
    ET.SubElement(
        node_resource,
        f'{{{xjdf_ns}}}NodeInfo',
        {
            'JobPriority': priority_map.get(job.priority or 'normal', '50'),
            'LastEnd': f'{delivery.isoformat()}T12:00:00+05:30',
        },
    )

    run_set = ET.SubElement(xjdf, f'{{{xjdf_ns}}}ResourceSet', {'Name': 'RunList'})
    run_resource = ET.SubElement(run_set, f'{{{xjdf_ns}}}Resource')
    run_list = ET.SubElement(run_resource, f'{{{xjdf_ns}}}RunList')
    ET.SubElement(run_list, f'{{{xjdf_ns}}}FileSpec', {'URL': pdf_zip_path})

    _indent_xml(root, space='    ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


PROCESS_CODE_FALLBACKS = {
    'printing': 'PR-OFF',
    'print-back': 'PRIB',
    'lamination': 'PO-LAM',
    'foiling': 'FN-FOIL',
    'diecutting': 'CV-DIE',
    'die': 'CV-DIE',
    'cv-die': 'CV-DIE',
    'diecutting+embossing': 'CV-DIE',
    'emb+p': 'CV-DIE',
    'embossing': 'FN-EMB',
    'pasting': 'CV-GLUE',
    'spot uv': 'FN-SPOT',
    'fg': 'PK-PACK',
}

# When seeds / UI disagree on ``process_code`` (e.g. ``FN-EMB`` vs ``EMB``), try alternates before
# dropping the step — otherwise ``persist_bom_payload_block`` skips the section and downstream
# steps still chain from the last real predecessor (Printing instead of Embossing).
PROCESS_CODE_FALLBACK_ALIASES: dict[str, tuple[str, ...]] = {
    'CV-DIE': ('DIE', 'DIECUT', 'DIECUTTING'),
    'DIE': ('CV-DIE', 'DIECUT', 'DIECUTTING'),
    'FN-EMB': ('EMB',),
    'EMB': ('FN-EMB',),
}


def _parse_sap_item_code_from_form_field(raw: str) -> tuple[str, str]:
    """Split BOM / PO autocomplete values ``CODE — Name`` into SAP code and optional item name.

    Matches new-job BOM datalist (Unicode em dash) and common hyphen variants.
    """
    s = (raw or '').strip()
    if not s:
        return '', ''
    for sep in ('\u2014', ' — ', '\u2013'):  # em dash, spaced ASCII hyphen, en dash
        if sep in s:
            left, right = s.split(sep, 1)
            return left.strip()[:50], (right or '').strip()[:200]
    if '—' in s:  # literal Unicode em dash without surrounding spaces
        left, right = s.split('—', 1)
        return left.strip()[:50], (right or '').strip()[:200]
    return s[:50], ''


def _sanitize_item_segment(s: str, max_len: int) -> str:
    v = re.sub(r'[^A-Za-z0-9]', '', (s or '').upper())
    return v[:max_len]


def extract_fg_num(fg_code: str) -> str:
    """Extracts exactly the 'FGxxxxxx' part from arbitrary strings."""
    if not fg_code:
        return 'FG'
    match = re.search(r'(FG\d+)', fg_code, re.IGNORECASE)
    return match.group(1).upper() if match else fg_code.strip()


def _selected_line_fg_tokens(row: dict) -> set[str]:
    tokens: set[str] = set()
    if not isinstance(row, dict):
        return tokens
    for key in ('fg_code', 'supplier_cat_num', 'fg_name'):
        raw = str(row.get(key) or '').strip()
        if raw:
            tokens.add(extract_fg_num(raw))
    return {t for t in tokens if t}


def _drop_replaced_unlinked_selected_lines(lines: list) -> list:
    """Remove repeat placeholders after matching linked SO rows have been selected."""
    if not isinstance(lines, list):
        return []
    linked_tokens = [
        _selected_line_fg_tokens(row)
        for row in lines
        if isinstance(row, dict) and _safe_int(row.get('doc_entry')) is not None
    ]
    linked_tokens = [t for t in linked_tokens if t]
    if not linked_tokens:
        return lines
    out = []
    for row in lines:
        if not isinstance(row, dict):
            out.append(row)
            continue
        if _safe_int(row.get('doc_entry')) is not None:
            out.append(row)
            continue
        tokens = _selected_line_fg_tokens(row)
        if tokens and any(tokens.intersection(lt) for lt in linked_tokens):
            continue
        out.append(row)
    return out


def _safe_int(value) -> Optional[int]:
    try:
        if value in (None, ''):
            return None
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _safe_float(value) -> Optional[float]:
    try:
        if value in (None, ''):
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sync_selected_fg_job_refs_to_sap(
    customer_code: str,
    job_no: str,
    selected_lines: list[dict],
) -> list[str]:
    """Write the created job number back to matching open SO FG rows in SAP."""
    if not customer_code or not job_no or not selected_lines:
        return []

    warnings: list[str] = []
    client = SAPClient()
    try:
        try:
            open_orders = client.fetch_open_sales_orders_ordr(customer_code)
        except SAPClientError as e:
            msg = f'Could not load open Sales Orders for FG-to-job sync: {str(e)[:220]}'
            current_app.logger.warning('[SAP-SO-SYNC] %s', msg)
            return [msg]

        live_lines_by_doc: dict[int, list[dict]] = {}
        for order in open_orders:
            doc_entry = _safe_int(order.get('doc_entry'))
            if doc_entry is None:
                continue
            try:
                live_lines_by_doc[doc_entry] = client.fetch_rdr1_fg_lines(doc_entry)
            except SAPClientError as e:
                msg = f'Order {doc_entry}: could not read FG lines ({str(e)[:180]})'
                current_app.logger.warning('[SAP-SO-SYNC] %s', msg)
                warnings.append(msg)

        updates_by_doc: dict[int, dict[int, dict]] = defaultdict(dict)
        qty_tol = 0.0001

        for row in selected_lines or []:
            if not isinstance(row, dict):
                continue
            target_fg_num = extract_fg_num(str(row.get('fg_code') or ''))
            if not target_fg_num:
                continue
            target_qty = _safe_float(row.get('quantity'))
            preferred_doc_entry = _safe_int(row.get('doc_entry'))
            preferred_line_num = _safe_int(row.get('line_num'))

            candidate_pool: list[tuple[int, dict]] = []
            doc_entries = []
            if preferred_doc_entry is not None and preferred_doc_entry in live_lines_by_doc:
                doc_entries.append(preferred_doc_entry)
            if not doc_entries:
                doc_entries = list(live_lines_by_doc.keys())

            for doc_entry in doc_entries:
                for live_row in live_lines_by_doc.get(doc_entry) or []:
                    if extract_fg_num(str(live_row.get('fg_code') or '')) == target_fg_num:
                        candidate_pool.append((doc_entry, live_row))

            if not candidate_pool:
                continue

            chosen: list[tuple[int, dict]] = []
            exact_match = None
            if preferred_doc_entry is not None and preferred_line_num is not None:
                for doc_entry, live_row in candidate_pool:
                    if doc_entry != preferred_doc_entry:
                        continue
                    if _safe_int(live_row.get('line_num')) == preferred_line_num:
                        exact_match = (doc_entry, live_row)
                        break

            if exact_match is not None:
                if target_qty is None:
                    chosen = [exact_match]
                else:
                    exact_qty = _safe_float(exact_match[1].get('quantity'))
                    if exact_qty is not None and abs(exact_qty - target_qty) <= qty_tol:
                        chosen = [exact_match]

            if not chosen and target_qty is not None:
                qty_matches = [
                    (doc_entry, live_row)
                    for doc_entry, live_row in candidate_pool
                    if (
                        _safe_float(live_row.get('quantity')) is not None
                        and abs(_safe_float(live_row.get('quantity')) - target_qty) <= qty_tol
                    )
                ]
                if len(qty_matches) == 1:
                    chosen = qty_matches
                elif len(candidate_pool) > 1:
                    total_qty = sum(_safe_float(live_row.get('quantity')) or 0.0 for _, live_row in candidate_pool)
                    if abs(total_qty - target_qty) <= qty_tol:
                        chosen = candidate_pool

            if not chosen:
                chosen = candidate_pool

            for doc_entry, live_row in chosen:
                line_num = _safe_int(live_row.get('line_num'))
                item_code = str(live_row.get('fg_code') or '').strip()
                if line_num is None or not item_code:
                    continue
                updates_by_doc[doc_entry][line_num] = {
                    'LineNum': line_num,
                    'ItemCode': item_code,
                    'U_JEntry': job_no,
                }

        if not updates_by_doc:
            return []

        for doc_entry, line_map in updates_by_doc.items():
            payload = [line_map[k] for k in sorted(line_map)]
            try:
                client.patch_sales_order_line_job_refs(doc_entry, payload)
            except SAPClientError as e:
                msg = f'Order {doc_entry}: could not write U_JEntry ({str(e)[:180]})'
                current_app.logger.warning('[SAP-SO-SYNC] %s', msg)
                warnings.append(msg)

        return warnings
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _sync_selected_so_quantities_to_sap(selected_lines: list[dict]) -> list[str]:
    """Push edited quantity from job form back to open SO RDR1 lines when changed."""
    if not selected_lines:
        return []

    warnings: list[str] = []
    client = SAPClient()
    updates_by_doc: dict[int, list[dict]] = defaultdict(list)

    try:
        for row in selected_lines or []:
            if not isinstance(row, dict):
                continue
            doc_entry = _safe_int(row.get('doc_entry'))
            line_num = _safe_int(row.get('line_num'))
            item_code = str(row.get('fg_code') or '').strip()
            if doc_entry is None or line_num is None or not item_code:
                continue
            qty = _safe_float(row.get('quantity'))
            if qty is None:
                continue
            sap_qty = _safe_float(row.get('sap_quantity'))
            if sap_qty is None:
                sap_qty = _safe_float(row.get('dispatch_qty'))
            if sap_qty is not None and abs(qty - sap_qty) <= 0.0001:
                continue
            updates_by_doc[doc_entry].append({
                'LineNum': line_num,
                'ItemCode': item_code,
                'quantity': qty,
            })

        for doc_entry, payloads in updates_by_doc.items():
            try:
                client.patch_sales_order_line_quantities(doc_entry, payloads)
            except SAPClientError as e:
                msg = f'Order {doc_entry}: could not update quantity ({str(e)[:180]})'
                current_app.logger.warning('[SAP-SO-SYNC] %s', msg)
                warnings.append(msg)
        return warnings
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _sync_selected_so_dimensions_to_sap(selected_lines: list[dict]) -> list[str]:
    """Push width/height from job form back to open SO RDR1 lines when changed."""
    if not selected_lines:
        return []

    warnings: list[str] = []
    client = SAPClient()
    updates_by_doc: dict[int, list[dict]] = defaultdict(list)

    try:
        for row in selected_lines or []:
            if not isinstance(row, dict):
                continue
            doc_entry = _safe_int(row.get('doc_entry'))
            line_num = _safe_int(row.get('line_num'))
            item_code = str(row.get('fg_code') or '').strip()
            if doc_entry is None or line_num is None or not item_code:
                continue
            width_mm = _safe_float(row.get('carton_width_mm'))
            if width_mm is None:
                continue
            sap_width = _safe_float(row.get('sap_carton_width_mm'))
            height_mm = _safe_float(row.get('carton_height_mm'))
            sap_height = _safe_float(row.get('sap_carton_height_mm'))
            width_changed = sap_width is None or abs(width_mm - sap_width) > 0.0001
            height_changed = (
                height_mm is not None
                and (sap_height is None or abs(height_mm - sap_height) > 0.0001)
            )
            if not width_changed and not height_changed:
                continue
            payload: dict = {
                'LineNum': line_num,
                'ItemCode': item_code,
                'width_mm': width_mm,
            }
            if height_changed and height_mm is not None:
                payload['height_mm'] = height_mm
            updates_by_doc[doc_entry].append(payload)

        for doc_entry, payloads in updates_by_doc.items():
            try:
                client.patch_sales_order_line_dimensions(doc_entry, payloads)
            except SAPClientError as e:
                msg = f'Order {doc_entry}: could not update width ({str(e)[:180]})'
                current_app.logger.warning('[SAP-SO-SYNC] %s', msg)
                warnings.append(msg)
        return warnings
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _build_process_tail_label_map() -> dict[str, str]:
    """Map uppercased process tail / full ``process_code`` → ``process_master.name``.

    Keys include every active ``process_code`` (upper), plus the last ``-`` segment
    of each code when that suffix maps to a single display name (or the shortest
    ``process_code`` among ties) so item codes like ``FG…-MON-DIE`` still resolve.
    """
    exact: dict[str, str] = {}
    suffix_pairs: dict[str, list[tuple[str, str]]] = {}

    for row in ProcessMaster.query.filter_by(is_active=True).order_by(
        ProcessMaster.process_code
    ).all():
        code_u = (row.process_code or '').strip().upper()
        nm = (row.name or '').strip()
        if not code_u or not nm:
            continue
        exact[code_u] = nm
        parts = code_u.split('-')
        suff = parts[-1] if parts else ''
        if len(suff) >= 2:
            suffix_pairs.setdefault(suff, []).append((code_u, nm))

    out = dict(exact)
    for suff, pairs in suffix_pairs.items():
        names = {p[1] for p in pairs}
        if suff in out:
            continue
        if len(names) == 1:
            out[suff] = next(iter(names))
        else:
            # Same last segment, different processes — pick shortest process_code row
            code_nm = min(pairs, key=lambda p: len(p[0]))
            out[suff] = code_nm[1]

    return out


def _synthetic_display_name_for_process_item_code(
    job: JobMaster,
    detail_line: Optional[JobDetailLine],
    item_code: str,
) -> str:
    """Human-readable SAP name, e.g. PET-12-Rectangle-TR-EMB Embossing."""
    from app.services.unit1_item_naming import unit1_process_item_description
    from app.services.unit1_processes import unit1_fg_base_code

    code = (item_code or '').strip()
    if not code:
        return ''
    desc = unit1_process_item_description(code)
    if desc and desc != code:
        return desc[:100]
    base = unit1_fg_base_code(code)
    if code.upper() == base.upper():
        return ''
    hdr = None
    for hl in job.header_lines.order_by(JobHeaderLine.line_no).all():
        if unit1_fg_base_code(hl.sap_fg_item_code or '') == base:
            hdr = hl
            break
    if hdr:
        return unit1_process_item_description(code)[:100]
    return ''


def _process_item_code(fg_code: str, element_name: str, process_code: str) -> str:
    """Unit 1: ``{FG item}-{process}`` e.g. PET-12-1009-TR-EMB (no element/GEN segment)."""
    from app.services.unit1_processes import unit1_process_item_code

    _ = element_name  # legacy param; not used in Unit 1 codes
    return unit1_process_item_code(fg_code, process_code)


def _resolve_process_code(process_name: str, hinted_code: Optional[str] = None) -> Optional[str]:
    name = (process_name or '').strip()
    code = (hinted_code or '').strip()

    # FG is a special pseudo-step in the UI; when present it must map to a real ProcessMaster row.
    # BomStep.process_code FK must reference a real row — prefer PK-PACK (seeded), else any FG row you add.
    if name.upper() == 'FG' or (code and code.upper() == 'FG'):
        for candidate in ('FG', 'PK-PACK'):
            row = ProcessMaster.query.filter_by(process_code=candidate).first()
            if row:
                return row.process_code
        current_app.logger.warning(
            '[BOM] FG step skipped: add process_master rows PK-PACK (packing) or FG. '
            'Run seeds / recreate_db.sql INSERT INTO process_master.'
        )
        return None

    from app.services.unit1_processes import normalize_unit1_process_code

    code = normalize_unit1_process_code(code) if code else code

    # 1. Try hinted code if it's valid in our system (prefer active Unit 1 rows)
    if code:
        row = (
            ProcessMaster.query.filter_by(process_code=code, is_active=True).first()
            or ProcessMaster.query.filter_by(process_code=code).first()
        )
        if row:
            return normalize_unit1_process_code(row.process_code)

    # 2. Try to find by name (case insensitive) — active rows only (avoids legacy COAT vs COT)
    if name:
        exact = (
            ProcessMaster.query.filter(
                db.func.lower(ProcessMaster.name) == name.lower(),
                ProcessMaster.is_active.is_(True),
            )
            .order_by(ProcessMaster.process_code.desc())
            .first()
        )
        if exact:
            return normalize_unit1_process_code(exact.process_code)
        
        # 3. Try Fallbacks (and common alternate codes for the same logical process)
        mapped = PROCESS_CODE_FALLBACKS.get(name.lower())
        if mapped:
            row = ProcessMaster.query.filter_by(process_code=mapped).first()
            if row:
                return mapped
            for alt in PROCESS_CODE_FALLBACK_ALIASES.get(mapped, ()):
                row = ProcessMaster.query.filter_by(process_code=alt).first()
                if row:
                    return alt

        # 4. Final attempt: Return None if we cannot find a valid process
        # This safely skips the step instead of crashing with a Foreign Key error.
        return None

    return None


def _upsert_customer_mirror(
    card_code: str,
    card_name: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> None:
    now = dt.utcnow()
    row = SapCustomerMirror.query.get(card_code)
    if row:
        if card_name:
            row.card_name = card_name[:200]
        if phone is not None:
            row.phone = (phone or '')[:100] or None
        if email is not None:
            row.email = (email or '')[:120] or None
        row.synced_at = now
        return
    db.session.add(
        SapCustomerMirror(
            card_code=(card_code or '')[:30],
            card_name=(card_name or card_code)[:200],
            phone=(phone or '')[:100] or None,
            email=(email or '')[:120] or None,
            synced_at=now,
        )
    )


def parse_sap_date(val):
    """Parse SAP Service Layer date (ISO or /Date(ms)/)."""
    if val is None:
        return None
    if isinstance(val, str) and val.startswith('/Date('):
        m = re.search(r'/Date\((\d+)\)', val)
        if m:
            ms = int(m.group(1))
            return dt.utcfromtimestamp(ms / 1000).date()
    if isinstance(val, str) and len(val) >= 10 and val[4] == '-':
        try:
            return dt.strptime(val[:10], '%Y-%m-%d').date()
        except ValueError:
            return None
    return None


def _decimal_or_none(val) -> Optional[Decimal]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


def _yield_loss_pct_from_form(idx: int) -> Decimal:
    """Unit 1 job form: RM gross-up is wastage-only (yield not used)."""
    _ = idx
    return Decimal('0')


def _material_detail_indices_from_form() -> list[int]:
    """Indices of filled material/detail rows (Unit 1: keyed by raw material SAP code)."""
    indices: set[int] = set()
    for k in request.form.keys():
        if not k.startswith('mat_raw_material_'):
            continue
        try:
            i = int(k.rsplit('_', 1)[-1])
        except (TypeError, ValueError):
            continue
        if (request.form.get(k, '') or '').strip():
            indices.add(i)
    return sorted(indices)


def _int_or_none(val) -> Optional[int]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _apply_non_bom_job_detail_fields(
    detail: JobDetailLine,
    key_prefix: str = '',
) -> None:
    """Update print/substrate fields only; BOM-driving columns stay unchanged.

    ``key_prefix`` is the stem before each field name, e.g. ``dl_12`` for
    ``dl_12_paper_brand``, or empty for plain ``paper_brand`` (line detail form).
    """
    def k(name: str) -> str:
        return f'{key_prefix}_{name}' if key_prefix else name

    yl = (request.form.get(k('yield_loss_pct'), '') or '').strip()
    if yl:
        try:
            detail.yield_loss_pct = Decimal(str(max(0.0, min(float(yl), 99.9))))
        except (TypeError, ValueError):
            pass
    detail.paper_brand = request.form.get(k('paper_brand'), '').strip() or None
    detail.mill = request.form.get(k('mill'), '').strip() or None
    detail.gsm = _int_or_none(request.form.get(k('gsm')))
    detail.sheet_length = _decimal_or_none(request.form.get(k('sheet_length')))
    detail.sheet_width = _decimal_or_none(request.form.get(k('sheet_width')))
    psb = request.form.get(k('paper_supplied_by'), 'company')
    detail.paper_supplied_by = 'customer' if psb == 'customer' else 'company'
    detail.print_style = request.form.get(k('print_style'), '').strip() or None
    detail.print_type = request.form.get(k('print_type'), '').strip() or None
    detail.front_colours = request.form.get(k('front_colours'), '').strip() or None
    detail.back_colours = request.form.get(k('back_colours'), '').strip() or None
    detail.die_no = request.form.get(k('die_no'), '').strip() or None
    detail.pasting_style = request.form.get(k('pasting_style'), '').strip() or None
    detail.special_instructions = request.form.get(k('special_instructions'), '').strip() or None
    detail.compute_wastage()


def resolve_customer_name_from_sap(card_code: str) -> str:
    """Prefer SAP customer mirror, then live BP, then code."""
    from app.services.sap_job_client import SAPClient, SAPClientError

    m = SapCustomerMirror.query.get(card_code)
    if m and m.card_name:
        return m.card_name.strip()

    try:
        client = SAPClient()
        try:
            bp = client.fetch_business_partner(card_code)
        finally:
            client.logout()
        name = bp.get('CardName') or card_code
        _upsert_customer_mirror(
            card_code,
            card_name=name,
            phone=bp.get('Phone1'),
            email=bp.get('EmailAddress'),
        )
        db.session.commit()
        return name
    except SAPClientError:
        db.session.rollback()
    return card_code


# ------------------------------------------------------------------ LIST (manufacturing jobs)
@jobs_bp.route('/')
@login_required
def list_jobs():
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    customer_filter = request.args.get('customer', '').strip()
    priority_filter = request.args.get('priority', '')

    query = JobMaster.query.order_by(JobMaster.created_at.desc())

    if status_filter:
        query = query.filter_by(overall_status=status_filter)
    if customer_filter:
        query = query.filter(
            JobMaster.sap_customer_name_snap.ilike(f'%{customer_filter}%')
        )
    if priority_filter:
        query = query.filter_by(priority=priority_filter)

    pagination = query.paginate(
        page=page,
        per_page=20,
        error_out=False,
    )

    return render_template(
        'jobs/unified.html',
        jobs=pagination.items,
        pagination=pagination,
        status_filter=status_filter,
        customer_filter=customer_filter,
        priority_filter=priority_filter,
        list_endpoint='jobs.list_jobs',
        page_title='Jobs',
        status_choices=('open', 'staged', 'released', 'closed', 'cancelled'),
    )


def _header_line_for_bom_step(job: JobMaster, step_output_item_code: Optional[str]) -> Optional[JobHeaderLine]:
    """Resolve which FG/header line a BOM step belongs to (multi-FG combi jobs)."""
    headers = list(job.header_lines.order_by(JobHeaderLine.line_no).all())
    if not headers:
        return None
    raw = (step_output_item_code or '').strip()
    if not raw:
        return headers[0]
    wanted = extract_fg_num(raw)
    for hl in headers:
        if extract_fg_num(hl.sap_fg_item_code or '') == wanted:
            return hl
    return headers[0]


def _bom_step_identity_key(st: BomStep) -> tuple[str, str]:
    """Match steps across BOM revisions: process + output item (FG / routing code)."""
    pc = (st.process_code or '').strip().upper()
    oc = (st.output_item_code or '').strip().upper()
    return (pc, oc)


def _transfer_sap_po_from_inactive_boms(
    detail_line: JobDetailLine,
    new_bom: Bom,
) -> tuple[list[tuple[BomStep, BomStep]], int]:
    """Move SAP DocEntry/DocNum from **any** inactive BOM step onto matching new steps.

    Queues are filled from inactive BOMs ordered by **version descending** (newest superseded
    first) so the most recently linked production order is reused when process+output keys match.

    This fixes cases where only the immediate predecessor had no POs (regen → new version)
    but an older inactive BOM still holds the live ``sap_doc_entry`` rows.

    Returns ``(pairs, transferred_count)`` where each pair is ``(new_step, old_step)`` for
    callers that need to diff old vs new payloads for minimal PATCH.
    """
    pairs: list[tuple[BomStep, BomStep]] = []
    queues: dict[tuple[str, str], deque] = defaultdict(deque)
    inactive = (
        Bom.query.filter_by(detail_line_id=detail_line.id, is_active=False)
        .order_by(Bom.version.desc())
        .all()
    )
    for ob in inactive:
        for ost in ob.steps.order_by(BomStep.seq_no).all():
            if ost.sap_doc_entry:
                queues[_bom_step_identity_key(ost)].append(ost)
    new_steps = list(new_bom.steps.order_by(BomStep.seq_no).all())
    transferred = 0
    for nst in new_steps:
        if nst.sap_doc_entry:
            continue
        dq = queues.get(_bom_step_identity_key(nst))
        if not dq:
            continue
        ost = dq.popleft()
        nst.sap_doc_entry = ost.sap_doc_entry
        nst.sap_doc_num = ost.sap_doc_num
        ost.sap_doc_entry = None
        ost.sap_doc_num = None
        pairs.append((nst, ost))
        transferred += 1
    if transferred:
        current_app.logger.info(
            '[SAP-RELINK] Transferred %s production order link(s) onto BOM v%s from inactive pool',
            transferred,
            new_bom.version,
        )
    return pairs, transferred


def _relink_map_from_pairs(pairs: list[tuple[BomStep, BomStep]]) -> dict[int, BomStep]:
    """``new_step.id`` → old ``BomStep`` (for SAP payload diff / minimal PATCH)."""
    return {nst.id: ost for nst, ost in pairs}


def _sap_po_posting_and_due_strings(job: JobMaster) -> tuple[str, str]:
    """PostingDate / DueDate strings for SAP production orders (same rules as legacy push)."""
    override_date = current_app.config.get('SAP_OVERRIDE_POSTING_DATE')
    today_dt = date.today()
    posting_dt = dt.strptime(override_date, '%Y-%m-%d').date() if override_date else today_dt
    today = posting_dt.strftime('%Y-%m-%d')
    if job.delivery_date:
        due_dt = job.delivery_date
    else:
        due_dt = posting_dt + timedelta(days=20)
    if due_dt and due_dt < posting_dt:
        current_app.logger.warning(
            '[SAP-PUSH] Delivery due date %s is before posting %s; clamping due date to posting date.',
            due_dt,
            posting_dt,
        )
        due_dt = posting_dt
    due = due_dt.strftime('%Y-%m-%d') if due_dt else today
    return today, due


def _sap_job_category_code(job: JobMaster) -> str:
    cat = (job.job_type_cat or 'Mono').strip().lower()
    if cat == 'rigid':
        return 'ORJD'
    if cat == 'commercial':
        return 'OCJD'
    return 'OMJD'


def _ensure_omjd_doc_entry_for_job(sap_client: SAPClient, job: JobMaster) -> str:
    omjd_result = upsert_omjd_job_card(sap_client, job)
    omjd_doc_entry = omjd_result.get('doc_entry')
    if omjd_doc_entry in (None, ''):
        raise SAPClientError(
            f'OMJD SAP {omjd_result.get("action", "upsert")} for {job.job_no} '
            'did not return a DocEntry.'
        )
    return str(omjd_doc_entry)


def _bom_step_special_po_params(job: JobMaster, bom: Bom, step: BomStep, default_warehouse: str) -> dict:
    """Header + component lines for a special production order (create or full PATCH).

    ``PlannedQuantity`` uses **``BomStep.planned_qty``** (Unit 1: kilograms). If null, FG uses
    header ``dispatch_qty`` (kg); other steps use gross kg from detail line, else 1.0.
    """
    header_line = _header_line_for_bom_step(job, step.output_item_code)
    detail = bom.detail_line
    warehouse = step.sap_warehouse or default_warehouse
    item_no = (step.output_item_code or '').strip()
    if not item_no:
        element_name = detail.element_name if detail and detail.element_name else ''
        fg_code = header_line.sap_fg_item_code or '' if header_line else ''
        item_no = _process_item_code(fg_code, element_name, step.process_code)
    input_lines: list[dict] = []
    header_item_u = item_no.strip().upper()
    seen_input_codes: set[str] = set()
    for inp in step.inputs.filter(BomStepInput.sap_item_code.isnot(None)).all():
        qty = float(inp.effective_qty) if inp.effective_qty else 1.0
        code = (inp.sap_item_code or '').strip()
        code_u = code.upper()
        if not code_u:
            continue
        if header_item_u and code_u == header_item_u:
            current_app.logger.warning(
                '[SAP-PUSH] Skipping self-referencing component: step=%s item=%s',
                step.seq_no,
                code,
            )
            continue
        if code_u in seen_input_codes:
            current_app.logger.warning(
                '[SAP-PUSH] Skipping duplicate component: step=%s item=%s',
                step.seq_no,
                code,
            )
            continue
        seen_input_codes.add(code_u)
        line_item_name = (inp.description or '').strip()
        if not line_item_name:
            line_item_name = _synthetic_display_name_for_process_item_code(job, detail, code) or ''
        if not line_item_name:
            line_item_name = code[:100]
        row: dict = {
            'ItemNo': code,
            'PlannedQuantity': qty,
            'Warehouse': inp.sap_warehouse or warehouse,
            'ProductionOrderIssueType': 'im_Manual',
            'ItemName': (line_item_name or code)[:100],
        }
        input_lines.append(row)
    if step.planned_qty is not None:
        step_qty = float(step.planned_qty)
    else:
        hl = header_line
        pcode = (step.process_code or '').strip().upper()
        if (
            detail
            and hl
            and _is_fg_packaging_process_code(pcode)
            and _is_fg_output_item_for_job(job, (step.output_item_code or '').strip())
        ):
            try:
                step_qty = float(fg_planned_qty_pcs(job, detail, hl))
            except Exception:
                step_qty = float(hl.dispatch_qty or 1)
        elif detail is not None:
            try:
                tw = int(detail.total_sheets_with_wastage)
            except (TypeError, ValueError):
                tw = 0
            if tw > 0:
                step_qty = float(tw)
            else:
                current_app.logger.warning(
                    '[SAP-PO] BomStep seq=%s process=%s has no planned_qty and no gross kg; using 1.0',
                    step.seq_no,
                    step.process_code,
                )
                step_qty = 1.0
        else:
            current_app.logger.warning(
                '[SAP-PO] BomStep seq=%s process=%s has no planned_qty and no kg fallback; using 1.0',
                step.seq_no,
                step.process_code,
            )
            step_qty = 1.0
    auto_remarks = f'Job {job.job_no} | Step {step.seq_no}: {step.step_name}'
    user_po = (getattr(step, 'production_order_remarks', None) or '').strip()
    remarks = (user_po[:254] if user_po else auto_remarks[:254])
    return {
        'item_no': item_no,
        'warehouse': warehouse,
        'step_qty': step_qty,
        'remarks': remarks,
        'input_lines': input_lines,
        'header_fg': header_line.sap_fg_item_code if header_line else None,
        'job_no': (job.job_no or '').strip(),
        'job_category_code': _sap_job_category_code(job),
        'process_code': (step.process_code or '').strip(),
    }


def _bom_step_immediate_predecessor(bom: Bom, step: BomStep) -> Optional[BomStep]:
    """The routing step immediately before ``step`` on the same BOM (by ``seq_no``)."""
    return (
        BomStep.query.filter(
            BomStep.bom_id == bom.id,
            BomStep.seq_no < step.seq_no,
        )
        .order_by(BomStep.seq_no.desc())
        .first()
    )


def _coproduct_item_codes_from_step(step: BomStep) -> set[str]:
    """SAP codes that appear as negative inputs (diecut co-products) on ``step``."""
    out: set[str] = set()
    for pi in step.inputs.order_by(BomStepInput.id).all():
        c = (pi.sap_item_code or '').strip().upper()
        if not c:
            continue
        try:
            qf = float(pi.qty_per_job) if pi.qty_per_job is not None else None
        except (TypeError, ValueError):
            qf = None
        if qf is not None and qf < 0:
            out.add(c)
    return out


def _is_previous_step_linkage_input(bom: Bom, step: BomStep, inp: BomStepInput) -> bool:
    """True when this input consumes the previous step's main output or a diecut co-product."""
    prev = _bom_step_immediate_predecessor(bom, step)
    if not prev:
        return False
    prev_out = (prev.output_item_code or '').strip().upper()
    code_u = (inp.sap_item_code or '').strip().upper()
    if not code_u:
        return False
    if prev_out and code_u == prev_out:
        return True
    return code_u in _coproduct_item_codes_from_step(prev)


def _sap_drop_empty(d: dict) -> dict:
    """Remove empty optional SAP fields while preserving zero values."""
    return {k: v for k, v in d.items() if v not in (None, '')}


def _sap_build_full_patch_body_from_po_params(params: dict) -> dict:
    """PATCH body that replaces ``ProductionOrderLines`` from ``_bom_step_special_po_params``."""
    item_no = params['item_no']
    warehouse = params['warehouse']
    step_qty = params['step_qty']
    remarks = params['remarks']
    input_lines = params['input_lines'] or []
    body: dict = {
        'ItemNo': item_no,
        'PlannedQuantity': step_qty,
        'Warehouse': warehouse,
        'Remarks': (remarks or '')[:254],
        'ProductionOrderLines': [
            _sap_drop_empty({
                'ItemNo': ln['ItemNo'],
                'PlannedQuantity': float(ln.get('PlannedQuantity', 0)),
                'Warehouse': ln.get('Warehouse') or warehouse,
                'ProductionOrderIssueType': ln.get('ProductionOrderIssueType', 'im_Manual'),
                'ItemName': ((ln.get('ItemName') or ln.get('ItemNo') or '')[:100]),
            })
            for ln in input_lines
        ],
    }
    job_ent = str(params.get('sap_job_ent') or '').strip()
    if job_ent:
        body['U_JobEnt'] = job_ent[:254]
    cat = (params.get('job_category_code') or '').strip()
    if cat:
        body['U_Cat'] = cat[:20]
    pcode = (params.get('process_code') or '').strip()
    if pcode:
        body['U_PCode'] = pcode[:20]
    return body


def _sap_planned_qty_completed_rejected_error(exc: SAPClientError) -> bool:
    msg = str(exc or '').lower()
    return (
        'planned qty should be greater than completed + rejected qty' in msg
        or 'owor.plannedqty' in msg
    )


def _patch_production_order_with_planned_qty_retry(
    sap_client: SAPClient,
    doc_entry: int,
    payload: dict,
    *,
    replace_collections: bool = False,
) -> bool:
    """PATCH a PO; if SAP blocks OWOR.PlannedQty, retry without that header field.

    SAP B1 refuses lowering a production order's header planned quantity below completed
    plus rejected quantity. In that case we still want BOM/component edits to reach SAP,
    while leaving the already-progressed order's header planned quantity unchanged.

    Returns True when PlannedQuantity had to be omitted on retry.
    """
    try:
        sap_client.patch_production_order(
            int(doc_entry),
            payload,
            replace_collections=replace_collections,
        )
        return False
    except SAPClientError as e:
        if 'PlannedQuantity' not in payload or not _sap_planned_qty_completed_rejected_error(e):
            raise
        retry_payload = dict(payload)
        retry_payload.pop('PlannedQuantity', None)
        current_app.logger.warning(
            '[SAP-PATCH-PO] DocEntry=%s: SAP rejected PlannedQuantity=%s because completed/rejected '
            'quantity is higher; retrying without header PlannedQuantity.',
            doc_entry,
            payload.get('PlannedQuantity'),
        )
        sap_client.patch_production_order(
            int(doc_entry),
            retry_payload,
            replace_collections=replace_collections,
        )
        return True


def _push_bom_to_sap(
    job,
    bom,
    sap_client,
    *,
    sap_job_ent: Optional[str] = None,
) -> tuple[int, int, int]:
    """Create SAP special production orders for unlinked steps; PATCH when already linked.

    When ``relink_by_new_id`` maps a new ``BomStep`` to its predecessor (after regen transfer),
    every linked step still receives a **full** PATCH (``ProductionOrderLines`` replaced) so SAP
    always matches the current BOM component lines.

    Returns ``(created_count, patched_count, skipped_patch_count)``. ``skipped_patch_count`` counts
    linked steps where SAP accepted the component/warehouse PATCH only after omitting header
    ``PlannedQuantity`` because completed/rejected quantity already exceeded the new plan.
    """
    today, due = _sap_po_posting_and_due_strings(job)
    override_date = current_app.config.get('SAP_OVERRIDE_POSTING_DATE')
    current_app.logger.info(
        '[SAP-PUSH] PostingDate=%s DueDate=%s (override_date=%s)',
        today,
        due,
        bool(override_date),
    )
    from app.services.mfg_warehouse import default_sap_warehouse

    default_warehouse = default_sap_warehouse()
    created_count = 0
    patched_count = 0
    skipped_patch_count = 0
    steps = list(bom.steps.order_by(BomStep.seq_no).all())
    sap_job_ent = sap_job_ent or _ensure_omjd_doc_entry_for_job(sap_client, job)

    for step in steps:
        params = _bom_step_special_po_params(job, bom, step, default_warehouse)
        params['sap_job_ent'] = sap_job_ent
        item_no = params['item_no']
        warehouse = params['warehouse']
        step_qty = params['step_qty']
        remarks = params['remarks']
        input_lines = params['input_lines']
        current_app.logger.info(
            '[SAP-PUSH] Step seq=%s item_no=%s header_FG=%s linked=%s input_lines=%s',
            step.seq_no,
            item_no,
            params.get('header_fg'),
            bool(step.sap_doc_entry),
            input_lines,
        )

        if step.sap_doc_entry:
            current_app.logger.info(
                '[SAP-PUSH] Step seq=%s DocEntry=%s patch_kind=full',
                step.seq_no,
                step.sap_doc_entry,
            )
            try:
                skipped_planned_qty = _patch_production_order_with_planned_qty_retry(
                    sap_client,
                    int(step.sap_doc_entry),
                    _sap_build_full_patch_body_from_po_params(params),
                    replace_collections=True,
                )
                if skipped_planned_qty:
                    skipped_patch_count += 1
                patched_count += 1
            except SAPClientError as e:
                current_app.logger.warning(
                    '[SAP-PATCH-PO] Step seq=%s DocEntry=%s failed: %s',
                    step.seq_no,
                    step.sap_doc_entry,
                    str(e)[:300],
                )
                raise
            continue

        result = sap_client.create_special_production_order(
            item_no=item_no,
            planned_qty=step_qty,
            posting_date=today,
            due_date=due,
            warehouse=warehouse,
            remarks=remarks,
            lines=input_lines,
            u_job_ent=sap_job_ent,
            u_cat=params.get('job_category_code') or '',
            u_pcode=params.get('process_code') or '',
        )
        step.sap_doc_entry = result.get('abs_entry')
        step.sap_doc_num = result.get('doc_num')
        created_count += 1
    return created_count, patched_count, skipped_patch_count


# ------------------------------------------------------------------ CREATE
@jobs_bp.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'planner', 'operator')
def new_job():
    sap_configured = bool(current_app.config.get('SAP_SERVICE_LAYER_URL'))
    customers = SapCustomerMirror.query.order_by(SapCustomerMirror.card_name).all()

    if request.method == 'POST':
        current_app.logger.info('[CREATE-JOB] POST /jobs/new received')
        customer_id_raw = request.form.get('customer_id', '').strip()
        selected_lines_raw = request.form.get('sap_selected_lines_json', '').strip()
        bom_payload_raw = request.form.get('bom_payload_json', '').strip()
        job_kind = (request.form.get('job_kind', '') or '').strip() or None
        priority = (request.form.get('priority', 'normal') or 'normal').strip()
        delivery_raw = request.form.get('delivery_date') or None
        delivery_date = parse_sap_date(delivery_raw) if delivery_raw else None
        job_type_cat = request.form.get('job_type_cat', 'Mono')
        job_series = (request.form.get('job_series', 'Normal') or 'Normal').strip()
        original_job_no = (request.form.get('original_job_no') or '').strip() or None
        job_remarks = (request.form.get('job_remarks') or '').strip() or None
        sap_job_card_doc_entry = _safe_int(request.form.get('sap_job_card_doc_entry'))
        sap_job_card_doc_num_snap = (request.form.get('sap_job_card_doc_num_snap') or '').strip() or None
        sap_job_card_series_snap = (request.form.get('sap_job_card_series_snap') or '').strip() or None
        sap_job_card_title_snap = (request.form.get('sap_job_card_title_snap') or '').strip() or None
        sap_loaded = bool(sap_job_card_doc_entry or sap_job_card_doc_num_snap or sap_job_card_series_snap)

        if sap_loaded and job_series != 'Rejection':
            job_kind = 'repeat'
            if not original_job_no:
                sap_original_parts = [sap_job_card_series_snap, sap_job_card_doc_num_snap]
                original_job_no = '/'.join(part for part in sap_original_parts if part)

        try:
            selected_lines = json.loads(selected_lines_raw) if selected_lines_raw else []
        except Exception:
            selected_lines = []
        try:
            bom_payload = json.loads(bom_payload_raw) if bom_payload_raw else []
        except Exception:
            bom_payload = []

        # -------------------------- Special rule: Rejection series keeps original SO
        original_job = None
        if job_series == 'Rejection':
            if not original_job_no:
                flash('For Rejection series, Original Job No is required.', 'danger')
                return render_template(
                    'job_cards/form.html',
                    job_card=None,
                    customers=customers,
                    materials=[],
                    process_sequence={'lines': []},
                    sap_configured=sap_configured,
                    sap_selected_lines=[],
                    default_delivery_date='',
                    mjd1_customer_name='',
                )
            original_job = JobMaster.query.filter_by(job_no=original_job_no).first()
            if not original_job:
                flash(f'Original Job {original_job_no} not found.', 'danger')
                return render_template(
                    'job_cards/form.html',
                    job_card=None,
                    customers=customers,
                    materials=[],
                    process_sequence={'lines': []},
                    sap_configured=sap_configured,
                    sap_selected_lines=[],
                    default_delivery_date='',
                    mjd1_customer_name='',
                )
            # Rejection: customer must remain same as original job
            customer_id_raw = (original_job.sap_customer_code or '').strip()
            # Rejection: SO must remain same as original job (even if not "open")
            so_entry = original_job.sap_so_entry
            so_num_snap = original_job.sap_so_number_snap
            # Rejection: if UI didn't provide selected SO lines, rebuild from original lines
            if not isinstance(selected_lines, list) or not selected_lines:
                selected_lines = []
                for hl in original_job.header_lines.all():
                    selected_lines.append({
                        'so_no': so_num_snap,
                        'doc_entry': so_entry,
                        'line_num': None,
                        'fg_code': hl.sap_fg_item_code,
                        'fg_name': hl.fg_display_label,
                        'ups': hl.ups or 1,
                        'quantity': float(hl.dispatch_qty) if hl.dispatch_qty else 0.0,
                        'carton_length_mm': float(hl.length) if hl.length else None,
                        'carton_width_mm': float(hl.width) if hl.width else None,
                        'carton_height_mm': float(hl.height) if hl.height else None,
                    })
        else:
            if not customer_id_raw:
                flash('Please select a customer.', 'danger')
                return render_template(
                    'job_cards/form.html',
                    job_card=None,
                    customers=customers,
                    materials=[],
                    process_sequence={'lines': []},
                    sap_configured=sap_configured,
                    default_delivery_date='',
                    mjd1_customer_name='',
                )
            selected_lines = _drop_replaced_unlinked_selected_lines(selected_lines)
            if not isinstance(selected_lines, list) or not selected_lines:
                flash('Please select at least one open SO/FG line.', 'danger')
                return render_template(
                    'job_cards/form.html',
                    job_card=None,
                    customers=customers,
                    materials=[],
                    process_sequence={'lines': []},
                    sap_configured=sap_configured,
                    sap_selected_lines=[],
                    default_delivery_date='',
                    mjd1_customer_name='',
                )
            for row in selected_lines:
                de = row.get('doc_entry')
                try:
                    de = int(de) if de not in (None, '') else None
                except (TypeError, ValueError):
                    de = None
                if de is None:
                    flash(
                        'Each FG line must be linked to an open Sales Order. '
                        'Use Browse and select the matching open SO line for each FG.',
                        'danger',
                    )
                    return render_template(
                        'job_cards/form.html',
                        job_card=None,
                        customers=customers,
                        materials=[],
                        process_sequence={'lines': []},
                        sap_configured=sap_configured,
                        sap_selected_lines=[],
                        default_delivery_date='',
                        mjd1_customer_name='',
                    )

        customer = SapCustomerMirror.query.get(customer_id_raw) if customer_id_raw else None
        if not customer and job_series != 'Rejection':
            flash('Selected customer not found in mirror.', 'danger')
            return redirect(url_for('jobs.new_job'))

        card_code = customer.card_code if customer else (customer_id_raw or None)
        customer_name = (customer.card_name if customer else None) or (original_job.sap_customer_name_snap if original_job else None) or card_code

        # For non-rejection jobs, SO is taken from the chosen open SO line.
        if job_series != 'Rejection':
            first = selected_lines[0]
            so_entry = first.get('doc_entry')
            try:
                so_entry = int(so_entry) if so_entry not in (None, '') else None
            except (TypeError, ValueError):
                so_entry = None
            so_num_snap = str(first.get('so_no') or '').strip() or None

            so_guard_err = validate_so_numbers_for_new_job(
                (row.get('so_no') for row in selected_lines),
                job_series=job_series,
            )
            if so_guard_err:
                flash(so_guard_err, 'danger')
                return render_template(
                    'job_cards/form.html',
                    job_card=None,
                    customers=customers,
                    materials=[],
                    process_sequence={'lines': []},
                    sap_configured=sap_configured,
                    sap_selected_lines=selected_lines if isinstance(selected_lines, list) else [],
                    default_delivery_date='',
                    mjd1_customer_name='',
                )

        # Each detail line must list at least one header FG (multi-FG BOM / SAP linkage).
        # IMPORTANT: Detail lines can be more than the number of selected FG header lines.
        detail_row_indices = _material_detail_indices_from_form()
        if job_series != 'Rejection' and not delivery_date:
            flash(
                'Delivery date is required. Select open sales order / FG lines first '
                '(due date comes from the SO).',
                'danger',
            )
            return render_template(
                'job_cards/form.html',
                job_card=None,
                customers=customers,
                materials=[],
                process_sequence={'lines': []},
                sap_configured=sap_configured,
                sap_selected_lines=selected_lines if isinstance(selected_lines, list) else [],
                default_delivery_date='',
                mjd1_customer_name='',
            )
        seq_bom_err = _validate_process_sequence_and_bom(
            detail_row_indices,
            request.form.get('process_sequence_json', ''),
            bom_payload if isinstance(bom_payload, list) else [],
        )
        if seq_bom_err:
            flash(seq_bom_err, 'danger')
            return render_template(
                'job_cards/form.html',
                job_card=None,
                customers=customers,
                materials=[],
                process_sequence={'lines': []},
                sap_configured=sap_configured,
                sap_selected_lines=selected_lines if isinstance(selected_lines, list) else [],
                default_delivery_date='',
                mjd1_customer_name='',
            )
        for _vidx in detail_row_indices:
            _hdr_idxs: list[int] = []
            for _xv in request.form.getlist(f'detail_fg_involved_{_vidx}[]'):
                try:
                    _hdr_idxs.append(int(_xv))
                except (TypeError, ValueError):
                    continue
            if not _hdr_idxs:
                flash(
                    'Each detail line must have at least one FG selected under '
                    '"FG involved (header)".',
                    'danger',
                )
                return render_template(
                    'job_cards/form.html',
                    job_card=None,
                    customers=customers,
                    materials=[],
                    process_sequence={'lines': []},
                    sap_configured=sap_configured,
                    sap_selected_lines=selected_lines if isinstance(selected_lines, list) else [],
                    default_delivery_date='',
                    mjd1_customer_name='',
                )

        try:
            job = create_job(
                customer_code=card_code,
                customer_name=customer_name or card_code,
                created_by=current_user.id,
                so_entry=so_entry,
                so_number=so_num_snap or None,
                priority=priority if priority in ('low', 'normal', 'urgent') else 'normal',
                delivery_date=delivery_date,
                remarks=job_remarks,
                assigned_planner_id=None,
                job_type_cat=job_type_cat,
                job_series=job_series,
                original_job_no=original_job_no,
                sap_job_card_doc_entry=sap_job_card_doc_entry,
                sap_job_card_doc_num_snap=sap_job_card_doc_num_snap,
                sap_job_card_series_snap=sap_job_card_series_snap,
                sap_job_card_title_snap=sap_job_card_title_snap,
            )

            # Create one header line per selected SO/FG line.
            created_header_lines = []
            created_detail_links: list[tuple[int, JobDetailLine]] = []
            fg_involved_by_detail_idx: dict[int, list[int]] = {}
            # IMPORTANT:
            # - Always create one HEADER line per selected SO/FG line.
            # - Create DETAIL lines only for material rows the user actually filled.
            #   This allows multiple FG lines to share a single detail spec.
            for idx in range(len(selected_lines)):
                row = selected_lines[idx] if idx < len(selected_lines) else {}
                fg_code = str(row.get('fg_code') or '').strip()
                from app.services.unit1_item_naming import resolve_fg_name_for_snap

                fg_name = resolve_fg_name_for_snap(fg_code, str(row.get('fg_name') or ''))
                qty = row.get('quantity')
                try:
                    dispatch_qty = float(qty) if qty not in (None, '') else 0.0
                except (TypeError, ValueError):
                    dispatch_qty = 0.0

                has_detail = idx in detail_row_indices

                if not has_detail:
                    line = add_header_line_only(
                        job=job,
                        fg_item_code=fg_code,
                        fg_item_name=fg_name,
                        dispatch_qty=dispatch_qty,
                        uom=_unit1_default_uom(),
                        ups=row.get('ups') or 1,
                        job_type=job_kind or 'new',
                        length=row.get('carton_length_mm'),
                        width=row.get('carton_width_mm'),
                        height=row.get('carton_height_mm'),
                    )
                else:
                    element_name = (
                        request.form.get(f'mat_name_{idx}', '').strip() or 'Film'
                    )
                    line, detail = add_header_line(
                        job=job,
                        fg_item_code=fg_code,
                        fg_item_name=fg_name,
                        dispatch_qty=dispatch_qty,
                        uom=_unit1_default_uom(),
                        ups=row.get('ups') or 1,
                        job_type=job_kind or 'new',
                        length=row.get('carton_length_mm'),
                        width=row.get('carton_width_mm'),
                        height=row.get('carton_height_mm'),
                        # Detail fields
                        element_name=element_name,
                        detail_yield_loss_pct=_yield_loss_pct_from_form(idx),
                        raw_material_item_code=(request.form.get(f'mat_raw_material_{idx}', '').split('—')[0].strip() or None),
                        paper_brand=request.form.get(f'mat_paper_brand_{idx}', '').strip() or None,
                        mill=request.form.get(f'mat_mill_{idx}', '').strip() or None,
                        total_sheets=request.form.get(f'mat_total_sheets_{idx}') or None,
                        paper_supplied_by=_normalize_paper_supplied_by(
                            request.form.get(f'mat_paper_supplied_{idx}', None)
                        ),
                        wastage_pct=request.form.get(f'mat_wastage_pct_{idx}') or 0,
                        wastage_sheets=request.form.get(f'mat_wastage_sheets_{idx}') or None,
                        sheet_length=request.form.get(f'mat_length_{idx}') or None,
                        sheet_width=request.form.get(f'mat_width_{idx}') or None,
                        gsm=request.form.get(f'mat_gsm_{idx}') or None,
                        print_style=request.form.get(f'mat_print_style_{idx}', '').strip() or None,
                        print_type=request.form.get(f'mat_print_type_{idx}', '').strip() or None,
                        front_colours=request.form.get(f'mat_front_colours_{idx}', '').strip() or None,
                        back_colours=request.form.get(f'mat_back_colours_{idx}', '').strip() or None,
                        die_no=request.form.get(f'mat_die_no_{idx}', '').strip() or None,
                        pasting_style=request.form.get(f'mat_pasting_style_{idx}', '').strip() or None,
                        special_instructions=request.form.get(f'mat_detail_instr_{idx}', '').strip() or None,
                    )
                    created_detail_links.append((idx, detail))
                    raw_fg = request.form.getlist(f'detail_fg_involved_{idx}[]')
                    header_indices: list[int] = []
                    for x in raw_fg:
                        try:
                            header_indices.append(int(x))
                        except (TypeError, ValueError):
                            continue
                    fg_involved_by_detail_idx[idx] = header_indices
                created_header_lines.append(line)

            # Create extra detail lines beyond selected FG header rows (e.g. multiple elements for a single FG).
            # These rows use the same header FG pool via FG-involved mapping.
            existing_detail_nos = [d.detail_no for _i, d in created_detail_links if getattr(d, 'detail_no', None)]
            next_detail_no = (max(existing_detail_nos) + 1) if existing_detail_nos else 1
            for idx in detail_row_indices:
                if idx < len(selected_lines):
                    continue
                element_name = (
                    request.form.get(f'mat_name_{idx}', '').strip() or 'Film'
                )
                detail = JobDetailLine(
                    job_id=job.job_no,
                    detail_no=next_detail_no,
                    element_name=element_name,
                )
                next_detail_no += 1
                detail.yield_loss_pct = _yield_loss_pct_from_form(idx)
                detail.raw_material_item_code = (
                    (request.form.get(f'mat_raw_material_{idx}', '').split('—')[0].strip() or None)
                )
                detail.paper_brand = request.form.get(f'mat_paper_brand_{idx}', '').strip() or None
                detail.mill = request.form.get(f'mat_mill_{idx}', '').strip() or None
                detail.total_sheets = request.form.get(f'mat_total_sheets_{idx}') or None
                detail.paper_supplied_by = _normalize_paper_supplied_by(
                    request.form.get(f'mat_paper_supplied_{idx}', None)
                )
                detail.wastage_pct = request.form.get(f'mat_wastage_pct_{idx}') or 0
                detail.wastage_sheets = request.form.get(f'mat_wastage_sheets_{idx}') or None
                detail.sheet_length = request.form.get(f'mat_length_{idx}') or None
                detail.sheet_width = request.form.get(f'mat_width_{idx}') or None
                detail.gsm = request.form.get(f'mat_gsm_{idx}') or None
                detail.print_style = request.form.get(f'mat_print_style_{idx}', '').strip() or None
                detail.print_type = request.form.get(f'mat_print_type_{idx}', '').strip() or None
                detail.front_colours = request.form.get(f'mat_front_colours_{idx}', '').strip() or None
                detail.back_colours = request.form.get(f'mat_back_colours_{idx}', '').strip() or None
                detail.die_no = request.form.get(f'mat_die_no_{idx}', '').strip() or None
                detail.pasting_style = request.form.get(f'mat_pasting_style_{idx}', '').strip() or None
                detail.special_instructions = request.form.get(f'mat_detail_instr_{idx}', '').strip() or None
                db.session.add(detail)
                created_detail_links.append((idx, detail))
                raw_fg = request.form.getlist(f'detail_fg_involved_{idx}[]')
                header_indices: list[int] = []
                for x in raw_fg:
                    try:
                        header_indices.append(int(x))
                    except (TypeError, ValueError):
                        continue
                fg_involved_by_detail_idx[idx] = header_indices

            # Persist FG-involved mappings after ALL header lines exist (need header_line_id)
            db.session.flush()
            for d_idx, detail in created_detail_links:
                header_indices = fg_involved_by_detail_idx.get(d_idx) or []
                if not header_indices:
                    continue
                sync_detail_line_fg_involved(
                    detail_line=detail,
                    job_master=job,
                    selected_header_indices=header_indices,
                    selected_lines=selected_lines,
                    created_header_lines=created_header_lines,
                )


            # Persist BOM payload (if user built BOM in form) to bom tables.
            current_app.logger.info(f"BOM payload type={type(bom_payload).__name__}, len={len(bom_payload) if isinstance(bom_payload, list) else 'N/A'}, raw={str(bom_payload)[:500]}")
            sap_client = None
            if isinstance(bom_payload, list):
                planner_by_idx = planner_line_sequences_from_form(
                    request.form.get('process_sequence_json', '')
                )
                # Map material-row index -> created detail line (only rows user filled create details)
                detail_by_idx = {i: d for i, d in created_detail_links}
                sap_item_group = current_app.config.get('SAP_BOM_PROCESS_ITEM_GROUP_CODE', 115)
                sap_item_uom = current_app.config.get('UNIT1_DEFAULT_UOM') or current_app.config.get('SAP_BOM_PROCESS_ITEM_UOM', 'KGS')
                if sap_configured:
                    from app.services.sap_job_client import SAPClient
                    sap_client = SAPClient()
                for block in bom_payload:
                    if not isinstance(block, dict):
                        current_app.logger.warning(f"BOM block skipped: not a dict, got {type(block).__name__}")
                        continue
                    line_idx = block.get('line_index')
                    try:
                        line_idx_i = int(line_idx)
                    except (TypeError, ValueError):
                        current_app.logger.warning(f"BOM block skipped: bad line_index={line_idx!r}")
                        continue
                    if line_idx_i < 0:
                        current_app.logger.warning(f"BOM block skipped: line_index={line_idx_i} out of range (<0)")
                        continue
                    hdr = created_header_lines[0] if created_header_lines else None
                    sections = block.get('sections') or []
                    if not isinstance(sections, list) or not sections:
                        current_app.logger.warning(f"BOM block skipped: sections empty or not list for line_index={line_idx_i}")
                        continue
                    sections = ensure_final_fg_section(sections)
                    # Link BOM to the detail line for this material row index.
                    detail_line = detail_by_idx.get(line_idx_i)
                    if not detail_line:
                        current_app.logger.warning(f"BOM block skipped: no detail line for line_index={line_idx_i}")
                        continue
                    
                    bom = create_bom(detail_line, user_id=current_user.id)
                    seq = 10
                    # Track previous output codes per card_idx for propagation fallback
                    prev_outputs_by_card: dict[int, str] = {}
                    last_step_by_card: dict[int, Any] = {}
                    pending_outsource_wh_by_card: dict[int, str] = {}

                    for sec in sections:
                        if not isinstance(sec, dict):
                            continue
                        process_name = str(sec.get('process_name') or '').strip()
                        process_code = _resolve_process_code(
                            process_name,
                            hinted_code=str(sec.get('process_code') or '').strip(),
                        )
                        if not process_code:
                            continue
                        is_fg_step = _is_fg_packaging_process_code(process_code)

                        # Backend-authoritative outsourcing rule:
                        # If process_master.category == 'outsourcing', do NOT create a BOM step.
                        # Instead:
                        # - set the previous real step's header warehouse to process default_workcenter (fallback II-CORU)
                        # - force the next real step's *required-line* warehouse to SAP_OUTSOURCE_LINK_WAREHOUSE
                        pm = ProcessMaster.query.filter_by(process_code=process_code).first()
                        if pm and (pm.category or '').strip().lower() == 'outsourcing':
                            out_wh = (pm.default_workcenter or '').strip() or 'II-CORU'
                            for x in request.form.getlist(f'detail_fg_involved_{line_idx_i}[]'):
                                try:
                                    hi = int(x)
                                except (TypeError, ValueError):
                                    continue
                                # update previous step header WH for that FG card (if present)
                                prev_step = last_step_by_card.get(hi)
                                if prev_step is not None:
                                    prev_step.sap_warehouse = out_wh
                                pending_outsource_wh_by_card[hi] = out_wh
                            # Also handle single-FG jobs without explicit selection
                            if not request.form.getlist(f'detail_fg_involved_{line_idx_i}[]'):
                                # Apply to all cards we have seen so far
                                for hi in list(last_step_by_card.keys()):
                                    last_step_by_card[hi].sap_warehouse = out_wh
                                    pending_outsource_wh_by_card[hi] = out_wh
                            continue
                        
                        cards = sec.get('cards') or []
                        # Only consider selected header FGs for this detail line (if set).
                        allowed_hdr_idxs: list[int] = []
                        for x in request.form.getlist(f'detail_fg_involved_{line_idx_i}[]'):
                            try:
                                allowed_hdr_idxs.append(int(x))
                            except (TypeError, ValueError):
                                continue
                        if is_fg_step and not cards:
                            if allowed_hdr_idxs:
                                cards = [{'card_idx': hi} for hi in allowed_hdr_idxs]
                            else:
                                cards = [{'card_idx': 0}]
                        from app.services.mfg_warehouse import warehouse_for_process_code

                        fg_step_wh = warehouse_for_process_code('FG')
                        for c_idx, card in enumerate(cards):
                            if not isinstance(card, dict):
                                continue
                            
                            # Determine which header line this card belongs to
                            payload_card_idx = card.get('card_idx', 0)
                            try:
                                payload_card_idx = int(payload_card_idx) if payload_card_idx is not None else 0
                            except (TypeError, ValueError):
                                payload_card_idx = 0
                            if allowed_hdr_idxs and payload_card_idx not in allowed_hdr_idxs:
                                continue
                            # Fallback to current header if out of bounds or not provided
                            card_hdr = hdr
                            if 0 <= payload_card_idx < len(created_header_lines):
                                card_hdr = created_header_lines[payload_card_idx]

                            # Each card in a section gets its own BomStep (for separate POs)
                            step = add_step(
                                bom=bom,
                                process_code=process_code,
                                step_name=process_name or process_code,
                                seq_no=seq + c_idx, # Ensure unique seq_no if multiple cards
                            )
                            card_wh = (str(card.get('warehouse') or '').strip() or None)
                            if is_fg_step and (not card_wh or card_wh.upper() == 'II-RM'):
                                card_wh = fg_step_wh
                            step.sap_warehouse = card_wh
                            step.uom = card.get('uom')
                            prev_st_fg = last_step_by_card.get(payload_card_idx)
                            if prev_st_fg is None and len(last_step_by_card) == 1:
                                prev_st_fg = next(iter(last_step_by_card.values()))
                            if is_fg_step:
                                try:
                                    step.planned_qty = float(
                                        fg_planned_qty_for_bom_step(
                                            job,
                                            detail_line,
                                            card_hdr,
                                            prev_step=prev_st_fg,
                                            card_planned_qty=card.get('planned_qty'),
                                        )
                                    )
                                except Exception:
                                    step.planned_qty = float(card_hdr.dispatch_qty or 1) if card_hdr else 1.0
                            else:
                                try:
                                    step_planned = card.get('planned_qty')
                                    step.planned_qty = (
                                        float(step_planned) if step_planned not in (None, '') else None
                                    )
                                except (TypeError, ValueError):
                                    step.planned_qty = None

                            if is_fg_step:
                                # FG step must use the existing FG item code (do not create new item codes).
                                output_item_code = (card_hdr.sap_fg_item_code or '').strip()
                            else:
                                # For Item Code: FG Num - element(3 char) - process code
                                output_item_code = _process_item_code(
                                    fg_code=(card_hdr.sap_fg_item_code or ''),
                                    element_name=(detail_line.element_name if detail_line else ''),
                                    process_code=process_code,
                                )
                            step.output_item_code = output_item_code
                            po_rm = str(
                                card.get('production_order_remarks')
                                or card.get('sap_po_remarks')
                                or ''
                            ).strip()
                            step.production_order_remarks = po_rm[:254] if po_rm else None

                            fg_full_name = card_hdr.fg_display_label
                            if is_fg_step:
                                output_item_name = fg_full_name[:100]
                            elif output_item_code:
                                from app.services.unit1_item_naming import unit1_process_item_description

                                output_item_name = unit1_process_item_description(output_item_code)[:100]
                            else:
                                proc_full_name = (process_name or process_code or 'PROC').strip()
                                output_item_name = f'{fg_full_name}-{proc_full_name}'[:100]

                            if sap_client and (not is_fg_step) and output_item_code:
                                sap_client.ensure_item_exists(
                                    output_item_code,
                                    output_item_name,
                                    base_fg_code=(card_hdr.sap_fg_item_code or ''),
                                    item_group_code=int(sap_item_group),
                                    sales_uom=step.uom or sap_item_uom,
                                )

                            inputs_added = False
                            for req in (card.get('required_items') or []):
                                if not isinstance(req, dict):
                                    continue
                                raw_item = str(req.get('sap_item_code') or '').strip()
                                # Form autocomplete uses "CODE — Name" (Unicode em dash U+2014).
                                # Preserve the SAP-stored case of the ItemCode — SAP's Service Layer
                                # key lookup and ProductionOrderLines.ItemNo validation are case-sensitive.
                                if '\u2014' in raw_item:
                                    _code, _name = raw_item.split('\u2014', 1)
                                    sap_item_code = _code.strip()
                                    name_from_ui = _name.strip()
                                else:
                                    sap_item_code = raw_item
                                    name_from_ui = ''
                                if not sap_item_code:
                                    # Fallback to previous output for this specific card
                                    sap_item_code = prev_outputs_by_card.get(payload_card_idx, '')

                                resolved_item_name = (name_from_ui[:200] if name_from_ui else '').strip()
                                if not resolved_item_name:
                                    resolved_item_name = _synthetic_display_name_for_process_item_code(
                                        job, detail_line, sap_item_code
                                    )
                                if not resolved_item_name:
                                    resolved_item_name = sap_item_code

                                hl_for_input = _header_line_for_bom_step(job, sap_item_code)
                                base_fg_for_item = (
                                    (hl_for_input.sap_fg_item_code or '').strip()
                                    if hl_for_input
                                    else ''
                                ) or (card_hdr.sap_fg_item_code or '').strip()

                                # Auto-verify/create component in SAP (never use code-only as ItemName for intermediates).
                                if sap_client and sap_item_code:
                                    try:
                                        sap_client.ensure_item_exists(
                                            sap_item_code,
                                            resolved_item_name,
                                            base_fg_code=base_fg_for_item or None,
                                            item_group_code=100,
                                            sales_uom=req.get('uom', _unit1_default_uom()),
                                        )
                                    except Exception as e:
                                        current_app.logger.warning(
                                            f"Could not verify component {sap_item_code} in SAP: {e}"
                                        )

                                qty_val = req.get('qty_per_job')
                                try:
                                    qty_per_job = float(qty_val) if qty_val not in (None, '') else None
                                except (TypeError, ValueError):
                                    qty_per_job = None
                                if not sap_item_code or qty_per_job is None:
                                    continue
                                prev_out_code = (prev_outputs_by_card.get(payload_card_idx) or '').strip().upper()
                                is_prev_out_line = bool(prev_out_code) and sap_item_code.strip().upper() == prev_out_code
                                force_ohjw = (
                                    payload_card_idx in pending_outsource_wh_by_card
                                    and is_prev_out_line
                                )
                                desc = resolved_item_name[:200]
                                add_input(
                                    step=step,
                                    input_type='raw_material',
                                    sap_item_code=sap_item_code,
                                    description=desc,
                                    uom=req.get('uom', _unit1_default_uom()),
                                    qty_per_job=qty_per_job,
                                    sap_warehouse=(
                                        SAP_OUTSOURCE_LINK_WAREHOUSE if force_ohjw else req.get('warehouse')
                                    ),
                                )
                                inputs_added = True
                            if is_fg_step and not inputs_added:
                                prev_st = last_step_by_card.get(payload_card_idx)
                                if prev_st is None and len(last_step_by_card) == 1:
                                    prev_st = next(iter(last_step_by_card.values()))
                                prev_code = (prev_st.output_item_code or '').strip() if prev_st else ''
                                if not prev_code:
                                    prev_code = (prev_outputs_by_card.get(payload_card_idx) or '').strip()
                                if prev_code:
                                    try:
                                        link_qty = float(prev_st.planned_qty) if prev_st and prev_st.planned_qty is not None else float(step.planned_qty or 1)
                                    except (TypeError, ValueError):
                                        link_qty = float(step.planned_qty or 1)
                                    link_wh = (
                                        (str(prev_st.sap_warehouse or '').strip()[:20] if prev_st else '')
                                        or fg_step_wh
                                    )
                                    add_input(
                                        step=step,
                                        input_type='raw_material',
                                        sap_item_code=prev_code[:50],
                                        description=_synthetic_display_name_for_process_item_code(
                                            job, detail_line, prev_code
                                        )[:200]
                                        or f'Output of {prev_st.step_name if prev_st else "prior step"}',
                                        uom=(prev_st.uom if prev_st else None) or step.uom or _unit1_default_uom(),
                                        qty_per_job=link_qty,
                                        sap_warehouse=link_wh,
                                    )
                            # Record output code for next section's fallback
                            prev_outputs_by_card[payload_card_idx] = output_item_code
                            last_step_by_card[payload_card_idx] = step
                            # Consume outsourcing modifier after the immediate next real process
                            if payload_card_idx in pending_outsource_wh_by_card:
                                pending_outsource_wh_by_card.pop(payload_card_idx, None)
                        
                        seq += 10 # Move to next process step block

                    bom.slip_process_sequence_json = slip_process_sequence_json_from_planner_and_sections(
                        planner_by_idx.get(line_idx_i),
                        sections,
                        resolve_process_code=_resolve_process_code,
                    )

                    # New: Immediately push Special Production Orders to SAP
                    if sap_client:
                        try:
                            _, _, _ = _push_bom_to_sap(job, bom, sap_client)
                        except Exception as e:
                            current_app.logger.exception("[SAP-PUSH] Auto-push failed during job creation: %s", e)
                            flash(
                                f'Job created, but SAP Production Order auto-push failed: {str(e)[:220]}',
                                'warning',
                            )
                        
                if sap_client:
                    try:
                        sap_client.logout()
                    except Exception:
                        pass

            db.session.commit()
            fg_sync_warnings: list[str] = []
            if sap_configured and job_series != 'Rejection':
                try:
                    fg_sync_warnings = _sync_selected_fg_job_refs_to_sap(card_code, job.job_no, selected_lines)
                except Exception as e:
                    current_app.logger.exception(
                        '[SAP-SO-SYNC] Unexpected FG-to-job sync failure for job %s: %s',
                        job.job_no,
                        e,
                    )
                    fg_sync_warnings = [f'FG-to-job sync failed: {str(e)[:180]}']
                try:
                    qty_warnings = _sync_selected_so_quantities_to_sap(selected_lines)
                    fg_sync_warnings = (fg_sync_warnings or []) + (qty_warnings or [])
                except Exception as e:
                    current_app.logger.exception(
                        '[SAP-SO-SYNC] SO quantity sync failed for job %s: %s',
                        job.job_no,
                        e,
                    )
                    fg_sync_warnings = (fg_sync_warnings or []) + [
                        f'SO quantity sync failed: {str(e)[:180]}'
                    ]
                try:
                    dim_warnings = _sync_selected_so_dimensions_to_sap(selected_lines)
                    fg_sync_warnings = (fg_sync_warnings or []) + (dim_warnings or [])
                except Exception as e:
                    current_app.logger.exception(
                        '[SAP-SO-SYNC] SO width sync failed for job %s: %s',
                        job.job_no,
                        e,
                    )
                    fg_sync_warnings = (fg_sync_warnings or []) + [
                        f'SO width sync failed: {str(e)[:180]}'
                    ]

            if fg_sync_warnings:
                flash(
                    'Job created, but some open SO FG lines could not be updated: '
                    + '; '.join(fg_sync_warnings[:3]),
                    'warning',
                )
            if sap_configured:
                try:
                    sap = SAPClient()
                    try:
                        omjd_result = upsert_omjd_job_card(sap, job)
                    finally:
                        sap.logout()
                    flash(
                        f"OMJD SAP {omjd_result.get('action', 'updated')} "
                        f"for {job.job_no}.",
                        'success',
                    )
                except SAPClientError as e:
                    current_app.logger.warning(
                        '[SAP-OMJD] Upsert failed after job creation job=%s: %s',
                        job.job_no,
                        e,
                    )
                    flash(
                        'Job created, but OMJD SAP update failed: '
                        f'{str(e)[:220]}. Use "Update OMJD SAP" from the job view to retry.',
                        'warning',
                    )
                except Exception as e:
                    current_app.logger.exception(
                        '[SAP-OMJD] Unexpected upsert failure after job creation job=%s',
                        job.job_no,
                    )
                    flash(
                        'Job created, but OMJD SAP update failed: '
                        f'{str(e)[:220]}. Use "Update OMJD SAP" from the job view to retry.',
                        'warning',
                    )
            if sap_client:
                flash(f'✅ Successfully created Job {job.job_no}, generated BOMs, and auto-created all SAP Item Codes!', 'success')
            else:
                flash(f'✅ Job {job.job_no} created successfully.', 'success')
            return redirect(url_for('jobs.view_job', job_id=job.id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception(f"Error creating job: {str(e)}")
            flash(f'Error creating job: {str(e)}', 'danger')

    return render_template(
        'job_cards/form.html',
        job_card=None,
        customers=customers,
        materials=[],
        process_sequence={'lines': []},
        sap_configured=sap_configured,
        sap_selected_lines=[],
        default_delivery_date='',
        mjd1_customer_name='',
    )


# ------------------------------------------------------------------ VIEW
@jobs_bp.route('/<int:job_id>')
@login_required
def view_job(job_id):
    job = JobMaster.query.get_or_404(job_id)
    history = job.status_history.order_by(None).order_by(
        db.text('changed_at DESC')
    ).limit(20).all()
    bom_input_names = _bom_input_display_names_by_id(job)
    cancel_block = session.pop('job_cancel_block', None)
    omjd_doc_entry = None
    if current_app.config.get('SAP_SERVICE_LAYER_URL'):
        try:
            sap = SAPClient()
            try:
                row = find_omjd_by_ver_entry(sap, job.job_no)
                if row:
                    omjd_doc_entry = row.get('DocEntry')
            finally:
                sap.logout()
        except Exception as e:
            current_app.logger.warning(
                '[SAP-OMJD] Could not resolve DocEntry for job=%s: %s',
                job.job_no,
                str(e)[:200],
            )
    old_sap_job_no_display = '/'.join(
        part
        for part in (
            (job.sap_job_card_series_snap or '').strip(),
            (job.sap_job_card_doc_num_snap or '').strip(),
        )
        if part
    ) or '-'
    old_in_app_job_no_display = '-'
    if job.original_job_no:
        original_exists_locally = JobMaster.query.filter(
            JobMaster.job_no == job.original_job_no,
            JobMaster.id != job.id,
        ).first()
        if original_exists_locally:
            old_in_app_job_no_display = job.original_job_no
    old_job_no_display = (
        old_in_app_job_no_display
        if old_in_app_job_no_display != '-'
        else old_sap_job_no_display
    )
    from app.services.job_service import ALLOWED_TRANSITIONS

    status_choices = [
        (code, code.replace('_', ' ').title())
        for code in ALLOWED_TRANSITIONS.get(job.overall_status, [])
    ]
    return render_template(
        'jobs/view.html',
        job=job,
        history=history,
        bom_input_names=bom_input_names,
        job_cancel_block=cancel_block if cancel_block and cancel_block.get('job_id') == job.id else None,
        omjd_doc_entry=omjd_doc_entry,
        old_job_no_display=old_job_no_display,
        status_choices=status_choices,
    )


@jobs_bp.route('/<int:job_id>/upload-pdf', methods=['POST'])
@login_required
@role_required('admin', 'planner')
def upload_job_pdf(job_id):
    job = JobMaster.query.get_or_404(job_id)
    if not job.is_editable:
        flash('This job can only accept PDF uploads before it is released.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    line = None
    step = None
    step_id = request.form.get('step_id', type=int)
    if step_id:
        step = BomStep.query.join(Bom).join(
            JobDetailLine,
            Bom.detail_line_id == JobDetailLine.id,
        ).filter(
            BomStep.id == step_id,
            JobDetailLine.job_id == job.job_no,
        ).first()
        if step is None:
            flash('Selected process does not belong to this job.', 'danger')
            return redirect(url_for('jobs.view_job', job_id=job.id))
        if (step.process_code or '').strip().upper() != 'PRI':
            flash('PDF upload is only available for PRI production orders.', 'warning')
            return redirect(url_for('jobs.view_job', job_id=job.id))
        if not step.sap_doc_num:
            flash('Create/update SAP production orders before uploading a PDF for this process.', 'warning')
            return redirect(url_for('jobs.view_job', job_id=job.id))
        line = _header_line_for_bom_step(job, step.output_item_code)

    line_id = request.form.get('line_id', type=int)
    if line_id and step is None:
        line = JobHeaderLine.query.filter_by(id=line_id, job_id=job.job_no).first()
        if line is None:
            flash('Selected component does not belong to this job.', 'danger')
            return redirect(url_for('jobs.view_job', job_id=job.id))

    uploaded_pdf = request.files.get('job_pdf')
    if not uploaded_pdf or not uploaded_pdf.filename:
        flash('Please choose a PDF file to upload.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    if not uploaded_pdf.filename.lower().endswith('.pdf'):
        flash('Only PDF files can be uploaded.', 'danger')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    try:
        configured_upload_dir = current_app.config['JOB_PDF_UPLOAD_DIR']
        upload_dir = _resolve_job_pdf_upload_dir(configured_upload_dir)
        _ensure_job_pdf_share_access(upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        package_stem = _job_process_upload_stem(job, step) if step else _job_upload_stem(job)
        pdf_filename = _safe_pdf_zip_member_name(uploaded_pdf.filename, package_stem)
        pdf_zip_path = f'asset/{pdf_filename}'
        ptk_filename = f'{package_stem}.ptk'
        zip_path = upload_dir / f'{package_stem}.zip'
        pdf_bytes = uploaded_pdf.read()
        ptk_bytes = _build_printtalk_ptk(
            job,
            line,
            pdf_zip_path,
            job_id=str(step.sap_doc_num) if step else None,
            descriptive_name=_job_display_name(job, line),
        )
        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(ptk_filename, ptk_bytes)
            zf.writestr(pdf_zip_path, pdf_bytes)
        current_app.logger.info(
            'Job ZIP package created for job=%s configured_dir=%s resolved_path=%s',
            job.job_no,
            configured_upload_dir,
            zip_path,
        )
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as e:
        current_app.logger.exception('Job ZIP package creation failed for job=%s', job.job_no)
        flash(f'Could not create ZIP package: {e}', 'danger')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    flash(f'ZIP package created: {zip_path}', 'success')
    return redirect(url_for('jobs.view_job', job_id=job.id))


@jobs_bp.route('/<int:job_id>/sap/omjd-update', methods=['POST'])
@login_required
@role_required('admin', 'planner')
def update_job_omjd_sap(job_id):
    job = JobMaster.query.get_or_404(job_id)
    if not current_app.config.get('SAP_SERVICE_LAYER_URL'):
        flash('SAP Service Layer URL is not configured.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    try:
        sap = SAPClient()
        try:
            result = upsert_omjd_job_card(sap, job)
        finally:
            sap.logout()
        action = result.get('action') or 'updated'
        doc_entry = result.get('doc_entry')
        suffix = f' (DocEntry {doc_entry})' if doc_entry not in (None, '') else ''
        flash(f'OMJD SAP {action} for {job.job_no}{suffix}.', 'success')
    except SAPClientError as e:
        current_app.logger.warning(
            '[SAP-OMJD] Manual upsert failed job=%s: %s',
            job.job_no,
            e,
        )
        flash(f'OMJD SAP update failed: {str(e)[:260]}', 'danger')
    except Exception as e:
        current_app.logger.exception(
            '[SAP-OMJD] Unexpected manual upsert failure job=%s',
            job.job_no,
        )
        flash(f'OMJD SAP update failed: {str(e)[:260]}', 'danger')
    return redirect(url_for('jobs.view_job', job_id=job.id))


# ------------------------------------------------------------------ PRINT (slips)
def _slip_process_sequence_string_for_bom(bom: Bom) -> str:
    """Comma-separated process codes for slips / BOM print (planner order + outsourcing)."""
    return ','.join(bom.slip_process_sequence_codes) if bom else ''


def _fg_thickness_display_from_item_code(fg_code: str) -> str:
    """Thickness in micron from FG code segment, e.g. ``PET-12-1002-TR`` → ``12``."""
    from app.services.unit1_processes import unit1_fg_base_code
    from app.utils.thickness import parse_thickness, thickness_display

    base = unit1_fg_base_code((fg_code or '').strip())
    if not base:
        return '—'
    parts = base.split('-')
    if len(parts) < 2:
        return '—'
    disp = thickness_display(parse_thickness(parts[1]))
    return disp if disp != '—' else '—'


def _slip_detail_rows_ctx(job, detail_lines: list) -> dict[int, dict]:
    """Unit 1 ELEMENTS slip values: width from SO/header, weights in kg, thickness from FG code."""
    from sqlalchemy import func

    ctx: dict[int, dict] = {}
    fallback_headers = list(job.header_lines.all())
    rm_codes: set[str] = set()
    for dl in detail_lines:
        code = (dl.raw_material_item_code or '').strip()
        if code:
            rm_codes.add(code.upper())
    mirror_names: dict[str, str] = {}
    if rm_codes:
        rows = SapItemMirror.query.filter(
            func.upper(SapItemMirror.item_code).in_(list(rm_codes))
        ).all()
        mirror_names = {
            (r.item_code or '').strip().upper(): (r.item_name or '').strip()
            for r in rows
        }

    for dl in detail_lines:
        width_mm = None
        fg_code_for_thickness = None
        net_weight = None
        inv_rows = list(dl.fg_involved.all())
        if inv_rows:
            dispatch_sum = 0.0
            has_dispatch = False
            for inv in inv_rows:
                hl = inv.header_line
                if hl and width_mm is None and hl.width is not None:
                    width_mm = hl.width
                code = (inv.sap_fg_item_code or '').strip()
                if code and not fg_code_for_thickness:
                    fg_code_for_thickness = code
                if hl and hl.dispatch_qty is not None:
                    dispatch_sum += float(hl.dispatch_qty)
                    has_dispatch = True
            if has_dispatch:
                net_weight = dispatch_sum
        elif fallback_headers:
            hl0 = fallback_headers[0]
            if hl0.width is not None:
                width_mm = hl0.width
            fg_code_for_thickness = (hl0.sap_fg_item_code or '').strip() or None
            if hl0.dispatch_qty is not None:
                net_weight = float(hl0.dispatch_qty)

        gross = float(dl.total_sheets) if dl.total_sheets is not None else None
        waste = float(dl.wastage_sheets or 0)
        if net_weight is None and gross is not None:
            net_weight = gross - waste

        rm_code = (dl.raw_material_item_code or '').strip()
        ctx[dl.detail_no] = {
            'raw_item_code': rm_code or None,
            'raw_item_name': mirror_names.get(rm_code.upper(), '') if rm_code else None,
            'width_mm': width_mm,
            'thickness': _fg_thickness_display_from_item_code(fg_code_for_thickness or ''),
            'weight_net': net_weight,
            'weight_wastage': waste,
            'weight_total': gross,
        }
    return ctx


def _sap_order_line_matches_job(line: dict, job_no: str) -> bool:
    """Whether a SAP SO line is explicitly linked to this job via RDR1.U_JEntry."""
    wanted = (job_no or '').strip()
    if not wanted or not isinstance(line, dict):
        return False
    for key in ('U_JEntry', 'u_JEntry', 'U_JENTRY'):
        value = line.get(key)
        if value is not None and str(value).strip() == wanted:
            return True
    return False


@jobs_bp.route('/<int:job_id>/print')
@login_required
def print_job(job_id):
    """Print-friendly slips view for a job (3 slips like physical job card)."""
    job = JobMaster.query.get_or_404(job_id)
    header_lines = job.header_lines.all()
    detail_lines = job.detail_lines.all()

    sap_configured = bool(current_app.config.get('SAP_SERVICE_LAYER_URL'))
    order_header = None
    sales_rep_name = None
    artwork_by_fg: dict[str, str] = {}
    itemcode_by_fg: dict[str, str] = {}
    frgnname_by_fg: dict[str, str] = {}

    if sap_configured and job.sap_so_entry:
        try:
            client = SAPClient()
            try:
                order_header = client.fetch_order_header_for_print(int(job.sap_so_entry))

                # Sales rep: among open ORDR that contain any job FG line, use highest DocEntry's SlpCode;
                # display name uses company mapping; unknown codes fall back to Service Layer.
                fg_codes_u: list[str] = []
                _seen_fg: set[str] = set()
                for h in header_lines:
                    c = (h.sap_fg_item_code or '').strip()
                    if not c:
                        continue
                    key = c.upper()
                    if key in _seen_fg:
                        continue
                    _seen_fg.add(key)
                    fg_codes_u.append(c)
                slp_code = None
                if fg_codes_u:
                    latest_de = client.fetch_latest_doc_entry_for_items(
                        fg_codes_u,
                        open_orders_only=current_app.config.get('SAP_PRINT_SLP_OPEN_ONLY', True),
                        scan_limit=current_app.config.get('SAP_PRINT_SLP_SCAN_LIMIT', 500),
                    )
                    if latest_de is not None:
                        hdr_slp = client.fetch_order_header_for_print(int(latest_de))
                        slp_code = hdr_slp.get('SalesPersonCode')
                if slp_code is None and order_header:
                    slp_code = order_header.get('SalesPersonCode')
                sales_rep_name = resolve_sales_rep_display_name(slp_code)
                if not sales_rep_name:
                    sales_rep_name = client.fetch_salesperson_name(slp_code)

                raw_lines = client.fetch_order_lines_raw(int(job.sap_so_entry))
                raw_lines = sorted(
                    raw_lines,
                    key=lambda ln: 0 if _sap_order_line_matches_job(ln, job.job_no) else 1,
                )
                artwork_fields = [
                    x.strip()
                    for x in (current_app.config.get('SAP_ORDER_LINE_ARTWORK_FIELDS') or '').split(',')
                    if x.strip()
                ]
                for ln in raw_lines:
                    item = (ln.get('ItemCode') or '').strip()
                    if not item:
                        continue
                    if item not in artwork_by_fg:
                        for k in artwork_fields:
                            v = ln.get(k)
                            if v is None:
                                kk = k[0].lower() + k[1:] if len(k) > 1 else k
                                v = ln.get(kk)
                            vv = (str(v).strip() if v is not None else '')
                            if vv:
                                artwork_by_fg[item] = vv
                                break
                    if item not in itemcode_by_fg:
                        try:
                            frgn = client.fetch_item_foreign_name(item)
                        except Exception:
                            frgn = None
                        sub = (
                            ln.get('SupplierCatNum')
                            or ln.get('supplierCatNum')
                            or ln.get('SubCatNum')
                            or ln.get('subCatNum')
                        )
                        sub = str(sub).strip() if sub is not None else ''
                        if not sub:
                            try:
                                sub = client.fetch_oscn_substitute(item) or ''
                            except Exception:
                                sub = ''
                        # "Itemcode" column should show BP catalog no; "Artwork Num" will show FrgnName
                        itemcode_by_fg[item] = sub or ''
                        frgnname_by_fg[item] = frgn or ''
            finally:
                client.logout()
        except SAPClientError:
            order_header = None
            sales_rep_name = None
            artwork_by_fg = {}
            itemcode_by_fg = {}
            frgnname_by_fg = {}
        except Exception:
            order_header = None
            sales_rep_name = None
            artwork_by_fg = {}
            itemcode_by_fg = {}
            frgnname_by_fg = {}

    # Process sequence per detail line for slips: prefer payload-derived order (includes outsourcing).
    process_seq_by_detail_no: dict[int, str] = {}
    for dl in detail_lines:
        bom = dl.active_bom
        if not bom:
            continue
        s = _slip_process_sequence_string_for_bom(bom)
        if s:
            process_seq_by_detail_no[dl.detail_no] = s

    repeat_original_job_no_for_slip = None
    if job.original_job_no and not (
        job.sap_job_card_doc_entry
        or job.sap_job_card_doc_num_snap
        or job.sap_job_card_series_snap
    ):
        original_exists_locally = JobMaster.query.filter(
            JobMaster.job_no == job.original_job_no,
            JobMaster.id != job.id,
        ).first()
        if original_exists_locally:
            repeat_original_job_no_for_slip = job.original_job_no

    slip_detail_by_no = _slip_detail_rows_ctx(job, detail_lines)

    return render_template(
        'jobs/print_slips.html',
        job=job,
        header_lines=header_lines,
        detail_lines=detail_lines,
        order_header=order_header,
        sales_rep_name=sales_rep_name,
        artwork_by_fg=artwork_by_fg,
        itemcode_by_fg=itemcode_by_fg,
        frgnname_by_fg=frgnname_by_fg,
        process_seq_by_detail_no=process_seq_by_detail_no,
        slip_detail_by_no=slip_detail_by_no,
        repeat_original_job_no_for_slip=repeat_original_job_no_for_slip,
        sap_configured=sap_configured,
        print_date=dt.utcnow().strftime('%d/%m/%Y'),
    )


@jobs_bp.route('/<int:job_id>/print/bom')
@login_required
def print_job_bom(job_id):
    """Print-friendly BOM detail sheet (separate page from the three job slips)."""
    job = JobMaster.query.get_or_404(job_id)
    detail_lines = sorted(job.detail_lines.all(), key=lambda d: d.detail_no)
    bom_blocks: list[dict[str, Any]] = []
    for dl in detail_lines:
        bom = dl.active_bom
        seq_display = _slip_process_sequence_string_for_bom(bom) if bom else ''
        steps_payload: list[dict[str, Any]] = []
        if bom:
            for step in bom.steps.order_by(None).order_by(db.text('seq_no ASC')).all():
                steps_payload.append({
                    'step': step,
                    'inputs': list(step.inputs.order_by(BomStepInput.id).all()),
                })
        fg_rows = list(dl.fg_involved.all())
        bom_blocks.append({
            'detail': dl,
            'bom': bom,
            'sequence_display': seq_display,
            'steps': steps_payload,
            'fg_involved': fg_rows,
        })
    return render_template(
        'jobs/print_bom_slip.html',
        job=job,
        bom_blocks=bom_blocks,
        print_date=dt.utcnow().strftime('%d/%m/%Y'),
    )


# ------------------------------------------------------------------ EDIT
@jobs_bp.route('/<int:job_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'planner')
def edit_job(job_id):
    job = JobMaster.query.get_or_404(job_id)

    if not job.is_editable:
        flash('This job can only be edited before it is released.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    header_lines = job.header_lines.order_by(JobHeaderLine.line_no).all()
    detail_lines = job.detail_lines.order_by(JobDetailLine.detail_no).all()

    if request.method == 'POST':
        del_raw = request.form.get('delivery_date', '').strip()
        job.delivery_date = parse_sap_date(del_raw) if del_raw else None
        job.remarks = (request.form.get('job_remarks') or '').strip() or None

        for hl in header_lines:
            hl.width = _decimal_or_none(request.form.get(f'hl_{hl.id}_width'))

        for dl in detail_lines:
            _apply_non_bom_job_detail_fields(dl, f'dl_{dl.id}')

        try:
            db.session.commit()
            flash('Job updated successfully.', 'success')
            return redirect(url_for('jobs.view_job', job_id=job.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving job: {str(e)}', 'danger')

    return render_template(
        'jobs/edit.html',
        job=job,
        header_lines=header_lines,
        detail_lines=detail_lines,
    )


def _apply_edit_bom_detail_sheet_fields(job: JobMaster, detail: JobDetailLine, form) -> Optional[str]:
    """Wastage sheets + gross total for edit-BOM (matches new-job: ceil(max net sheets) + wastage sheets)."""
    ws_raw = (form.get('detail_wastage_sheets') or '').strip()
    ts_hidden = (form.get('detail_total_sheets') or '').strip()
    try:
        if ws_raw != '':
            detail.wastage_sheets = max(0, int(float(ws_raw)))
        else:
            detail.wastage_sheets = 0
    except (TypeError, ValueError):
        return 'Invalid wastage sheets.'
    detail.wastage_pct = None
    if ts_hidden != '':
        try:
            tsv = int(float(ts_hidden))
            detail.total_sheets = tsv if tsv > 0 else None
        except (TypeError, ValueError):
            return 'Invalid gross sheets value.'
    else:
        detail.total_sheets = None
        g = gross_sheet_planned_for_detail(job, detail)
        detail.total_sheets = int(g) if g and g > 0 else None
    return None


def _bom_belongs_to_job(job: JobMaster, bom: Bom) -> bool:
    dl = bom.detail_line
    if not dl or dl.job_id != job.job_no:
        return False
    if bom.job_id and bom.job_id != job.job_no:
        return False
    return True


def _is_fg_packaging_process_code(process_code: str) -> bool:
    return (process_code or '').strip().upper() in ('FG', 'PK-PACK')


def _is_fg_output_item_for_job(job: JobMaster, output_item_code: str) -> bool:
    oc = (output_item_code or '').strip().upper()
    if not oc:
        return False
    for hl in job.header_lines:
        if (hl.sap_fg_item_code or '').strip().upper() == oc:
            return True
    return False


def _sync_bom_step_planned_qty_from_headers(job: JobMaster, bom: Bom) -> None:
    """Refresh FG/PK step planned_qty from the previous process output (not SO dispatch)."""
    detail = bom.detail_line
    steps_list = list(bom.steps.order_by(BomStep.seq_no).all())
    for i, st in enumerate(steps_list):
        hl = _header_line_for_bom_step(job, st.output_item_code)
        pcode = (st.process_code or '').strip().upper()
        if not (
            detail
            and hl
            and _is_fg_packaging_process_code(pcode)
            and _is_fg_output_item_for_job(job, (st.output_item_code or '').strip())
        ):
            continue
        prev_qty = None
        for j in range(i - 1, -1, -1):
            pst = steps_list[j]
            if pst.planned_qty is None:
                continue
            try:
                v = float(pst.planned_qty)
                if v > 0:
                    prev_qty = v
                    break
            except (TypeError, ValueError):
                continue
        try:
            if prev_qty is not None:
                st.planned_qty = Decimal(str(prev_qty))
            else:
                st.planned_qty = Decimal(str(fg_planned_qty_pcs(job, detail, hl)))
        except Exception:
            st.planned_qty = Decimal(str(float(hl.dispatch_qty or 1)))


def _patch_bom_linked_production_orders(job: JobMaster, bom: Bom) -> list[str]:
    """PATCH PlannedQuantity / Warehouse on existing SAP POs for this BOM."""
    sap_configured = bool(current_app.config.get('SAP_SERVICE_LAYER_URL'))
    if not sap_configured:
        return []
    # Preserve BOM-studio planned_qty edits exactly as entered (do not re-sync from headers here).
    errors: list[str] = []
    sap = SAPClient()
    try:
        omjd_result = upsert_omjd_job_card(sap, job)
        omjd_doc_entry = omjd_result.get('doc_entry')
        if omjd_doc_entry in (None, ''):
            return [
                f'OMJD SAP {omjd_result.get("action", "upsert")} for '
                f'{job.job_no} did not return a DocEntry.'
            ]
        sap_job_ent = str(omjd_doc_entry)
        for st in bom.steps.order_by(BomStep.seq_no).all():
            if not st.sap_doc_entry or st.planned_qty is None:
                continue
            payload: dict = {'PlannedQuantity': float(st.planned_qty)}
            payload['U_JobEnt'] = sap_job_ent[:254]
            cat = _sap_job_category_code(job)
            if cat:
                payload['U_Cat'] = cat[:20]
            _pc = (st.process_code or '').strip()
            if _pc:
                payload['U_PCode'] = _pc[:20]
            wh = (st.sap_warehouse or '').strip()
            if wh:
                payload['Warehouse'] = wh
            try:
                _patch_production_order_with_planned_qty_retry(
                    sap,
                    int(st.sap_doc_entry),
                    payload,
                )
            except SAPClientError as e:
                errors.append(f'Step {st.seq_no}: {str(e)[:180]}')
    finally:
        sap.logout()
    return errors


def _apply_removed_bom_steps_to_sap(
    job: JobMaster,
    removed_steps: list[BomStep],
    *,
    sap_client: Optional[SAPClient] = None,
) -> list[str]:
    """Cancel production orders in SAP and deactivate intermediate item codes for removed steps.

    Uses ``SAPClient.close_production_order`` (Service Layer **Cancel** action). When
    ``sap_client`` is provided, it is used for all calls and is **not** logged out here
    (caller owns the session).
    """
    sap_configured = bool(current_app.config.get('SAP_SERVICE_LAYER_URL'))
    if not sap_configured or not removed_steps:
        return []
    errors: list[str] = []
    own_client = sap_client is None
    sap = sap_client or SAPClient()
    try:
        for st in removed_steps:
            cancelled_po = False
            if st.sap_doc_entry:
                try:
                    sap.close_production_order(int(st.sap_doc_entry))
                    cancelled_po = True
                    st.sap_doc_entry = None
                    st.sap_doc_num = None
                except SAPClientError as e:
                    errors.append(f'Cancel PO step {st.seq_no}: {str(e)[:120]}')
            code = (st.output_item_code or '').strip()
            if not cancelled_po or not code or _is_fg_output_item_for_job(job, code):
                continue
            try:
                sap.set_item_valid(code, valid=False)
            except SAPClientError as e:
                errors.append(f'Deactivate item {code}: {str(e)[:120]}')
    finally:
        if own_client:
            sap.logout()
    return errors


def _collect_superseded_bom_sap_cleanup_steps(detail_line: JobDetailLine) -> list[BomStep]:
    """Steps on inactive BOM versions for this detail (SAP PO cancel / item cleanup before push)."""
    boms = (
        Bom.query.filter_by(detail_line_id=detail_line.id, is_active=False)
        .order_by(Bom.version.asc())
        .all()
    )
    out: list[BomStep] = []
    seen: set[int] = set()
    for ob in boms:
        for st in ob.steps.order_by(BomStep.seq_no).all():
            if st.id not in seen:
                seen.add(st.id)
                out.append(st)
    return out


def _sap_transfer_links_close_removed_po_steps(
    job: JobMaster,
    detail_line: JobDetailLine,
    active_bom: Bom,
    sap_client: SAPClient,
) -> tuple[list[str], dict[int, BomStep]]:
    """Reuse SAP DocEntry on matching new steps, then cancel POs left on superseded (removed) steps.

    Call after a new active BOM version exists (regenerate or studio save). Returns SAP cleanup
    warnings and a relink map (``new_step.id`` → old ``BomStep``) for diagnostics; SAP pushes
    always use a full line PATCH for linked steps.
    """
    db.session.flush()
    relink_pairs, _ = _transfer_sap_po_from_inactive_boms(detail_line, active_bom)
    db.session.flush()
    superseded = _collect_superseded_bom_sap_cleanup_steps(detail_line)
    warns: list[str] = []
    if superseded:
        warns = _apply_removed_bom_steps_to_sap(job, superseded, sap_client=sap_client)
    return warns, _relink_map_from_pairs(relink_pairs)


def _ensure_bom_routing_items_in_sap(job: JobMaster, bom: Bom, sap: SAPClient) -> None:
    """Create or refresh intermediate routing item codes in SAP for non-FG BOM steps."""
    hdr = job.header_lines.order_by(JobHeaderLine.line_no).first()
    fg_code = (hdr.sap_fg_item_code or '') if hdr else ''
    fg_name = hdr.fg_display_label if hdr else 'FG'
    detail = bom.detail_line
    sap_item_group = int(current_app.config.get('SAP_BOM_PROCESS_ITEM_GROUP_CODE', 115))
    sap_item_uom = _unit1_default_uom()
    for step in bom.steps.order_by(BomStep.seq_no).all():
        pcode = (step.process_code or '').strip()
        if _is_fg_packaging_process_code(pcode):
            continue
        out_code = (step.output_item_code or '').strip()
        if not out_code:
            continue
        from app.services.unit1_item_naming import unit1_process_item_description

        out_name = unit1_process_item_description(out_code)[:100] or out_code
        try:
            sap.ensure_item_exists(
                out_code[:50],
                out_name,
                base_fg_code=fg_code or None,
                item_group_code=sap_item_group,
                sales_uom=step.uom or sap_item_uom,
            )
        except SAPClientError as e:
            current_app.logger.warning('[SAP-ENSURE] %s: %s', out_code, e)
        try:
            sap.set_item_valid(out_code[:50], valid=True)
        except SAPClientError as e:
            current_app.logger.warning('[SAP-ENSURE] reactivate %s: %s', out_code, e)


def _parse_process_rows_from_form() -> list[tuple[Optional[int], str]]:
    """Parallel ``step_id[]`` (optional) and ``proc_code[]`` from POST."""
    ids = request.form.getlist('step_id[]')
    codes = request.form.getlist('proc_code[]')
    out: list[tuple[Optional[int], str]] = []
    n = max(len(ids), len(codes))
    for i in range(n):
        raw_id = ids[i].strip() if i < len(ids) else ''
        code = codes[i].strip() if i < len(codes) else ''
        if not code:
            continue
        sid: Optional[int] = None
        if raw_id.isdigit():
            sid = int(raw_id)
        out.append((sid, code))
    return out


def _extras_queues_from_old_bom(job: JobMaster, detail: JobDetailLine, old_bom: Bom) -> defaultdict[tuple[str, int], deque]:
    """Collect non-linkage required rows from ``old_bom`` keyed by (process_code, card_idx).

    Used during BOM regeneration to preserve planner-added materials/consumables for processes
    that still exist in the new sequence. Inter-step linkage rows are excluded.
    """
    header_lines = list(job.header_lines.order_by(JobHeaderLine.line_no).all())
    prev_outputs_by_card: dict[int, str] = {}
    queues: defaultdict[tuple[str, int], deque] = defaultdict(deque)

    for st in old_bom.steps.order_by(BomStep.seq_no).all():
        pc = (st.process_code or '').strip()
        if not pc:
            continue
        hl = _header_line_for_bom_step(job, st.output_item_code)
        cidx = 0
        if hl is not None and hl in header_lines:
            cidx = header_lines.index(hl)

        prev_full = (prev_outputs_by_card.get(cidx) or '').strip().upper()
        extras: list[dict[str, Any]] = []
        for inp in st.inputs.order_by(BomStepInput.id).all():
            code = (inp.sap_item_code or '').strip()
            if not code:
                continue
            if prev_full and code.upper() == prev_full:
                continue
            # Negative diecut co-products are regenerated by new-job combi logic; don't carry over.
            try:
                qf = float(inp.qty_per_job) if inp.qty_per_job is not None else None
            except Exception:
                qf = None
            if qf is not None and qf < 0:
                continue
            extras.append(
                {
                    'sap_item_code': code,
                    'description': (inp.description or '')[:200],
                    'warehouse': (inp.sap_warehouse or '') or '',
                    'qty_per_job': '' if inp.qty_per_job is None else str(float(inp.qty_per_job)),
                    'uom': (inp.uom or '') or _unit1_default_uom(),
                    'preserve_on_regen': True,
                }
            )

        if st.output_item_code:
            prev_outputs_by_card[cidx] = str(st.output_item_code).strip().upper()

        if extras:
            queues[(pc.upper(), int(cidx))].append(extras)

    return queues


def _po_remarks_queues_from_old_bom(job: JobMaster, detail: JobDetailLine, old_bom: Bom) -> defaultdict[tuple[str, int], deque]:
    """Collect production order remarks from ``old_bom`` keyed by (process_code, card_idx).

    Used during BOM regeneration so planner-entered PO remarks survive across versions.
    """
    header_lines = list(job.header_lines.order_by(JobHeaderLine.line_no).all())
    queues: defaultdict[tuple[str, int], deque] = defaultdict(deque)
    for st in old_bom.steps.order_by(BomStep.seq_no).all():
        pc = (st.process_code or '').strip()
        if not pc:
            continue
        rm = (getattr(st, "production_order_remarks", None) or "").strip()
        if not rm:
            continue
        hl = _header_line_for_bom_step(job, st.output_item_code)
        cidx = 0
        if hl is not None and hl in header_lines:
            cidx = header_lines.index(hl)
        queues[(pc.upper(), int(cidx))].append(rm[:254])
    return queues


def _regenerate_bom_from_process_list(
    job: JobMaster,
    detail: JobDetailLine,
    old_bom: Bom,
    process_pairs: list[tuple[Optional[int], str]],
    user_id: int,
    *,
    sap_ensure_items: bool = True,
) -> Bom:
    """Create a new BOM version from an ordered list of process codes.

    Rebuilds the BOM using the **same implementation as new-job BOM creation** by synthesizing
    a minimal ``bom_payload_json`` block and calling ``persist_bom_payload_block``.

    ``old_bom`` is intentionally not cloned: regeneration is authoritative from the edited
    process sequence + current job/header/detail data.

    When ``sap_ensure_items`` is true and SAP is configured, creates/updates routing item master
    rows for non-FG steps. When false, only local DB rows are written; the edit-BOM **Regenerate**
    handler still runs SAP PO relink + close-removed cleanup when SAP is configured.
    """
    sap_configured = bool(current_app.config.get('SAP_SERVICE_LAYER_URL'))
    sap_client = SAPClient() if (sap_configured and sap_ensure_items) else None

    headers = list(job.header_lines.order_by(JobHeaderLine.line_no).all())
    allowed_hdr_idxs: list[int] = []
    for inv in detail.fg_involved.all():
        hl = inv.header_line
        if hl and hl in headers:
            allowed_hdr_idxs.append(headers.index(hl))
    if not allowed_hdr_idxs and headers:
        allowed_hdr_idxs = list(range(len(headers)))

    def _is_split_process(code: str, name: str, pm_row: Optional[ProcessMaster]) -> bool:
        c = (code or '').strip().upper()
        n = (name or '').strip().lower()
        if c in ('CV-DIE', 'DIE', 'DIECUT', 'DIECUTTING', 'DIE-CUT', 'DIECUT-TRY', 'DIE-TRY', 'DIE-TRAY', 'EMB+P'):
            return True
        if 'diecut' in n or ('die' in n and 'cut' in n):
            return True
        if pm_row and (pm_row.default_workcenter or '').strip().upper() == 'DIECUTTING':
            return True
        return False

    normalized_pairs = list(process_pairs or [])

    extras_queues = _extras_queues_from_old_bom(job, detail, old_bom)
    remarks_queues = _po_remarks_queues_from_old_bom(job, detail, old_bom)

    sections: list[dict[str, Any]] = []
    has_reached_split = False
    for _sid, pcode in normalized_pairs:
        code = (pcode or '').strip()
        if not code:
            continue

        if _is_fg_packaging_process_code(code):
            pm = None
            pname = 'FG'
        else:
            pm = ProcessMaster.query.filter_by(process_code=code).first()
            if not pm:
                current_app.logger.warning('[BOM-REGEN] Unknown process_code=%s skipped', code)
                continue
            pname = (pm.name or code).strip()

        is_fg_step = _is_fg_packaging_process_code(code)
        is_split_step = _is_split_process(code, pname, pm)

        cards: list[dict[str, Any]] = []
        if is_fg_step:
            for hi in allowed_hdr_idxs:
                q = extras_queues.get((code.upper(), int(hi)))
                merged = list(q.popleft()) if q else []
                rq = remarks_queues.get((code.upper(), int(hi)))
                rm = (rq.popleft() if rq else None) or ''
                cards.append({'card_idx': hi, 'required_items': merged, 'production_order_remarks': rm})
        elif is_split_step:
            hi0 = allowed_hdr_idxs[0]
            q0 = extras_queues.get((code.upper(), int(hi0)))
            merged0 = list(q0.popleft()) if q0 else []
            rq0 = remarks_queues.get((code.upper(), int(hi0)))
            rm0 = (rq0.popleft() if rq0 else None) or ''
            cards.append({'card_idx': hi0, 'required_items': merged0, 'production_order_remarks': rm0})
        elif has_reached_split:
            for hi in allowed_hdr_idxs:
                q = extras_queues.get((code.upper(), int(hi)))
                merged = list(q.popleft()) if q else []
                rq = remarks_queues.get((code.upper(), int(hi)))
                rm = (rq.popleft() if rq else None) or ''
                cards.append({'card_idx': hi, 'required_items': merged, 'production_order_remarks': rm})
        else:
            hi0 = allowed_hdr_idxs[0]
            q0 = extras_queues.get((code.upper(), int(hi0)))
            merged0 = list(q0.popleft()) if q0 else []
            rq0 = remarks_queues.get((code.upper(), int(hi0)))
            rm0 = (rq0.popleft() if rq0 else None) or ''
            cards.append({'card_idx': hi0, 'required_items': merged0, 'production_order_remarks': rm0})

        if is_split_step:
            has_reached_split = True

        sections.append({'process_name': pname, 'process_code': code, 'cards': cards})

    block: dict[str, Any] = {
        'line_index': detail_material_row_index(job, detail),
        'sections': sections,
    }

    new_bom = create_bom(detail, user_id=user_id)
    try:
        persist_bom_payload_block(
            job,
            detail,
            new_bom,
            block,
            sap_client,
            resolve_process_code=_resolve_process_code,
            process_item_code_fn=_process_item_code,
            header_line_for_bom_step=_header_line_for_bom_step,
            synthetic_display_name_for_process_item_code=_synthetic_display_name_for_process_item_code,
            planner_sequence=[str(pc).strip() for _sid, pc in normalized_pairs if str(pc or '').strip()],
        )
        db.session.flush()
        return new_bom
    finally:
        if sap_client:
            try:
                sap_client.logout()
            except Exception:
                pass


@jobs_bp.route('/<int:job_id>/bom/<int:bom_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'planner')
def edit_bom_spec(job_id, bom_id):
    """Edit FG ups/qty, wastage sheets, process sequence, regenerate or save BOM studio.

    * **Regenerate BOM** — applies FG + wastage sheets from this form, rebuilds BOM (same
      quantity rules as new-job creation), then opens the BOM studio for the new version.
    * **Save BOM & update SAP** — reads ``bom_payload_json`` from the studio, writes a new
      BOM version, then runs SAP cleanup / routing items / production orders (same as push).
    """
    job = JobMaster.query.get_or_404(job_id)
    bom = Bom.query.filter_by(id=bom_id, is_active=True).first_or_404()
    if not _bom_belongs_to_job(job, bom):
        flash('This BOM does not belong to this job.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))
    if not job.is_editable:
        flash('This job can only be edited before it is released.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    detail = bom.detail_line
    fg_rows: list[tuple[JobDetailLineFgInvolved, JobHeaderLine]] = []
    for inv in detail.fg_involved.all():
        hl = inv.header_line
        if hl:
            fg_rows.append((inv, hl))

    process_choices = (
        ProcessMaster.query.filter_by(is_active=True)
        .order_by(ProcessMaster.category, ProcessMaster.name)
        .all()
    )
    steps = list(bom.steps.order_by(BomStep.seq_no).all())
    _studio_kw = dict(
        studio_block=bom_block_from_saved_bom(
            job, detail, bom, header_line_for_bom_step=_header_line_for_bom_step
        ),
        material_row_index=detail_material_row_index(job, detail),
    )

    if request.method == 'POST':
        action = (request.form.get('bom_action') or '').strip().lower()

        # --- FG quantities (mirror header lines; same inputs as new-job SO/FG lines) ---
        # For detail lines after the first, UPS is a per-detail-line value (material row UPS),
        # so we must not overwrite header-line UPS while editing that BOM.
        if (detail.detail_no or 0) > 1:
            detail.yield_loss_pct = Decimal('0')
            for _inv, hl in fg_rows:
                dq = request.form.get(f'fg_{hl.id}_dispatch_qty', '').strip()
                if dq != '':
                    try:
                        hl.dispatch_qty = Decimal(dq)
                    except Exception:
                        flash(f'Invalid quantity for line {hl.line_no}.', 'danger')
                        return render_template(
                            'jobs/edit_bom_spec.html',
                            job=job,
                            bom=bom,
                            detail=detail,
                            fg_rows=fg_rows,
                            process_choices=process_choices,
                            steps=steps,
                            bom_input_names=_bom_input_display_names_by_id(job),
                            **_studio_kw,
                        )
        else:
            detail.yield_loss_pct = Decimal('0')
            for _inv, hl in fg_rows:
                dq = request.form.get(f'fg_{hl.id}_dispatch_qty', '').strip()
                if dq != '':
                    try:
                        hl.dispatch_qty = Decimal(dq)
                    except Exception:
                        flash(f'Invalid quantity for line {hl.line_no}.', 'danger')
                        return render_template(
                            'jobs/edit_bom_spec.html',
                            job=job,
                            bom=bom,
                            detail=detail,
                            fg_rows=fg_rows,
                            process_choices=process_choices,
                            steps=steps,
                            bom_input_names=_bom_input_display_names_by_id(job),
                            **_studio_kw,
                        )

        if action in ('save_bom_builder', 'regenerate'):
            sheet_err = _apply_edit_bom_detail_sheet_fields(job, detail, request.form)
            if sheet_err:
                flash(sheet_err, 'danger')
                return render_template(
                    'jobs/edit_bom_spec.html',
                    job=job,
                    bom=bom,
                    detail=detail,
                    fg_rows=fg_rows,
                    process_choices=process_choices,
                    steps=steps,
                    bom_input_names=_bom_input_display_names_by_id(job),
                    **_studio_kw,
                )

        if action == 'save_bom_builder':
            if not current_user.has_role('admin', 'planner'):
                abort(403)

            raw_bom = request.form.get('bom_payload_json', '').strip()
            try:
                blocks = json.loads(raw_bom) if raw_bom else []
            except Exception:
                blocks = []
            mat_i = detail_material_row_index(job, detail)
            block: Optional[dict] = None
            if isinstance(blocks, list):
                for b in blocks:
                    if isinstance(b, dict) and int(b.get('line_index', -999999)) == mat_i:
                        block = b
                        break
                if block is None and blocks and isinstance(blocks[0], dict):
                    block = blocks[0]
            if not block or not isinstance(block.get('sections'), list) or not block['sections']:
                flash('BOM data missing or invalid. Edit the BOM studio and try again.', 'danger')
                return render_template(
                    'jobs/edit_bom_spec.html',
                    job=job,
                    bom=bom,
                    detail=detail,
                    fg_rows=fg_rows,
                    process_choices=process_choices,
                    steps=steps,
                    bom_input_names=_bom_input_display_names_by_id(job),
                    **_studio_kw,
                )

            sap_configured = bool(current_app.config.get('SAP_SERVICE_LAYER_URL'))
            sap_client: Optional[SAPClient] = SAPClient() if sap_configured else None
            try:
                create_bom(detail, user_id=current_user.id)
                new_bom = detail.active_bom
                if not new_bom:
                    raise RuntimeError('No active BOM after revision')
                persist_bom_payload_block(
                    job,
                    detail,
                    new_bom,
                    block,
                    sap_client,
                    resolve_process_code=_resolve_process_code,
                    process_item_code_fn=_process_item_code,
                    header_line_for_bom_step=_header_line_for_bom_step,
                    synthetic_display_name_for_process_item_code=_synthetic_display_name_for_process_item_code,
                    planner_sequence=(bom.slip_process_sequence_codes if bom else None),
                )
                # Preserve BOM-studio planned_qty edits exactly as entered (do not re-sync from headers here).
                cleanup_warns: list[str] = []
                created_count = 0
                patched_count = 0
                skipped_patch = 0
                if sap_client:
                    sap_job_ent = _ensure_omjd_doc_entry_for_job(sap_client, job)
                    cleanup_warns, _relink_pairs = _sap_transfer_links_close_removed_po_steps(
                        job, detail, new_bom, sap_client
                    )
                    _ensure_bom_routing_items_in_sap(job, new_bom, sap_client)
                    created_count, patched_count, skipped_patch = _push_bom_to_sap(
                        job,
                        new_bom,
                        sap_client,
                        sap_job_ent=sap_job_ent,
                    )
                db.session.commit()
                flash(
                    f'BOM saved from the studio and SAP updated '
                    f'({patched_count} production order(s) full-PATCHed, '
                    f'{created_count} created for new steps'
                    f'{", " + str(skipped_patch) + " kept existing SAP planned qty" if skipped_patch else ""}).',
                    'success',
                )
                for w in cleanup_warns:
                    flash(f'SAP cleanup: {w}', 'warning')
                return redirect(url_for('jobs.view_job', job_id=job.id))
            except Exception as e:
                db.session.rollback()
                flash(f'Error saving BOM: {str(e)}', 'danger')
            finally:
                if sap_client:
                    try:
                        sap_client.logout()
                    except Exception:
                        pass

        if action == 'regenerate':
            pairs = _parse_process_rows_from_form()
            if not pairs:
                flash('Add at least one process step before regenerating the BOM.', 'danger')
                return render_template(
                    'jobs/edit_bom_spec.html',
                    job=job,
                    bom=bom,
                    detail=detail,
                    fg_rows=fg_rows,
                    process_choices=process_choices,
                    steps=steps,
                    bom_input_names=_bom_input_display_names_by_id(job),
                    **_studio_kw,
                )

            try:
                new_bom = _regenerate_bom_from_process_list(
                    job, detail, bom, pairs, current_user.id, sap_ensure_items=False
                )
                if new_bom:
                    _sync_bom_step_planned_qty_from_headers(job, new_bom)
                regen_cleanup: list[str] = []
                sap_cleanup: Optional[SAPClient] = None
                if bool(current_app.config.get('SAP_SERVICE_LAYER_URL')):
                    sap_cleanup = SAPClient()
                    try:
                        regen_cleanup, _ = _sap_transfer_links_close_removed_po_steps(
                            job, detail, new_bom, sap_cleanup
                        )
                    finally:
                        try:
                            sap_cleanup.logout()
                        except Exception:
                            pass
                db.session.commit()
                if not new_bom:
                    flash('BOM was regenerated but no active BOM was found.', 'danger')
                    return redirect(url_for('jobs.view_job', job_id=job.id))
                flash(
                    'BOM regenerated. Edit it in the BOM studio below, then use '
                    'Save BOM & update SAP to push line changes / create POs for new steps.',
                    'success',
                )
                for w in regen_cleanup:
                    flash(f'SAP cleanup: {w}', 'warning')
                return redirect(
                    url_for('jobs.edit_bom_spec', job_id=job.id, bom_id=new_bom.id)
                    + '#edit-bom-preview'
                )
            except Exception as e:
                db.session.rollback()
                flash(f'Error regenerating BOM: {str(e)}', 'danger')

        elif action == 'save':
            flash(
                'FG-only save was removed. Use Regenerate BOM or Save BOM & update SAP '
                'to apply FG / wastage sheets and update quantities.',
                'info',
            )
            return redirect(url_for('jobs.edit_bom_spec', job_id=job.id, bom_id=bom.id))

    return render_template(
        'jobs/edit_bom_spec.html',
        job=job,
        bom=bom,
        detail=detail,
        fg_rows=fg_rows,
        process_choices=process_choices,
        steps=steps,
        bom_input_names=_bom_input_display_names_by_id(job),
        **_studio_kw,
    )


@jobs_bp.route('/<int:job_id>/bom-steps/<int:step_id>/edit-po', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'planner')
def edit_po_step(job_id, step_id):
    """Edit warehouse, non-linkage component lines, and add lines; PATCH SAP PO when linked.

    The line that consumes the **previous** step's output (same item code as that output) is
    fixed here — change routing in the BOM studio instead. Planned quantity is not edited
    on this screen (use BOM studio / FG save flows).
    """
    job = JobMaster.query.get_or_404(job_id)
    step = BomStep.query.get_or_404(step_id)
    bom = step.bom
    if not bom.is_active or not _bom_belongs_to_job(job, bom):
        flash('This production step is not editable for this job.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))
    if not job.is_editable:
        flash('This job can only be edited before it is released.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    inputs = list(step.inputs.order_by(BomStepInput.id).all())
    input_rows = [
        {'inp': inp, 'is_linkage': _is_previous_step_linkage_input(bom, step, inp)}
        for inp in inputs
    ]

    def _render(**extra):
        return render_template(
            'jobs/edit_po_step.html',
            job=job,
            step=step,
            bom=bom,
            input_rows=input_rows,
            **extra,
        )

    if request.method == 'POST':
        detail = bom.detail_line
        hl = _header_line_for_bom_step(job, step.output_item_code)
        base_fg = (hl.sap_fg_item_code or '').strip() if hl else ''
        sap_item_group = int(current_app.config.get('SAP_BOM_PROCESS_ITEM_GROUP_CODE', 115))

        parsed_existing: list[
            tuple[BomStepInput, str, str, Optional[str], str, str]
        ] = []
        for row in input_rows:
            inp = row['inp']
            if row['is_linkage']:
                continue
            raw_code = request.form.get(f'inp_{inp.id}_sap_item_code') or ''
            code, auto_nm = _parse_sap_item_code_from_form_field(raw_code)
            code = (code or '').strip()[:50]
            desc = (request.form.get(f'inp_{inp.id}_description') or '').strip()[:200]
            if auto_nm and not desc:
                desc = auto_nm[:200]
            qty_raw = request.form.get(f'inp_{inp.id}_qty', '').strip()
            wh_line = (request.form.get(f'inp_{inp.id}_sap_warehouse') or '').strip()[:20] or None
            uom_raw = (request.form.get(f'inp_{inp.id}_uom') or '').strip()[:10]
            if not code:
                flash(
                    f'Component SAP code cannot be empty (row id {inp.id}). '
                    'Remove the line in the BOM studio if it should not exist.',
                    'danger',
                )
                return _render()
            if qty_raw != '':
                try:
                    Decimal(qty_raw)
                except Exception:
                    flash(f'Invalid quantity for item {code}.', 'danger')
                    return _render()
            parsed_existing.append((inp, code, desc, wh_line, uom_raw, qty_raw))

        new_codes = request.form.getlist('new_sap_item_code')
        new_qtys = request.form.getlist('new_qty_per_job')
        new_descs = request.form.getlist('new_description')
        new_whs = request.form.getlist('new_sap_warehouse')
        new_uoms = request.form.getlist('new_uom')
        parsed_new: list[tuple[str, Decimal, str, Optional[str], str]] = []
        for i, raw_code in enumerate(new_codes):
            code, auto_nm = _parse_sap_item_code_from_form_field(raw_code or '')
            code = (code or '').strip()[:50]
            if not code:
                continue
            q_raw = new_qtys[i].strip() if i < len(new_qtys) else ''
            if not q_raw:
                flash(f'Quantity is required for new component {code}.', 'danger')
                return _render()
            try:
                q_dec = Decimal(q_raw)
            except Exception:
                flash(f'Invalid quantity for new component {code}.', 'danger')
                return _render()
            desc = (new_descs[i].strip()[:200] if i < len(new_descs) else '') or ''
            if auto_nm and not desc:
                desc = auto_nm[:200]
            wh_n = (new_whs[i].strip()[:20] if i < len(new_whs) else '') or None
            uom_n = (new_uoms[i].strip()[:10] if i < len(new_uoms) else '') or _unit1_default_uom()
            parsed_new.append((code, q_dec, desc, wh_n, uom_n))

        step.sap_warehouse = request.form.get('sap_warehouse', '').strip() or None
        step.warehouse = request.form.get('warehouse', '').strip() or None
        po_rem_raw = (request.form.get('production_order_remarks') or '').strip()[:254]
        step.production_order_remarks = po_rem_raw if po_rem_raw else None

        for inp, code, desc, wh_line, uom_raw, qty_raw in parsed_existing:
            inp.sap_item_code = code
            inp.description = desc or None
            inp.sap_warehouse = wh_line
            inp.uom = uom_raw or None
            inp.qty_per_job = Decimal(qty_raw) if qty_raw != '' else None

        for code, q_dec, desc, wh_n, uom_n in parsed_new:
            add_input(
                step=step,
                input_type='raw_material',
                sap_item_code=code,
                description=desc or code[:200],
                uom=uom_n,
                qty_per_job=q_dec,
                sap_warehouse=wh_n,
            )

        sap_client: Optional[SAPClient] = None
        sap_ok = True
        sap_msg = ''
        try:
            sap_configured = bool(current_app.config.get('SAP_SERVICE_LAYER_URL'))
            sap_client = SAPClient() if sap_configured else None
            db.session.flush()
            if sap_client:
                for inp in step.inputs.filter(BomStepInput.sap_item_code.isnot(None)).all():
                    if _is_previous_step_linkage_input(bom, step, inp):
                        continue
                    c = (inp.sap_item_code or '').strip().upper()
                    if not c:
                        continue
                    nm = (inp.description or '').strip() or _synthetic_display_name_for_process_item_code(
                        job, detail, c
                    ) or c
                    try:
                        sap_client.ensure_item_exists(
                            c[:50],
                            nm[:200],
                            base_fg_code=base_fg or None,
                            item_group_code=sap_item_group,
                            sales_uom=(inp.uom or _unit1_default_uom())[:10],
                        )
                    except SAPClientError as e:
                        current_app.logger.warning(
                            '[EDIT-PO] ensure_item_exists %s: %s', c, str(e)[:200]
                        )

            db.session.commit()

            if step.sap_doc_entry and sap_client:
                from app.services.mfg_warehouse import default_sap_warehouse

                default_wh = default_sap_warehouse()
                params = _bom_step_special_po_params(job, bom, step, default_wh)
                try:
                    skipped_planned_qty = _patch_production_order_with_planned_qty_retry(
                        sap_client,
                        int(step.sap_doc_entry),
                        _sap_build_full_patch_body_from_po_params(params),
                        replace_collections=True,
                    )
                    if skipped_planned_qty:
                        current_app.logger.warning(
                            '[EDIT-PO] DocEntry=%s component lines updated; header PlannedQuantity unchanged.',
                            step.sap_doc_entry,
                        )
                except SAPClientError as e:
                    sap_ok = False
                    sap_msg = str(e)
                    current_app.logger.warning('[SAP-PATCH-PO] full lines: %s', sap_msg[:300])
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving: {str(e)}', 'danger')
            inputs_err = list(step.inputs.order_by(BomStepInput.id).all())
            input_rows_err = [
                {'inp': i, 'is_linkage': _is_previous_step_linkage_input(bom, step, i)}
                for i in inputs_err
            ]
            return render_template(
                'jobs/edit_po_step.html',
                job=job,
                step=step,
                bom=bom,
                input_rows=input_rows_err,
            )
        finally:
            if sap_client is not None:
                try:
                    sap_client.logout()
                except Exception:
                    pass

        if sap_ok:
            flash('Production step saved and SAP order updated.', 'success')
        else:
            flash(
                'Saved locally; SAP production order could not be fully updated: '
                f'{sap_msg[:300]}',
                'warning',
            )
        return redirect(url_for('jobs.view_job', job_id=job.id))

    return _render()


# ---- helper: release all SAP Production Orders linked to a job ----
def _release_sap_production_orders(job):
    """Release every SAP Production Order tied to this job's BOM steps.

    Returns (released_count, error_messages_list).
    """
    from app.models.mfg_bom import Bom, BomStep
    from app.services.sap_job_client import SAPClient, SAPClientError

    # Collect all sap_doc_entry values across all BOMs for this job
    detail_lines = job.detail_lines.all()
    doc_entries = []
    for jdl in detail_lines:
        bom = jdl.active_bom
        if not bom:
            continue
        for step in bom.steps.all():
            if step.sap_doc_entry:
                doc_entries.append(step.sap_doc_entry)

    if not doc_entries:
        return 0, []

    sap_client = SAPClient()
    released = 0
    errors = []
    for doc_entry in doc_entries:
        try:
            sap_client.release_production_order(doc_entry)
            released += 1
            current_app.logger.info(
                f'[SAP-RELEASE] Released ProductionOrder DocEntry={doc_entry} for job {job.job_no}'
            )
        except SAPClientError as e:
            msg = f'DocEntry {doc_entry}: {str(e)[:200]}'
            errors.append(msg)
            current_app.logger.warning(f'[SAP-RELEASE] Failed: {msg}')

    return released, errors


def _linked_sap_production_order_refs(job) -> list[tuple[int, Optional[int]]]:
    """Return unique linked SAP production orders as ``(DocEntry, DocNum)`` pairs."""
    refs: dict[int, Optional[int]] = {}
    for jdl in job.detail_lines.all():
        bom = jdl.active_bom
        if not bom:
            continue
        for step in bom.steps.all():
            if step.sap_doc_entry:
                de = int(step.sap_doc_entry)
                dn = int(step.sap_doc_num) if step.sap_doc_num is not None else None
                if de not in refs:
                    refs[de] = dn
                elif refs[de] is None and dn is not None:
                    refs[de] = dn
    return list(refs.items())


FINAL_FG_PROCESS_CODES = {'FG', 'PK-PACK'}
MATERIAL_ISSUE_PREFIXES = ('PMT', 'FIL', 'ADH', 'RMC', 'TAP')


def _first_float_from_dict(row: dict, *keys: str) -> float:
    for key in keys:
        if not key:
            continue
        candidates = [key]
        if len(key) > 1:
            candidates.append(key[0].lower() + key[1:])
        for candidate in candidates:
            if candidate not in row:
                continue
            value = row.get(candidate)
            if value in (None, ''):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def _production_order_abs_entry(row: dict, fallback: int) -> int:
    for key in ('AbsoluteEntry', 'DocEntry', 'DocumentAbsoluteEntry'):
        value = row.get(key)
        if value not in (None, ''):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return int(fallback)


def _is_final_fg_output_item(job: JobMaster, output_item_code: str) -> bool:
    code_u = (output_item_code or '').strip().upper()
    if not code_u:
        return False
    fg_codes = [
        (hl.sap_fg_item_code or '').strip().upper()
        for hl in job.header_lines.order_by(JobHeaderLine.line_no).all()
        if (hl.sap_fg_item_code or '').strip()
    ]
    for fg_u in fg_codes:
        if code_u == fg_u:
            return True
        if code_u.startswith(f'{fg_u}-') and code_u.endswith('-FG'):
            return True
    return False


def _final_fg_po_steps(job: JobMaster) -> list[BomStep]:
    out: list[BomStep] = []
    seen_entries: set[int] = set()
    for jdl in job.detail_lines.all():
        bom = jdl.active_bom
        if not bom:
            continue
        for step in bom.steps.order_by(BomStep.seq_no).all():
            if not step.sap_doc_entry:
                continue
            pcode = (step.process_code or '').strip().upper()
            if pcode not in FINAL_FG_PROCESS_CODES:
                continue
            if not _is_final_fg_output_item(job, step.output_item_code or ''):
                continue
            de = int(step.sap_doc_entry)
            if de in seen_entries:
                continue
            seen_entries.add(de)
            out.append(step)
    return out


def _line_is_material_issue(line: dict) -> bool:
    code_u = str(line.get('ItemNo') or line.get('ItemCode') or '').strip().upper()
    return any(code_u.startswith(prefix) for prefix in MATERIAL_ISSUE_PREFIXES)


def _fg_completion_row_from_sap(
    sap_client: SAPClient,
    step: BomStep,
) -> dict:
    doc_entry = int(step.sap_doc_entry)
    header = sap_client.get(
        f'/ProductionOrders({doc_entry})',
        params={
            '$select': (
                'AbsoluteEntry,DocumentNumber,ItemNo,ProductDescription,PlannedQuantity,'
                'CompletedQuantity,ProductionOrderStatus,U_PCode,U_JobEnt'
            )
        },
    )
    lines = sap_client.fetch_production_order_lines_raw(doc_entry)
    issued_qty = 0.0
    for line in lines:
        if _line_is_material_issue(line):
            continue
        issued_qty += _first_float_from_dict(line, 'IssuedQuantity', 'IssuedQty')
    completed_qty = _first_float_from_dict(header, 'CompletedQuantity')
    pending_qty = max(0.0, issued_qty - completed_qty)
    status = sap_client._normalize_production_order_status(
        header.get('ProductionOrderStatus') or header.get('productionOrderStatus')
    )
    return {
        'doc_entry': _production_order_abs_entry(header, doc_entry),
        'doc_num': header.get('DocumentNumber') or step.sap_doc_num,
        'item_no': header.get('ItemNo') or step.output_item_code,
        'product_description': header.get('ProductDescription') or step.step_name or '',
        'planned_qty': _first_float_from_dict(header, 'PlannedQuantity'),
        'issued_qty': issued_qty,
        'completed_qty': completed_qty,
        'pending_qty': pending_qty,
        'status': status,
        'process_code': header.get('U_PCode') or step.process_code,
        'job_entry': header.get('U_JobEnt'),
    }


def _fetch_final_fg_completion_rows(job: JobMaster, sap_client: SAPClient) -> list[dict]:
    return [_fg_completion_row_from_sap(sap_client, step) for step in _final_fg_po_steps(job)]


def _find_final_fg_completion_row(
    job: JobMaster,
    sap_client: SAPClient,
    doc_entry: int,
) -> Optional[dict]:
    wanted = int(doc_entry)
    for row in _fetch_final_fg_completion_rows(job, sap_client):
        if int(row.get('doc_entry') or 0) == wanted:
            return row
    return None


def _close_linked_sap_production_orders_final(job: JobMaster, sap_client: SAPClient) -> dict:
    closed_count = 0
    skipped_count = 0
    warnings: list[str] = []
    errors: list[str] = []

    for doc_entry, doc_num in _linked_sap_production_order_refs(job):
        label = str(doc_num or doc_entry)
        try:
            status = sap_client._fetch_production_order_status(int(doc_entry))
            if status == 'cancelled':
                warnings.append(f'Production Order {label} is already cancelled; not counted as closed.')
                continue
            if status == 'closed':
                skipped_count += 1
                continue
            if status == 'planned':
                sap_client.release_production_order(int(doc_entry))
            sap_client.mark_production_order_closed(int(doc_entry))
            closed_count += 1
        except SAPClientError as e:
            errors.append(f'Production Order {label}: {str(e)[:220]}')

    return {
        'closed_count': closed_count,
        'skipped_count': skipped_count,
        'warnings': warnings,
        'errors': errors,
    }


# ---- helper: selectively cancel SAP Production Orders linked to a job ----
def _cancel_sap_production_orders_if_safe(job):
    """Cancel linked SAP production orders only when no issued material exists.

    Returns ``(cancelled_count, warnings_list)``.
    """
    from app.services.sap_job_client import SAPClient, SAPClientError

    doc_refs = _linked_sap_production_order_refs(job)
    if not doc_refs:
        return 0, []

    sap_client = SAPClient()
    cancelled = 0
    warnings = []
    try:
        for doc_entry, doc_num in doc_refs:
            label = str(doc_num or doc_entry)
            try:
                if sap_client.production_order_has_issued_material(int(doc_entry)):
                    warnings.append(
                        f'Production Order {label}: issued material exists; not cancelled.'
                    )
                    current_app.logger.warning(
                        '[SAP-CANCEL-PO] Skipped DocEntry=%s DocNum=%s for job %s because issued material exists',
                        doc_entry,
                        doc_num,
                        job.job_no,
                    )
                    continue
                sap_client.close_production_order(int(doc_entry))
                cancelled += 1
                current_app.logger.info(
                    '[SAP-CANCEL-PO] Cancelled ProductionOrder DocEntry=%s DocNum=%s for job %s',
                    doc_entry,
                    doc_num,
                    job.job_no,
                )
            except SAPClientError as e:
                msg = f'Production Order {label}: {str(e)[:200]}'
                warnings.append(msg)
                current_app.logger.warning('[SAP-CANCEL-PO] Failed: %s', msg)
    finally:
        try:
            sap_client.logout()
        except Exception:
            pass

    return cancelled, warnings


def _cancel_job_sap_production_orders(job):
    """Cancel linked SAP production orders only if all are safe."""
    doc_refs = _linked_sap_production_order_refs(job)
    if not doc_refs:
        return {'ok': True, 'cancelled_count': 0, 'blocked': [], 'errors': []}

    sap_client = SAPClient()
    blocked: list[dict] = []
    errors: list[dict] = []
    cancel_refs: list[tuple[int, Optional[int], str]] = []
    try:
        for doc_entry, doc_num in doc_refs:
            label = str(doc_num or doc_entry)
            try:
                status = sap_client._fetch_production_order_status(int(doc_entry))
                has_issued = sap_client.production_order_has_issued_material(int(doc_entry))
            except SAPClientError as e:
                errors.append({
                    'doc_entry': doc_entry,
                    'doc_num': doc_num,
                    'label': label,
                    'error': str(e)[:200],
                })
                continue

            if has_issued:
                blocked.append({
                    'doc_entry': doc_entry,
                    'doc_num': doc_num,
                    'label': label,
                    'error': f'Issued material exists; cannot cancel production order {label}.',
                })
                continue

            cancel_refs.append((doc_entry, doc_num, label))

        if blocked or errors:
            return {'ok': False, 'cancelled_count': 0, 'blocked': blocked, 'errors': errors}

        cancelled_count = 0
        for doc_entry, doc_num, label in cancel_refs:
            try:
                status = sap_client._fetch_production_order_status(int(doc_entry))
                if status == 'planned':
                    sap_client.release_production_order(int(doc_entry))
                sap_client.close_production_order(int(doc_entry))
                cancelled_count += 1
                current_app.logger.info(
                    '[SAP-CANCEL-JOB-PO] Cancelled ProductionOrder DocEntry=%s DocNum=%s for job %s (status=%s)',
                    doc_entry,
                    doc_num,
                    job.job_no,
                    status,
                )
            except SAPClientError as e:
                errors.append({
                    'doc_entry': doc_entry,
                    'doc_num': doc_num,
                    'label': label,
                    'error': str(e)[:200],
                })
                current_app.logger.warning(
                    '[SAP-CANCEL-JOB-PO] Failed DocEntry=%s DocNum=%s for job %s: %s',
                    doc_entry,
                    doc_num,
                    job.job_no,
                    str(e)[:200],
                )
                break

        if errors:
            return {'ok': False, 'cancelled_count': cancelled_count, 'blocked': [], 'errors': errors}

        return {'ok': True, 'cancelled_count': cancelled_count, 'blocked': [], 'errors': []}
    finally:
        try:
            sap_client.logout()
        except Exception:
            pass



@jobs_bp.route('/<int:job_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'planner', 'quality')
def change_status(job_id):
    job = JobMaster.query.get_or_404(job_id)
    to_status = request.form.get('to_status', '').strip()
    reason = request.form.get('reason', '').strip() or None
    from_status = job.overall_status

    if not to_status:
        flash('No target status specified.', 'danger')
        return redirect(url_for('jobs.view_job', job_id=job.id))
    if to_status == 'closed':
        flash('Use Final close job so FG completion can be verified before closing SAP production orders.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    try:
        transition_job_status(job, to_status, remark=reason, user_id=current_user.id)
        db.session.commit()

        # When releasing a job, also release all linked SAP Production Orders
        if from_status == 'staged' and to_status == 'released':
            sap_released, sap_errors = _release_sap_production_orders(job)
            if sap_released:
                flash(
                    f'Job status changed to Released. '
                    f'{sap_released} SAP Production Order(s) also released.',
                    'success',
                )
            else:
                flash(f'Job status changed to Released.', 'success')
            if sap_errors:
                for err in sap_errors:
                    flash(f'SAP release warning: {err}', 'warning')
        
        # When closing a job, only cancel SAP POs that have no issued material.
        elif to_status == 'closed':
            sap_cancelled, sap_warnings = _cancel_sap_production_orders_if_safe(job)
            status_label = to_status.replace('_', ' ').title()
            if sap_cancelled:
                flash(
                    f'Job status changed to {status_label}. '
                    f'{sap_cancelled} SAP Production Order(s) cancelled in SAP.',
                    'success',
                )
            else:
                flash(f'Job status changed to {status_label}.', 'success')
            for warning in sap_warnings:
                flash(f'SAP warning: {warning}', 'warning')

        elif to_status == 'cancelled':
            cancel_result = _cancel_job_sap_production_orders(job)
            if not cancel_result.get('ok'):
                rows = []
                for item in (cancel_result.get('blocked', []) or []) + (cancel_result.get('errors', []) or []):
                    rows.append({
                        'label': item.get('label') or str(item.get('doc_num') or item.get('doc_entry') or '—'),
                        'error': item.get('error') or 'Unable to cancel production order.',
                    })
                session['job_cancel_block'] = {
                    'job_id': job.id,
                    'job_no': job.job_no,
                    'rows': rows,
                }
                db.session.rollback()
                return redirect(url_for('jobs.view_job', job_id=job.id))

            transition_job_status(job, to_status, remark=reason, user_id=current_user.id)
            db.session.commit()
            flash(
                f'Job status changed to Cancelled. '
                f'{cancel_result.get("cancelled_count", 0)} SAP Production Order(s) cancelled in SAP.',
                'success',
            )
        
        else:
            flash(f'Job status changed to {to_status.replace("_", " ").title()}.', 'success')

    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'danger')

    return redirect(url_for('jobs.view_job', job_id=job.id))


@jobs_bp.route('/<int:job_id>/final-close/data')
@login_required
@role_required('admin', 'planner', 'quality')
def final_close_data(job_id):
    job = JobMaster.query.get_or_404(job_id)
    if not current_app.config.get('SAP_SERVICE_LAYER_URL'):
        return jsonify({'ok': False, 'error': 'SAP is not configured.'}), 400

    sap_client = SAPClient()
    try:
        rows = _fetch_final_fg_completion_rows(job, sap_client)
    finally:
        try:
            sap_client.logout()
        except Exception:
            pass

    pending = [r for r in rows if float(r.get('pending_qty') or 0) > 0.0001]
    return jsonify({
        'ok': True,
        'rows': rows,
        'can_close': not pending,
        'pending_count': len(pending),
    })


@jobs_bp.route('/<int:job_id>/final-close/report-completion', methods=['POST'])
@login_required
@role_required('admin', 'planner', 'quality')
def report_final_fg_completion(job_id):
    job = JobMaster.query.get_or_404(job_id)
    if not current_app.config.get('SAP_SERVICE_LAYER_URL'):
        return jsonify({'ok': False, 'error': 'SAP is not configured.'}), 400

    doc_entry_raw = request.form.get('doc_entry')
    qty_raw = request.form.get('quantity')
    batch_number = (request.form.get('batch_number') or '').strip()
    remarks = (request.form.get('remarks') or '').strip()
    try:
        doc_entry = int(doc_entry_raw)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Invalid production order.'}), 400
    try:
        qty = float(qty_raw)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Invalid completion quantity.'}), 400
    if qty <= 0:
        return jsonify({'ok': False, 'error': 'Completion quantity must be greater than zero.'}), 400
    if not batch_number:
        return jsonify({'ok': False, 'error': 'Batch number is required.'}), 400

    sap_client = SAPClient()
    try:
        row = _find_final_fg_completion_row(job, sap_client, doc_entry)
        if not row:
            return jsonify({'ok': False, 'error': 'Production order is not a final FG completion step for this job.'}), 400
        pending_qty = float(row.get('pending_qty') or 0)
        if qty > pending_qty + 0.0001:
            return jsonify({
                'ok': False,
                'error': f'Completion quantity cannot exceed pending quantity ({pending_qty:g}).',
            }), 400
        result = sap_client.report_production_order_completion(
            doc_entry,
            qty,
            batch_number,
            remarks=remarks,
        )
        rows = _fetch_final_fg_completion_rows(job, sap_client)
    except SAPClientError as e:
        return jsonify({'ok': False, 'error': str(e)[:500]}), 400
    finally:
        try:
            sap_client.logout()
        except Exception:
            pass

    pending = [r for r in rows if float(r.get('pending_qty') or 0) > 0.0001]
    return jsonify({
        'ok': True,
        'message': 'Completion reported in SAP.',
        'sap_response': result,
        'rows': rows,
        'can_close': not pending,
        'pending_count': len(pending),
    })


@jobs_bp.route('/<int:job_id>/final-close', methods=['POST'])
@login_required
@role_required('admin', 'planner', 'quality')
def final_close_job(job_id):
    job = JobMaster.query.get_or_404(job_id)
    reason = (request.form.get('reason') or '').strip() or None
    if not can_transition(job.overall_status, 'closed'):
        return jsonify({
            'ok': False,
            'error': f'Cannot move job {job.job_no} from "{job.overall_status}" to "closed".',
        }), 400
    if not current_app.config.get('SAP_SERVICE_LAYER_URL'):
        return jsonify({'ok': False, 'error': 'SAP is not configured.'}), 400

    sap_client = SAPClient()
    try:
        rows = _fetch_final_fg_completion_rows(job, sap_client)
        pending = [r for r in rows if float(r.get('pending_qty') or 0) > 0.0001]
        if pending:
            return jsonify({
                'ok': False,
                'error': 'FG completion is still pending. Report completion before final close.',
                'rows': rows,
                'can_close': False,
                'pending_count': len(pending),
            }), 400

        close_result = _close_linked_sap_production_orders_final(job, sap_client)
        if close_result.get('errors'):
            return jsonify({
                'ok': False,
                'error': 'Could not close all linked SAP production orders.',
                'errors': close_result['errors'],
                'warnings': close_result.get('warnings', []),
            }), 400

        transition_job_status(job, 'closed', remark=reason, user_id=current_user.id)
        db.session.commit()
        flash(
            f'Job status changed to Closed. {close_result.get("closed_count", 0)} SAP Production Order(s) closed.',
            'success',
        )
        for warning in close_result.get('warnings', []):
            flash(f'SAP warning: {warning}', 'warning')
        return jsonify({
            'ok': True,
            'redirect_url': url_for('jobs.view_job', job_id=job.id),
            'closed_count': close_result.get('closed_count', 0),
            'skipped_count': close_result.get('skipped_count', 0),
            'warnings': close_result.get('warnings', []),
        })
    except SAPClientError as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)[:500]}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)[:500]}), 400
    finally:
        try:
            sap_client.logout()
        except Exception:
            pass


# --------------------------------------------------------- HEADER LINES
@jobs_bp.route('/<int:job_id>/lines/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'planner')
def add_line(job_id):
    job = JobMaster.query.get_or_404(job_id)

    if not job.is_editable:
        flash('This job can only be edited before it is released.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    fg_items = SapItemMirror.query.filter_by(item_type='fg').order_by(
        SapItemMirror.item_name
    ).all()

    if request.method == 'POST':
        fg_code = request.form.get('fg_item_code', '').strip()
        dispatch_qty = request.form.get('dispatch_qty', '').strip()
        component_type = request.form.get('component_type', '').strip()
        uom = request.form.get('uom', _unit1_default_uom()).strip()
        job_type = request.form.get('job_type', 'new').strip()
        length = request.form.get('length') or None
        width = request.form.get('width') or None
        height = request.form.get('height') or None

        if not fg_code or not dispatch_qty:
            flash('FG item code and dispatch quantity are required.', 'danger')
            return render_template('jobs/add_line.html', job=job, fg_items=fg_items)

        fg_item = SapItemMirror.query.get(fg_code)
        fg_name = fg_item.item_name if fg_item else fg_code

        try:
            line, detail = add_header_line(
                job=job,
                fg_item_code=fg_code,
                fg_item_name=fg_name,
                dispatch_qty=dispatch_qty,
                uom=uom,
                ups=request.form.get('ups', 1, type=int),
                job_type=job_type,
                length=length,
                width=width,
                height=height,
            )


            db.session.commit()
            flash('Component line added successfully.', 'success')
            return redirect(url_for('jobs.view_job', job_id=job.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding line: {str(e)}', 'danger')

    return render_template('jobs/add_line.html', job=job, fg_items=fg_items)


@jobs_bp.route('/lines/<int:line_id>/detail', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'planner')
def edit_detail(line_id):
    """Edit print / substrate spec for the detail line paired with this header (same line_no)."""
    line = JobHeaderLine.query.get_or_404(line_id)
    job = line.job

    if not job.is_editable:
        flash('This job can only be edited before it is released.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    detail = JobDetailLine.query.filter_by(
        job_id=job.job_no,
        detail_no=line.line_no,
    ).first()
    is_new = detail is None
    if is_new:
        detail = JobDetailLine(
            job_id=job.job_no,
            detail_no=line.line_no,
        )

    if request.method == 'POST':
        _apply_non_bom_job_detail_fields(detail, '')
        try:
            if is_new:
                db.session.add(detail)
            db.session.commit()
            flash('Printing specification saved.', 'success')
            return redirect(url_for('jobs.view_job', job_id=job.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving spec: {str(e)}', 'danger')

    return render_template(
        'jobs/edit_detail.html',
        job=job,
        line=line,
        detail=detail,
        is_new=is_new,
    )



# -------------------------------------------------- PUSH PRODUCTION ORDERS
@jobs_bp.route('/<int:job_id>/bom/<int:bom_id>/push_production_orders', methods=['POST'])
@login_required
@role_required('admin', 'planner')
def push_production_orders(job_id, bom_id):
    """Sync SAP for the active BOM: relink from inactive BOMs, cleanup, routing items, then POs.

    1. Move ``sap_doc_entry`` from matching steps on **any** inactive BOM version onto the active
       BOM (newest inactive first), so cleanup does not close orders that still apply.
    2. Cancel production orders / deactivate routing items for removed superseded steps only.
    3. Ensure intermediate routing item codes exist (and are valid) in SAP.
    4. PATCH linked orders (header-only or full lines vs matched predecessor) or POST new POs.
    """
    from app.models.mfg_bom import Bom, BomStep, BomStepInput
    from app.services.sap_job_client import SAPClient, SAPClientError
    from datetime import date

    job = JobMaster.query.get_or_404(job_id)
    bom = Bom.query.filter_by(id=bom_id, is_active=True).first_or_404()
    if not _bom_belongs_to_job(job, bom):
        flash('This BOM does not belong to this job.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))
    if not job.is_editable:
        flash('This job can only be edited before it is released.', 'warning')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    sap_configured = bool(current_app.config.get('SAP_SERVICE_LAYER_URL'))
    if not sap_configured:
        flash('SAP is not configured. Production orders cannot be created.', 'danger')
        return redirect(url_for('jobs.view_job', job_id=job.id))

    sap_client = SAPClient()
    try:
        sap_job_ent = _ensure_omjd_doc_entry_for_job(sap_client, job)
        cleanup_warns, _relink_pairs = _sap_transfer_links_close_removed_po_steps(
            job, bom.detail_line, bom, sap_client
        )
        _ensure_bom_routing_items_in_sap(job, bom, sap_client)
        created_count, patched_count, skipped_patch = _push_bom_to_sap(
            job,
            bom,
            sap_client,
            sap_job_ent=sap_job_ent,
        )
        db.session.commit()

        flash(
            f'✅ SAP: {patched_count} production order(s) full-PATCHed, '
            f'{created_count} created. '
            f'{str(skipped_patch) + " kept existing SAP planned qty. " if skipped_patch else ""}'
            f'Document numbers are shown under each BOM step.',
            'success',
        )
        for w in cleanup_warns:
            flash(f'SAP cleanup: {w}', 'warning')
    except SAPClientError as exc:
        db.session.rollback()
        flash(f'SAP error: {exc}', 'danger')
    except Exception as exc:
        db.session.rollback()
        flash(f'Unexpected error: {exc}', 'danger')
    finally:
        try:
            sap_client.logout()
        except Exception:
            pass

    return redirect(url_for('jobs.view_job', job_id=job.id))
