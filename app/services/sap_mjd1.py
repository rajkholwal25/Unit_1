"""SAP @MJD1 / MJD1: customer → open SO (U_SoNo) → FG (U_FGCode).

In SAP B1 the user table is shown as **@MJD1**; the table code is **MJD1**; Service Layer exposes a
standalone UDT as **U_MJD1** (never use ``@`` in OData paths).

Two modes (see config):
- **udo** (default): User-defined **object** ``OMJD`` with lines in ``MJD1Collection`` (DPR pattern).
- **udt**: Direct reads from the UDT **@MJD1** as **/U_MJD1** (or ``SAP_MJD1_ODATA_PATH``).

Field names default to your UDF names; override via .env (see config.py).
"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Dict, List, Optional

from flask import current_app

from app.logging_config import get_logger
from app.services.sap_job_client import SAPClient, SAPClientError

_log = get_logger('mjd1')

# Set True after first UDT GET returns -1002 so we skip repeated failed UDT calls (same process).
_udt_odata_unavailable: bool = False


def _is_service_not_found_error(exc: SAPClientError) -> bool:
    s = str(exc)
    return '-1002' in s or 'service not found' in s.lower()


MJD1_HINT_SERVICE_NOT_FOUND = (
    'SAP -1002 Service Not Found: this OData entity is not exposed at the path we called. '
    'For SAP UDT @MJD1 the OData name is usually U_MJD1 (table code MJD1, no @ in the URL). '
    'Set SAP_MJD1_ODATA_PATH from $metadata if needed. '
    'If lines live on OMJD, use SAP_MJD1_SOURCE=udo (default). '
    'Restart the SAP Service Layer after adding UDTs/UDOs.'
)

MJD1_HINT_UDO_INVALID_EXPAND = (
    'SAP rejected $expand on OMJD: the OData NavigationProperty name is not MJD1Collection on your system. '
    'This app loads lines via GET /OMJD(DocEntry) without list $expand. '
    'If lines are still missing, set SAP_MJD1_UDO_LINES_JSON_KEY to the JSON array property name, '
    'and/or SAP_MJD1_UDO_EXPAND_NAV to the NavigationProperty from $metadata (EntityType OMJD).'
)


def _mjd1_source() -> str:
    s = (current_app.config.get('SAP_MJD1_SOURCE') or 'udo').strip().lower()
    return s if s in ('udo', 'udt') else 'udo'


def _udo_object_path() -> str:
    name = (current_app.config.get('SAP_MJD1_UDO_OBJECT') or 'OMJD').strip()
    return name if name.startswith('/') else f'/{name}'


def _udt_path() -> str:
    cfg = current_app.config
    override = (cfg.get('SAP_MJD1_ODATA_PATH') or '').strip()
    if override:
        return override if override.startswith('/') else f'/{override}'
    t = cfg['SAP_UDT_MJD1_TABLE']
    if t.startswith('U_'):
        return f'/{t}'
    return f'/U_{t}'


def _mjd1_path_for_errors() -> str:
    if _mjd1_source() == 'udo':
        return f"{_udo_object_path()} (list + per-doc GET; lines JSON key configurable)"
    return _udt_path()


def mjd1_error_payload(message: str) -> Dict[str, Any]:
    """JSON body for failed MJD1 API calls (includes hint for common SAP codes)."""
    out: Dict[str, Any] = {
        'error': message,
        'odata_path': _mjd1_path_for_errors(),
        'mjd1_source': _mjd1_source(),
    }
    if 'Service Not Found' in message or '-1002' in message:
        out['hint'] = MJD1_HINT_SERVICE_NOT_FOUND
        _log.warning('MJD1 OData issue path=%s: %s', out['odata_path'], message[:300])
    if 'invalid navigation property' in message.lower() or 'cannot expand' in message.lower():
        out['hint'] = MJD1_HINT_UDO_INVALID_EXPAND
        out['odata_path'] = _mjd1_path_for_errors()
    return out


def _select_fields() -> str:
    cfg = current_app.config
    parts = [
        cfg['SAP_MJD1_FIELD_CODE'],
        cfg['SAP_MJD1_FIELD_CUSTOMER_NAME'],
        cfg['SAP_MJD1_FIELD_SO'],
        cfg['SAP_MJD1_FIELD_FG'],
        'Name',
    ]
    fn = cfg.get('SAP_MJD1_FIELD_FG_NAME')
    if fn:
        parts.append(fn)
    fq = cfg.get('SAP_MJD1_FIELD_QTY')
    if fq:
        parts.append(fq)
    cc = cfg.get('SAP_MJD1_FIELD_CARD_CODE')
    if cc:
        parts.append(cc)
    return ','.join(dict.fromkeys(p for p in parts if p))


def _line_num_key(ln: Dict[str, Any]) -> int:
    try:
        v = ln.get('LineNum')
        if v is None:
            return 0
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _dedupe_merge_mjd1_lines(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Service Layer sometimes returns two entries for the same LineNum (one sparse); merge, prefer non-empty."""
    buckets: Dict[int, Dict[str, Any]] = {}
    order: List[int] = []
    for ln in lines:
        k = _line_num_key(ln)
        if k not in buckets:
            buckets[k] = dict(ln)
            order.append(k)
            continue
        base = buckets[k]
        for kk, vv in ln.items():
            if vv is None or vv == '':
                continue
            cur = base.get(kk)
            if cur is None or cur == '':
                base[kk] = vv
    return [buckets[k] for k in order]


def _udo_lines_json_keys_to_try() -> List[str]:
    """Property names on OMJD JSON that may hold the MJD1 line array (Service Layer varies)."""
    cfg = current_app.config
    explicit = (cfg.get('SAP_MJD1_UDO_LINES_JSON_KEY') or '').strip()
    if explicit:
        return [explicit]
    legacy = (cfg.get('SAP_MJD1_UDO_LINES_COLLECTION') or 'MJD1Collection').strip()
    return list(
        dict.fromkeys(
            [
                legacy,
                'MJD1Collection',
                'MJD1',
                'MJD1Rows',
                'U_MJD1',
                'U_MJD1Collection',
                'MJD1LineCollection',
            ]
        )
    )


def _lines_from_omjd_doc(doc: Dict[str, Any]) -> tuple[List[Dict[str, Any]], str]:
    """Return (lines, key_used). Empty lines if none found."""
    for k in _udo_lines_json_keys_to_try():
        v = doc.get(k)
        if isinstance(v, list):
            return v, k
    return [], ''


def _omjd_header_without_lines(p: Dict[str, Any]) -> Dict[str, Any]:
    """OMJD UDFs (e.g. U_CustName, CardName) often live on the document header, not on each line."""
    line_keys = set(_udo_lines_json_keys_to_try())
    out: Dict[str, Any] = {}
    for k, v in p.items():
        if k in line_keys:
            continue
        if isinstance(v, list):
            continue
        out[k] = v
    return out


def _csv_list(key: str, default: str = '') -> list[str]:
    raw = current_app.config.get(key)
    if raw is None or raw == '':
        raw = default
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [x.strip() for x in str(raw).split(',') if x.strip()]


def _first_value(row: Dict[str, Any], fields: list[str]):
    if not isinstance(row, dict):
        return None
    for field in fields or []:
        if not field:
            continue
        candidates = [field]
        if len(field) > 1:
            candidates.append(field[0].lower() + field[1:])
        for key in candidates:
            if key not in row:
                continue
            v = row.get(key)
            if v not in (None, ''):
                return v
    return None


def _first_text(row: Dict[str, Any], fields: list[str]) -> str:
    v = _first_value(row, fields)
    return str(v).strip() if v not in (None, '') else ''


def _first_int(row: Dict[str, Any], fields: list[str]) -> Optional[int]:
    v = _first_value(row, fields)
    if v in (None, ''):
        return None
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _first_float(row: Dict[str, Any], fields: list[str]) -> Optional[float]:
    v = _first_value(row, fields)
    if v in (None, ''):
        return None
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _extract_fg_num(value: Any) -> str:
    if value is None:
        return ''
    s = str(value).strip()
    if not s:
        return ''
    match = re.search(r'(FG\d+)', s, re.IGNORECASE)
    return match.group(1).upper() if match else s


def _fg_match_tokens(row: Dict[str, Any], *extra_values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in extra_values:
        token = _extract_fg_num(value)
        if token:
            tokens.add(token)
    for key in (
        'fg_code',
        'fg_name',
        'supplier_cat_num',
        'ItemCode',
        'Dscription',
        'SupplierCatNum',
        'SubCatNum',
    ):
        token = _extract_fg_num(row.get(key))
        if token:
            tokens.add(token)
    return tokens


def _num_filter_expr(field: str, value: Any) -> str:
    if not field or value in (None, ''):
        return ''
    s = str(value).strip()
    if not s:
        return ''
    try:
        return f'{field} eq {int(float(s))}'
    except (TypeError, ValueError):
        esc = s.replace("'", "''")
        return f"{field} eq '{esc}'"


def _customer_name_fallback_keys() -> List[str]:
    raw = (current_app.config.get('SAP_MJD1_CUSTOMER_NAME_FALLBACKS') or '').strip()
    if raw:
        return [x.strip() for x in raw.split(',') if x.strip()]
    return ['CardName', 'U_CardName', 'U_CustNm', 'U_Customer']


def customer_name_from_row(r: Dict[str, Any]) -> str:
    """Resolved display/match name for customer (primary UDF + optional fallbacks)."""
    k = current_app.config['SAP_MJD1_FIELD_CUSTOMER_NAME']
    v = (r.get(k) or '').strip()
    if v:
        return v
    for alt in _customer_name_fallback_keys():
        if alt == k:
            continue
        v = (r.get(alt) or '').strip()
        if v:
            return v
    return ''


def _fetch_one_omjd_document(client: SAPClient, doc_entry: int) -> Dict[str, Any]:
    """GET single OMJD document with MJD1 lines (no invalid list $expand)."""
    path = _udo_object_path()
    url = f'{path}({doc_entry})'
    cfg = current_app.config
    data = client.get(url)
    lines, _ = _lines_from_omjd_doc(data)
    if lines:
        return data
    # Optional: user knows exact OData NavigationProperty for $expand
    nav_user = (cfg.get('SAP_MJD1_UDO_EXPAND_NAV') or '').strip()
    if nav_user:
        try:
            data = client.get(url, params={'$expand': nav_user})
            lines, _ = _lines_from_omjd_doc(data)
            if lines:
                _log.info('OMJD DocEntry=%s lines loaded via $expand=%s', doc_entry, nav_user)
                return data
        except SAPClientError as e:
            _log.warning('OMJD $expand=%s failed: %s', nav_user, e)
    # Try common OData navigation names (may differ from JSON property name)
    for nav in ('MJD1', 'MJD1Collection', 'U_MJD1', 'MJD1Rows', 'MJD1LineCollection'):
        try:
            data = client.get(url, params={'$expand': nav})
            lines, _ = _lines_from_omjd_doc(data)
            if lines:
                _log.info('OMJD DocEntry=%s lines loaded via $expand=%s', doc_entry, nav)
                return data
        except SAPClientError:
            continue
    _log.warning(
        'OMJD DocEntry=%s: no line array found; tried JSON keys %s and $expand fallbacks',
        doc_entry,
        _udo_lines_json_keys_to_try()[:5],
    )
    return data


def _flatten_udo_to_mjd1_rows(parents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn OMJD documents + line rows into one dict per line (like U_MJD1 rows)."""
    cfg = current_app.config
    code_field = cfg['SAP_MJD1_FIELD_CODE']
    sep = cfg.get('SAP_MJD1_LINE_KEY_SEPARATOR') or '|'
    flat: List[Dict[str, Any]] = []
    for p in parents:
        doc_entry = p.get('DocEntry')
        lines, _ = _lines_from_omjd_doc(p)
        if not lines:
            continue
        header = _omjd_header_without_lines(p)
        lines = _dedupe_merge_mjd1_lines(lines)
        for ln in lines:
            row = {**header, **ln}
            ln_key = _line_num_key(ln)
            row[code_field] = f"{doc_entry}{sep}{ln_key}"
            flat.append(row)
    return flat


def fetch_mjd1_rows(client: SAPClient) -> List[Dict[str, Any]]:
    """Load all MJD1-equivalent rows: UDO lines (OMJD) or UDT rows (U_MJD1).

    If ``SAP_MJD1_SOURCE=udt`` but Service Layer returns -1002 for the UDT entity (not exposed),
    we fall back to OMJD once and remember that for this process so later calls skip UDT.
    """
    global _udt_odata_unavailable
    if _mjd1_source() == 'udt' and not _udt_odata_unavailable:
        try:
            return _fetch_mjd1_rows_udt(client)
        except SAPClientError as e:
            if _is_service_not_found_error(e):
                _udt_odata_unavailable = True
                _log.warning(
                    'fetch_mjd1_rows: UDT %s not available from Service Layer (%s); '
                    'using OMJD (udo) instead. SQL @"MJD1" may list more rows than OData exposes.',
                    _udt_path(),
                    str(e)[:200],
                )
                return _fetch_mjd1_rows_udo(client)
            raise
    if _mjd1_source() == 'udt' and _udt_odata_unavailable:
        return _fetch_mjd1_rows_udo(client)
    return _fetch_mjd1_rows_udo(client)


def _fetch_mjd1_rows_udt(client: SAPClient) -> List[Dict[str, Any]]:
    path = _udt_path()
    select = _select_fields()
    _log.info('fetch_mjd1_rows (udt): path=%s $select=%s', path, select)
    rows: List[Dict[str, Any]] = []
    skip = 0
    page = 2000
    while True:
        try:
            data = client.get(
                path,
                params={
                    '$select': select,
                    '$top': page,
                    '$skip': skip,
                },
            )
        except SAPClientError as e:
            _log.error(
                'fetch_mjd1_rows udt failed at skip=%s path=%s: %s',
                skip,
                path,
                e,
            )
            raise
        batch = data.get('value') or []
        if not batch:
            break
        rows.extend(batch)
        skip += len(batch)
        if skip > 50000:
            break
    _log.info('fetch_mjd1_rows (udt): loaded %s row(s)', len(rows))
    return rows


def _fetch_mjd1_rows_udo(client: SAPClient) -> List[Dict[str, Any]]:
    """List OMJD without $expand (avoids OData 201 invalid navigation property), then GET each doc."""
    path = _udo_object_path()
    _log.info('fetch_mjd1_rows (udo): path=%s $select=DocEntry+DocNum then per-doc GET', path)
    all_flat: List[Dict[str, Any]] = []
    skip = 0
    page = 100
    while True:
        try:
            data = client.get(
                path,
                params={
                    '$select': 'DocEntry,DocNum',
                    '$top': page,
                    '$skip': skip,
                },
            )
        except SAPClientError as e:
            _log.error(
                'fetch_mjd1_rows udo list failed at skip=%s path=%s: %s',
                skip,
                path,
                e,
            )
            raise
        batch = data.get('value') or []
        if not batch:
            break
        parents_with_lines: List[Dict[str, Any]] = []
        for p in batch:
            de = p.get('DocEntry')
            if de is None:
                continue
            try:
                detail = _fetch_one_omjd_document(client, int(de))
                parents_with_lines.append(detail)
            except SAPClientError as ex:
                _log.warning('fetch_mjd1_rows udo: could not load DocEntry=%s: %s', de, ex)

        all_flat.extend(_flatten_udo_to_mjd1_rows(parents_with_lines))
        skip += len(batch)
        if skip > 50000:
            break
    _log.info('fetch_mjd1_rows (udo): loaded %s line(s)', len(all_flat))
    return all_flat


def fetch_mjd1_row_by_code(client: SAPClient, code: str) -> Dict[str, Any]:
    if _mjd1_source() == 'udt':
        path = _udt_path()
        k = str(code).replace("'", "''")
        return client.get(f"{path}('{k}')", params={'$select': _select_fields()})

    cfg = current_app.config
    sep = cfg.get('SAP_MJD1_LINE_KEY_SEPARATOR') or '|'
    parts = str(code).split(sep, 1)
    if len(parts) != 2 or not parts[0].isdigit():
        raise SAPClientError(
            f'MJD1 line key must be DocEntry{sep}LineNum (UDO mode). Got: {code!r}'
        )
    doc_entry = int(parts[0])
    if not parts[1].isdigit():
        raise SAPClientError(f'MJD1 line LineNum must be numeric. Got: {code!r}')
    line_num = int(parts[1])
    data = _fetch_one_omjd_document(client, doc_entry)
    lines, _ = _lines_from_omjd_doc(data)
    lines = _dedupe_merge_mjd1_lines(lines)
    header = _omjd_header_without_lines(data)
    for ln in lines:
        if _line_num_key(ln) == line_num:
            return {**header, **ln}
    raise SAPClientError(f'No MJD1 line DocEntry={doc_entry} LineNum={line_num}')


def find_open_order_by_so_doc_num(client: SAPClient, doc_num: Any) -> Optional[Dict[str, Any]]:
    """Return SAP Order row if DocNum is open (bost_Open)."""
    return _order_open(client, doc_num)


def _order_open(client: SAPClient, doc_num: Any) -> Optional[Dict[str, Any]]:
    try:
        n = int(str(doc_num).strip())
    except (TypeError, ValueError):
        return None
    try:
        data = client.get(
            '/Orders',
            params={
                '$filter': f"DocNum eq {n} and DocumentStatus eq 'bost_Open'",
                '$select': 'DocEntry,DocNum,DocDueDate,CardCode,CardName',
                '$top': 1,
            },
        )
        vals = data.get('value') or []
        return vals[0] if vals else None
    except SAPClientError:
        return None


def distinct_customers(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    seen: set[str] = set()
    out: List[Dict[str, str]] = []
    for r in rows:
        name = customer_name_from_row(r)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({'name': name})
    out.sort(key=lambda x: x['name'])
    return out


def distinct_bp_customer_names(client: SAPClient) -> List[Dict[str, str]]:
    """Distinct CardName from SAP Business Partners (customer type)."""
    seen: set[str] = set()
    out: List[Dict[str, str]] = []
    for bp in client.fetch_customers():
        name = (bp.get('CardName') or '').strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({'name': name})
    out.sort(key=lambda x: x['name'])
    return out


def distinct_ocrd_series_customers(client: SAPClient) -> List[Dict[str, str]]:
    """OCRD: CardCode + CardName for Business Partners from Series 1 and 89."""
    raw = []
    seen_codes: set[str] = set()
    for series in (1, 89):
        for bp in client.fetch_business_partners_by_series(series):
            code = (bp.get('CardCode') or '').strip()
            if not code:
                continue
            key = code.casefold()
            if key in seen_codes:
                continue
            seen_codes.add(key)
            raw.append(bp)
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for bp in raw:
        code = (bp.get('CardCode') or '').strip()
        name = (bp.get('CardName') or '').strip()
        if not code:
            continue
        key = code.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({'code': code, 'name': name or code})
    out.sort(key=lambda x: x['name'])
    return out


def merged_mjd1_and_bp_customer_names(
    rows: List[Dict[str, Any]],
    client: SAPClient,
) -> List[Dict[str, str]]:
    """Union of distinct MJD1 names (e.g. U_PrNa) and BP CardNames — for large dropdowns without /U_MJD1 OData."""
    seen: set[str] = set()
    out: List[Dict[str, str]] = []

    def add(name: str) -> None:
        name = (name or '').strip()
        if not name:
            return
        key = name.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append({'name': name})

    for r in rows:
        add(customer_name_from_row(r))
    for bp in client.fetch_customers():
        add(bp.get('CardName'))
    out.sort(key=lambda x: x['name'])
    return out


def _customer_list_source() -> str:
    s = (current_app.config.get('SAP_MJD1_CUSTOMER_LIST_SOURCE') or 'mjd1').strip().lower()
    if s in ('mjd1', 'business_partners', 'merged', 'ocrd_series'):
        return s
    return 'mjd1'


def mjd1_customer_list_for_dropdown(
    client: SAPClient,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    src = _customer_list_source()
    if src == 'ocrd_series':
        return distinct_ocrd_series_customers(client)
    if src == 'business_partners':
        return distinct_bp_customer_names(client)
    if src == 'merged':
        return merged_mjd1_and_bp_customer_names(rows, client)
    return distinct_customers(rows)


def row_matches_customer(
    r: Dict[str, Any],
    want: str,
    card_code: Optional[str] = None,
) -> bool:
    """Match dropdown selection to an MJD1 row (CardCode, UDF, U_PrNa, CardName)."""
    want_cc = (card_code or '').strip()
    if want_cc:
        rc = (r.get('CardCode') or '').strip()
        if rc and rc == want_cc:
            return True
        k_card_field = (current_app.config.get('SAP_MJD1_FIELD_CARD_CODE') or '').strip()
        if k_card_field:
            rv = (r.get(k_card_field) or '').strip()
            if rv and rv == want_cc:
                return True
    w = want.strip()
    if not w:
        return False
    wf = w.casefold()
    k = customer_name_from_row(r)
    if k and (k == w or k.casefold() == wf):
        return True
    for alt in ('CardName', 'U_CardName'):
        v = (r.get(alt) or '').strip()
        if v and (v == w or v.casefold() == wf):
            return True
    return False


def open_sales_orders_for_customer(
    client: SAPClient,
    rows: List[Dict[str, Any]],
    customer_name: str,
    card_code: Optional[str] = None,
) -> List[Dict[str, Any]]:
    k_so = current_app.config['SAP_MJD1_FIELD_SO']
    seen: set[str] = set()
    result: List[Dict[str, Any]] = []
    want = customer_name.strip()
    cc = card_code
    for r in rows:
        if not row_matches_customer(r, want, cc):
            continue
        so = r.get(k_so)
        if so is None or str(so).strip() == '':
            continue
        so_key = str(so).strip()
        if so_key in seen:
            continue
        ord_open = find_open_order_by_so_doc_num(client, so_key)
        if ord_open:
            seen.add(so_key)
            result.append({
                'so_no': so_key,
                'doc_entry': ord_open.get('DocEntry'),
                'doc_num': ord_open.get('DocNum'),
                'doc_due_date': ord_open.get('DocDueDate'),
            })
    result.sort(key=lambda x: str(x['so_no']))
    return result


def fg_lines_for_customer_so(
    rows: List[Dict[str, Any]],
    customer_name: str,
    so_no: str,
    card_code: Optional[str] = None,
) -> List[Dict[str, Any]]:
    cfg = current_app.config
    k_so = cfg['SAP_MJD1_FIELD_SO']
    k_fg = cfg['SAP_MJD1_FIELD_FG']
    k_fn = cfg.get('SAP_MJD1_FIELD_FG_NAME') or ''
    k_q = cfg.get('SAP_MJD1_FIELD_QTY') or ''
    k_code = cfg['SAP_MJD1_FIELD_CODE']
    out: List[Dict[str, Any]] = []
    want = customer_name.strip()
    cc = card_code
    for r in rows:
        if not row_matches_customer(r, want, cc):
            continue
        if str(r.get(k_so)).strip() != str(so_no).strip():
            continue
        fg = (r.get(k_fg) or '').strip()
        if not fg:
            continue
        qty = r.get(k_q) if k_q else None
        fg_name = (r.get(k_fn) or '').strip() if k_fn else ''
        out.append({
            'mjd1_code': r.get(k_code),
            'fg_code': fg,
            'fg_name': fg_name or fg,
            'dispatch_qty': qty,
        })
    return out


def _job_card_identity_matches(row: Dict[str, Any], doc_num: Any, series: Any) -> bool:
    doc_fields = _csv_list('SAP_JOB_CARD_DOCNUM_FIELDS', 'DocNum,DocEntry,Code')
    series_fields = _csv_list('SAP_JOB_CARD_SERIES_FIELDS', 'Series,SeriesCode,U_Series')
    doc_val = _first_value(row, doc_fields)
    if doc_val in (None, ''):
        return False
    if not _value_matches(doc_val, doc_num):
        return False
    if series in (None, ''):
        return True
    series_val = _first_value(row, series_fields)
    if series_val in (None, ''):
        return False
    return _value_matches(series_val, series)


def _value_matches(actual: Any, expected: Any) -> bool:
    if actual in (None, '') or expected in (None, ''):
        return False
    a = str(actual).strip()
    e = str(expected).strip()
    if not a or not e:
        return False
    if a == e:
        return True
    try:
        return int(float(a)) == int(float(e))
    except (TypeError, ValueError):
        return a.casefold() == e.casefold()


def _job_card_row_lists(row: Dict[str, Any], keys: list[str]) -> list[Dict[str, Any]]:
    for key in keys:
        v = row.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []


def _job_card_row_lists_any(doc: Dict[str, Any], keys: list[str]) -> list[Dict[str, Any]]:
    for key in keys:
        v = doc.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []


def _detail_value_has_content(value: Any) -> bool:
    if value in (None, '', False):
        return False
    if isinstance(value, (int, float)):
        return abs(float(value)) > 1e-12
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return False
        try:
            return abs(float(stripped)) > 1e-12
        except ValueError:
            return True
    if isinstance(value, dict):
        return any(_detail_value_has_content(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_detail_value_has_content(v) for v in value)
    return True


def _job_card_detail_row_has_content(row: Dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False

    content_field_groups = [
        _csv_list('SAP_JOB_CARD_DETAIL_STEP_NAME_FIELDS', 'StepName,ProcessName,U_ProcessName,Name,Dscription,ItemName'),
        _csv_list('SAP_JOB_CARD_DETAIL_PROCESS_CODE_FIELDS', 'U_JoEl,U_PQC,U_PriSt,ProcessCode,U_ProcessCode,OperationCode,Code'),
        _csv_list('SAP_JOB_CARD_DETAIL_OUTPUT_FIELDS', 'U_RaItC,U_RaItN,OutputItemCode,U_OutputItemCode,ItemCode,ItemNo'),
        _csv_list('SAP_JOB_CARD_DETAIL_PAPER_QUALITY_FIELDS', 'U_Grade,U_GRADE,U_PQC'),
        _csv_list('SAP_JOB_CARD_DETAIL_MILL_FIELDS', 'U_PBr,U_Mill,U_MILL'),
        _csv_list('SAP_JOB_CARD_DETAIL_QTY_FIELDS', 'PlannedQuantity,BaseQuantity,Quantity,U_Qty'),
        _csv_list('SAP_JOB_CARD_DETAIL_UPS_FIELDS', 'U_NoUps,U_UPS,UPS,Ups'),
        _csv_list('SAP_JOB_CARD_HEADER_LENGTH_FIELDS', 'U_Len,U_Length,U_CartonLength,Length'),
        _csv_list('SAP_JOB_CARD_HEADER_WIDTH_FIELDS', 'U_Wid,U_Width,U_CartonWidth,Width'),
        _csv_list('SAP_JOB_CARD_HEADER_HEIGHT_FIELDS', 'U_Hei,U_Height,U_CartonHeight,Height'),
        _csv_list('SAP_JOB_CARD_DETAIL_GSM_FIELDS', 'U_GSM,U_Gsm,U_gsm'),
        _csv_list('SAP_JOB_CARD_DETAIL_FRONT_COLOUR_FIELDS', 'U_Front,U_FRONT'),
        _csv_list('SAP_JOB_CARD_DETAIL_BACK_COLOUR_FIELDS', 'U_Back,U_BACK'),
        _csv_list('SAP_JOB_CARD_DETAIL_PRINT_STYLE_FIELDS', 'U_PriSt,U_PrSi'),
        _csv_list('SAP_JOB_CARD_DETAIL_PRINT_TYPE_FIELDS', 'U_Pltty,U_PType'),
        _csv_list('SAP_JOB_CARD_DETAIL_PRINT_TYPE_FLAG_FIELDS', 'U_Con,U_met'),
        _csv_list('SAP_JOB_CARD_DETAIL_DIE_NO_FIELDS', 'U_Dia,U_Die,U_DieNo,U_Die_No'),
        _csv_list('SAP_JOB_CARD_DETAIL_REMARK_FIELDS', 'Remarks,U_Remarks,Comments,ProductionOrderRemarks'),
    ]
    for fields in content_field_groups:
        if _detail_value_has_content(_first_value(row, fields)):
            return True
    return bool(_normalize_job_card_bom_inputs(row))


def _fetch_job_card_header_row(client: SAPClient, doc_num: Any, series: Any) -> Dict[str, Any]:
    source = _mjd1_source()
    if source == 'udt':
        rows = fetch_mjd1_rows(client)
        for row in rows:
            if _job_card_identity_matches(row, doc_num, series):
                return row
        return {}

    path = _udo_object_path()
    doc_fields = _csv_list('SAP_JOB_CARD_DOCNUM_FIELDS', 'DocNum,DocEntry,Code')
    series_fields = _csv_list('SAP_JOB_CARD_SERIES_FIELDS', 'Series,SeriesCode,U_Series')
    combos: list[tuple[str, Optional[str]]] = []
    for doc_field in doc_fields:
        if series_fields and series not in (None, ''):
            for series_field in series_fields:
                combos.append((doc_field, series_field))
        else:
            combos.append((doc_field, None))

    seen_filters: set[str] = set()
    for doc_field, series_field in combos:
        parts = []
        doc_expr = _num_filter_expr(doc_field, doc_num)
        if doc_expr:
            parts.append(doc_expr)
        if series_field:
            series_expr = _num_filter_expr(series_field, series)
            if series_expr:
                parts.append(series_expr)
        if not parts:
            continue
        filt = ' and '.join(parts)
        if filt in seen_filters:
            continue
        seen_filters.add(filt)
        try:
            data = client.get(path, params={'$filter': filt, '$top': 1})
        except SAPClientError:
            continue
        rows = data.get('value') or []
        if rows:
            return rows[0]

    return {}


def _fetch_job_card_doc(client: SAPClient, doc_entry: Optional[int]) -> Dict[str, Any]:
    if doc_entry is None:
        return {}
    path = _udo_object_path()
    url = f'{path}({int(doc_entry)})'
    data = client.get(url)
    selected_keys = _csv_list('SAP_JOB_CARD_SELECTED_LINE_KEYS', 'MJD1Collection,MJD1,U_MJD1,MJD1Rows,MJD1LineCollection')
    detail_keys = _csv_list('SAP_JOB_CARD_DETAIL_LINE_KEYS', 'MJD2Collection,MJD2,U_MJD2,MJD2Rows,MJD2LineCollection')
    if _job_card_row_lists_any(data, selected_keys + detail_keys):
        return data
    for nav in selected_keys + detail_keys:
        try:
            expanded = client.get(url, params={'$expand': nav})
        except SAPClientError:
            continue
        if _job_card_row_lists_any(expanded, selected_keys + detail_keys):
            return expanded
    return data


def _normalize_job_card_selected_line(row: Dict[str, Any], client: SAPClient) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    so_fields = _csv_list('SAP_JOB_CARD_HEADER_SO_FIELDS', 'U_SoNo,SoNo,SalesOrder')
    fg_fields = _csv_list('SAP_JOB_CARD_HEADER_FG_FIELDS', 'U_FGCode,U_FG,ItemCode,ItemNo')
    fg_name_fields = _csv_list('SAP_JOB_CARD_HEADER_FG_NAME_FIELDS', 'U_FGName,U_FGDesc,ItemName,ItemDescription,Dscription')
    qty_fields = _csv_list('SAP_JOB_CARD_HEADER_QTY_FIELDS', 'U_DispatchQty,Quantity,PlannedQty')
    ups_fields = _csv_list('SAP_JOB_CARD_HEADER_UPS_FIELDS', 'U_UPS,UPS')
    len_fields = _csv_list('SAP_JOB_CARD_HEADER_LENGTH_FIELDS', 'U_Length,U_CartonLength,Length')
    width_fields = _csv_list('SAP_JOB_CARD_HEADER_WIDTH_FIELDS', 'U_Width,U_CartonWidth,Width')
    height_fields = _csv_list('SAP_JOB_CARD_HEADER_HEIGHT_FIELDS', 'U_Height,U_CartonHeight,Height')

    so_no = _first_text(row, so_fields)
    fg_code = _first_text(row, fg_fields)
    fg_name = _first_text(row, fg_name_fields) or fg_code
    if not fg_code:
        return None, 'missing_fg'

    selected_line: Dict[str, Any] = {
        'so_no': so_no,
        'doc_entry': None,
        'line_num': None,
        'fg_code': fg_code,
        'fg_name': fg_name,
        'ups': _first_float(row, ups_fields) or 1.0,
        'quantity': _first_float(row, qty_fields),
        'carton_length_mm': _first_float(row, len_fields),
        'carton_width_mm': _first_float(row, width_fields),
        'carton_height_mm': _first_float(row, height_fields),
    }

    line_num = _first_int(row, ['LineNum', 'LineNo', 'U_LineNum'])
    qty = _first_float(row, qty_fields)
    ups = _first_float(row, ups_fields)
    if ups is None or ups <= 0:
        ups = 1.0

    resolved_doc_entry: Optional[int] = None
    resolved_line_num: Optional[int] = None
    if so_no:
        ord_open = find_open_order_by_so_doc_num(client, so_no)
        if ord_open:
            try:
                resolved_doc_entry = int(ord_open.get('DocEntry')) if ord_open.get('DocEntry') not in (None, '') else None
            except (TypeError, ValueError):
                resolved_doc_entry = None
            selected_line['card_code'] = (ord_open.get('CardCode') or '').strip() or None
            selected_line['card_name'] = (ord_open.get('CardName') or '').strip() or None
            if resolved_doc_entry is not None:
                try:
                    live_lines = client.fetch_rdr1_fg_lines(resolved_doc_entry)
                except SAPClientError:
                    live_lines = []
                if live_lines:
                    target_tokens = _fg_match_tokens(
                        {},
                        fg_code,
                        fg_name,
                    )
                    candidate_pool: list[dict[str, Any]] = []
                    for live_row in live_lines:
                        live_tokens = _fg_match_tokens(live_row)
                        if target_tokens and not target_tokens.intersection(live_tokens):
                            continue
                        candidate_pool.append(live_row)
                    if not candidate_pool:
                        candidate_pool = live_lines

                    preferred_line = line_num
                    if preferred_line is not None:
                        for live_row in candidate_pool:
                            if _first_int(live_row, ['line_num']) == preferred_line:
                                resolved_line_num = preferred_line
                                break
                    if resolved_line_num is None and qty is not None:
                        qty_matches = []
                        for live_row in candidate_pool:
                            live_qty = _first_float(live_row, ['quantity'])
                            if live_qty is not None and abs(live_qty - qty) <= 0.0001:
                                qty_matches.append(live_row)
                        if len(qty_matches) == 1:
                            resolved_line_num = _first_int(qty_matches[0], ['line_num'])
                    if resolved_line_num is None and candidate_pool:
                        resolved_line_num = _first_int(candidate_pool[0], ['line_num'])
            so_no = str(ord_open.get('DocNum') or so_no)
        else:
            selected_line['so_no'] = so_no
            return selected_line, 'open_so_not_found'

    selected_line['so_no'] = so_no
    selected_line['doc_entry'] = resolved_doc_entry
    selected_line['line_num'] = resolved_line_num
    if resolved_doc_entry is None:
        return selected_line, 'open_so_doc_entry_missing' if so_no else 'missing_so_reference'

    return (
        selected_line,
        None,
    )


def _normalize_job_card_bom_inputs(row: Dict[str, Any]) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    input_keys = _csv_list('SAP_JOB_CARD_DETAIL_INPUT_KEYS', 'Inputs,InputLines,Materials,Components,ChildRows')
    item_fields = _csv_list('SAP_JOB_CARD_DETAIL_INPUT_ITEM_FIELDS', 'ItemCode,U_ItemCode,MaterialCode')
    name_fields = _csv_list('SAP_JOB_CARD_DETAIL_INPUT_NAME_FIELDS', 'ItemName,Description,Dscription')
    qty_fields = _csv_list('SAP_JOB_CARD_DETAIL_INPUT_QTY_FIELDS', 'Quantity,Qty,PlannedQuantity,BaseQuantity')
    wh_fields = _csv_list('SAP_JOB_CARD_DETAIL_INPUT_WAREHOUSE_FIELDS', 'Warehouse,WhsCode')
    for key in input_keys:
        raw = row.get(key)
        if not isinstance(raw, list):
            continue
        for child in raw:
            if not isinstance(child, dict):
                continue
            item_code = _first_text(child, item_fields)
            desc = _first_text(child, name_fields)
            qty = _first_float(child, qty_fields)
            wh = _first_text(child, wh_fields)
            if not item_code and not desc and qty is None and not wh:
                continue
            out.append({
                'sap_item_code': item_code or None,
                'description': desc or item_code or '',
                'uom': _first_text(child, _csv_list('SAP_JOB_CARD_DETAIL_UOM_FIELDS', 'UoM,UOM,MeasureUnit')) or 'PCS',
                'qty_per_job': qty if qty is not None else 0,
                'sap_warehouse': wh or None,
            })
        if out:
            break
    return out


def _normalize_job_card_bom_steps(row: Dict[str, Any], client: SAPClient) -> list[Dict[str, Any]]:
    bom_obj = None
    for key in ('bom', 'Bom', 'BOM'):
        v = row.get(key)
        if isinstance(v, dict):
            bom_obj = v
            break
    if isinstance(bom_obj, dict):
        raw_steps = bom_obj.get('steps') or bom_obj.get('Steps') or bom_obj.get('line_items')
        if not isinstance(raw_steps, list):
            raw_steps = []
        out: list[Dict[str, Any]] = []
        for idx, child in enumerate(raw_steps, start=1):
            step = _normalize_job_card_bom_step(child, idx)
            if step:
                out.append(step)
        if out:
            return out

    owor_entry = _first_int(row, _csv_list('SAP_JOB_CARD_OWOR_LINK_FIELDS', 'U_OWORDocEntry,U_ProdOrderEntry,U_ProdOrderDocEntry,OWORDocEntry'))
    if owor_entry is not None:
        try:
            owor_lines = client.fetch_production_order_lines_raw(owor_entry)
        except SAPClientError:
            owor_lines = []
        out: list[Dict[str, Any]] = []
        for idx, child in enumerate(owor_lines, start=1):
            step = _normalize_job_card_bom_step(child, idx)
            if step:
                out.append(step)
        if out:
            return out

    fallback = _normalize_job_card_bom_step(row, 1)
    return [fallback] if fallback else []


def _normalize_job_card_bom_step(row: Dict[str, Any], seq: int) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    seq_no = _first_int(row, _csv_list('SAP_JOB_CARD_DETAIL_SEQ_FIELDS', 'LineNum,SeqNo,Sequence,StepNo'))
    if seq_no is None:
        seq_no = seq * 10
    process_code = _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_PROCESS_CODE_FIELDS', 'ProcessCode,U_ProcessCode,OperationCode,Code'))
    step_name = _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_STEP_NAME_FIELDS', 'StepName,ProcessName,U_ProcessName,Name,Dscription,ItemName'))
    output_code = _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_OUTPUT_FIELDS', 'OutputItemCode,U_OutputItemCode,ItemCode,ItemNo'))
    qty = _first_float(row, _csv_list('SAP_JOB_CARD_DETAIL_QTY_FIELDS', 'PlannedQuantity,BaseQuantity,Quantity,U_Qty'))
    wh = _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_WAREHOUSE_FIELDS', 'Warehouse,WhsCode'))
    uom = _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_UOM_FIELDS', 'UoM,UOM,MeasureUnit')) or 'PCS'
    remarks = _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_REMARK_FIELDS', 'Remarks,U_Remarks,Comments,ProductionOrderRemarks'))
    inputs = _normalize_job_card_bom_inputs(row)
    if not step_name:
        step_name = process_code or output_code or f'Step {seq}'
    if not process_code:
        process_code = step_name
    if not output_code and inputs:
        output_code = inputs[0].get('sap_item_code') or ''
    return {
        'seq_no': seq_no,
        'process_code': process_code,
        'step_name': step_name,
        'warehouse': wh or None,
        'uom': uom,
        'planned_qty': qty if qty is not None else 0,
        'output_item_code': output_code or None,
        'production_order_remarks': remarks[:254] if remarks else '',
        'inputs': inputs,
    }


def _json_date_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    s = str(value).strip()
    return s or None


def _float_or_none(value: Any) -> Optional[float]:
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    if value in (None, ''):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _rounded_int_or_none(value: Any) -> Optional[int]:
    numeric = _float_or_none(value)
    if numeric is None:
        return None
    return int(round(numeric))


def _clean_for_sap(value: Any) -> Any:
    """Drop empty optional fields while preserving 0 and False."""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            cleaned = _clean_for_sap(v)
            if cleaned is None or cleaned == '':
                continue
            out[k] = cleaned
        return out
    if isinstance(value, list):
        return [_clean_for_sap(v) for v in value]
    return value


def _odata_quote(value: Any) -> str:
    return str(value or '').replace("'", "''")


def _item_name_from_mirror(item_code: Optional[str]) -> Optional[str]:
    code = (item_code or '').strip()
    if not code:
        return None
    try:
        from app.models.sap_mirror import SapItemMirror

        row = SapItemMirror.query.get(code)
        return (row.item_name or '').strip() or None if row else None
    except Exception:
        return None


def _detail_header_lines(job, detail) -> list:
    from app.models.job import JobHeaderLine

    header_lines = list(
        job.header_lines.order_by(None).order_by(JobHeaderLine.line_no).all()
    )
    selected = []
    try:
        for inv in detail.fg_involved.all():
            if inv.header_line:
                selected.append(inv.header_line)
    except Exception:
        selected = []
    if selected:
        return selected
    same_no = [
        hl for hl in header_lines
        if getattr(hl, 'line_no', None) == getattr(detail, 'detail_no', None)
    ]
    return same_no or header_lines


def _detail_total_sheets(job, detail) -> int:
    """Gross sheet quantity for MJD2, including wastage."""
    total = _float_or_none(getattr(detail, 'total_sheets', None))
    wastage = _float_or_none(getattr(detail, 'wastage_sheets', None)) or 0.0
    if total and total > 0:
        return max(0, int(math.ceil(total)))

    net_max = 0.0
    detail_ups = _float_or_none(getattr(detail, 'ups', None))
    if detail_ups is not None and detail_ups <= 0:
        detail_ups = None
    for hl in _detail_header_lines(job, detail):
        qty = _float_or_none(getattr(hl, 'dispatch_qty', None)) or 0.0
        ups = detail_ups or _float_or_none(getattr(hl, 'ups', None)) or 1.0
        if qty > 0 and ups > 0:
            net_max = max(net_max, qty / ups)
    if net_max <= 0:
        return 0
    return int(math.ceil(net_max + wastage))


def _detail_wastage_sheets(detail) -> int:
    """Wastage sheets (rounded up) for MJD2."""
    wastage = _float_or_none(getattr(detail, 'wastage_sheets', None)) or 0.0
    if wastage <= 0:
        return 0
    return int(math.ceil(wastage))


def _detail_ups(job, detail) -> Optional[int]:
    v = _int_or_none(getattr(detail, 'ups', None))
    if v and v > 0:
        return v
    for hl in _detail_header_lines(job, detail):
        hv = _int_or_none(getattr(hl, 'ups', None))
        if hv and hv > 0:
            return hv
    return None


def _first_raw_input_for_step(step):
    from app.models.mfg_bom import BomStepInput

    try:
        inputs = step.inputs.order_by(None).order_by(BomStepInput.id).all()
    except Exception:
        inputs = []
    for inp in inputs:
        if (getattr(inp, 'input_type', '') or '') == 'raw_material':
            return inp
    return inputs[0] if inputs else None


def _mjd2_process_rows_for_detail(job, detail) -> list[dict[str, Any]]:
    """Return exactly one MJD2 row for a saved detail line."""
    total_sheets = _detail_total_sheets(job, detail)
    wastage_sheets = _detail_wastage_sheets(detail)
    ups = _detail_ups(job, detail)
    raw_code = getattr(detail, 'raw_material_item_code', None)
    return [{
        'U_JoEl': getattr(detail, 'element_name', None),
        'U_RaItC': raw_code,
        'U_RaItN': _item_name_from_mirror(raw_code) or raw_code,
        'U_PrSht': total_sheets,
        'U_TotW': total_sheets,  # total sheets including wastage
        'U_WsFor': wastage_sheets,
        'U_NoUps': ups,
        'U_Len': _rounded_int_or_none(getattr(detail, 'sheet_length', None)),
        'U_Width': _rounded_int_or_none(getattr(detail, 'sheet_width', None)),
        'U_GSM': _int_or_none(getattr(detail, 'gsm', None)),
        'U_Grade': getattr(detail, 'paper_brand', None),
        'U_Mill': getattr(detail, 'mill', None),
        'U_PBr': getattr(detail, 'paper_brand', None),
        'U_Front': getattr(detail, 'front_colours', None),
        'U_FrCo': getattr(detail, 'front_colours', None),
        'U_Back': getattr(detail, 'back_colours', None),
        'U_BcCo': getattr(detail, 'back_colours', None),
        'U_Dia': getattr(detail, 'die_no', None),
        'U_SpeIn': getattr(detail, 'special_instructions', None),
        'U_Spas': getattr(detail, 'pasting_style', None),
    }]


def build_omjd_payload(job) -> Dict[str, Any]:
    """Build an OMJD UDO payload from the saved WebApp job card."""
    from app.models.job import JobHeaderLine, JobDetailLine

    today = date.today().isoformat()
    header_lines = list(
        job.header_lines.order_by(None).order_by(JobHeaderLine.line_no).all()
    )
    detail_lines = list(
        job.detail_lines.order_by(None).order_by(JobDetailLine.detail_no).all()
    )
    total_dispatch = sum(
        _float_or_none(getattr(hl, 'dispatch_qty', None)) or 0.0
        for hl in header_lines
    )

    payload: Dict[str, Any] = {
        'U_VerEntry': job.job_no,
        'U_Series': (job.job_series or 'Normal').upper(),
        'U_Status': 'Open',
        'U_Prqty': total_dispatch,
        'U_DocDate': today,
        'U_EJCNo': job.original_job_no,
        'MJD1Collection': [],
        'MJD2Collection': [],
    }

    for idx, hl in enumerate(header_lines):
        payload['MJD1Collection'].append(_clean_for_sap({
            'LineId': idx,
            'U_SoNo': job.sap_so_number_snap,
            'U_FGCode': hl.sap_fg_item_code,
            'U_FGNa': hl.sap_fg_item_name_snap,
            'U_Dqty': _float_or_none(hl.dispatch_qty),
            'U_NoUps': _int_or_none(hl.ups),
            'U_Len': _float_or_none(hl.length),
            'U_Wid': _float_or_none(hl.width),
            'U_Hei': _float_or_none(hl.height),
            'U_PrNa': job.sap_customer_name_snap,
        }))

    mjd2_idx = 0
    for detail in detail_lines:
        for row in _mjd2_process_rows_for_detail(job, detail):
            row['LineId'] = mjd2_idx
            payload['MJD2Collection'].append(_clean_for_sap(row))
            mjd2_idx += 1

    return _clean_for_sap(payload)


def find_omjd_by_ver_entry(client: SAPClient, ver_entry: str) -> Optional[Dict[str, Any]]:
    path = _udo_object_path()
    data = client.get(
        path,
        params={
            '$filter': f"U_VerEntry eq '{_odata_quote(ver_entry)}'",
            '$select': 'DocEntry,U_VerEntry',
            '$top': 2,
        },
    )
    rows = data.get('value') or []
    if len(rows) > 1:
        raise SAPClientError(
            f'Multiple OMJD records found with U_VerEntry={ver_entry!r}; '
            'please resolve duplicates in SAP before updating.'
        )
    return rows[0] if rows else None


def upsert_omjd_job_card(client: SAPClient, job) -> Dict[str, Any]:
    payload = build_omjd_payload(job)
    existing = find_omjd_by_ver_entry(client, job.job_no)
    path = _udo_object_path()
    if existing:
        doc_entry = existing.get('DocEntry')
        if doc_entry in (None, ''):
            raise SAPClientError(f'OMJD U_VerEntry={job.job_no!r} matched without DocEntry.')
        client.patch(
            f'{path}({int(doc_entry)})',
            payload,
            request_headers={'B1S-ReplaceCollectionsOnPatch': 'true'},
        )
        return {'action': 'updated', 'doc_entry': int(doc_entry)}

    resp = client.post(path, payload)
    doc_entry = resp.get('DocEntry') or resp.get('docEntry')
    return {'action': 'created', 'doc_entry': doc_entry}


def fetch_job_card_prefill_payload(
    client: SAPClient,
    doc_num: Any,
    series: Any = None,
) -> Dict[str, Any]:
    """Normalize a SAP job card into the dashboard prefill shape."""
    header_row = _fetch_job_card_header_row(client, doc_num, series)
    if not header_row:
        raise SAPClientError(f'No SAP job card found for doc_num={doc_num!r} series={series!r}')

    doc_entry = _first_int(header_row, ['DocEntry'])
    doc = _fetch_job_card_doc(client, doc_entry)
    if not doc:
        doc = dict(header_row)

    selected_keys = _csv_list('SAP_JOB_CARD_SELECTED_LINE_KEYS', 'MJD1Collection,MJD1,U_MJD1,MJD1Rows,MJD1LineCollection')
    detail_keys = _csv_list('SAP_JOB_CARD_DETAIL_LINE_KEYS', 'MJD2Collection,MJD2,U_MJD2,MJD2Rows,MJD2LineCollection')

    selected_rows = _job_card_row_lists_any(doc, selected_keys)
    if not selected_rows and _mjd1_source() == 'udt':
        selected_rows = [r for r in fetch_mjd1_rows(client) if _job_card_identity_matches(r, doc_num, series)]
    if not selected_rows and _mjd1_source() != 'udt':
        selected_rows = [r for r in fetch_mjd1_rows(client) if _job_card_identity_matches(r, doc_num, series)]

    selected_lines: list[Dict[str, Any]] = []
    unresolved_lines: list[Dict[str, Any]] = []
    for row in selected_rows:
        selected_line, reason = _normalize_job_card_selected_line(row, client)
        if selected_line:
            selected_lines.append(selected_line)
        if reason:
            unresolved_lines.append({
                'reason': reason or 'unresolved',
                'fg_code': (selected_line or {}).get('fg_code') or _first_text(row, _csv_list('SAP_JOB_CARD_HEADER_FG_FIELDS', 'U_FGCode,U_FG,ItemCode,ItemNo')),
                'so_no': (selected_line or {}).get('so_no') or _first_text(row, _csv_list('SAP_JOB_CARD_HEADER_SO_FIELDS', 'U_SoNo,SoNo,SalesOrder')),
            })

    selected_height = _first_float(selected_rows[0], _csv_list('SAP_JOB_CARD_HEADER_HEIGHT_FIELDS', 'U_Hei,U_Height,U_CartonHeight,Height')) if selected_rows else None
    detail_rows = _job_card_row_lists_any(doc, detail_keys)
    if not detail_rows and selected_rows:
        detail_rows = selected_rows[:1]
    detail_lines: list[Dict[str, Any]] = []
    for idx, row in enumerate(detail_rows, start=1):
        if not isinstance(row, dict):
            continue
        if not _job_card_detail_row_has_content(row):
            continue
        element_name = _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_STEP_NAME_FIELDS', 'StepName,ProcessName,U_ProcessName,Name,Dscription,ItemName'))
        raw_material = _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_OUTPUT_FIELDS', 'U_RaItC,U_RaItN,OutputItemCode,U_OutputItemCode,ItemCode,ItemNo'))
        print_style = _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_PRINT_STYLE_FIELDS', 'U_PriSt,U_PrSi')) or None
        print_type = _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_PRINT_TYPE_FIELDS', 'U_Pltty,U_PType')) or None
        if not print_type or print_type.casefold() not in {'metpet', 'conventional', 'na'}:
            con_flag = _first_text(row, ['U_Con'])
            met_flag = _first_text(row, ['U_met'])
            if con_flag and con_flag.casefold() in {'y', 'yes', '1', 'true'}:
                print_type = 'Conventional'
            elif met_flag and met_flag.casefold() in {'y', 'yes', '1', 'true'}:
                print_type = 'MetPet'
        detail_no = len(detail_lines) + 1
        bom_steps = _normalize_job_card_bom_steps(row, client)
        process_names = [
            (step.get('step_name') or step.get('process_code') or '').strip()
            for step in bom_steps
            if isinstance(step, dict) and (step.get('step_name') or step.get('process_code'))
        ]
        detail_lines.append({
            'detail_no': detail_no,
            'element_name': element_name or 'Material',
            'ups': _first_float(row, _csv_list('SAP_JOB_CARD_DETAIL_UPS_FIELDS', 'U_UPS,UPS,Ups'))
            or _first_float(row, _csv_list('SAP_JOB_CARD_HEADER_UPS_FIELDS', 'U_UPS,UPS'))
            or 1,
            'raw_material_item_code': raw_material or None,
            'paper_brand': _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_PAPER_QUALITY_FIELDS', 'U_Grade,U_GRADE,U_PQC')) or None,
            'mill': _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_MILL_FIELDS', 'U_PBr,U_Mill,U_MILL')) or None,
            'total_sheets': _first_float(row, _csv_list('SAP_JOB_CARD_DETAIL_QTY_FIELDS', 'PlannedQuantity,BaseQuantity,Quantity,U_Qty')),
            'paper_supplied_by': 'company',
            'wastage_pct': 0,
            'wastage_sheets': None,
            'sheet_length': _first_float(row, _csv_list('SAP_JOB_CARD_HEADER_LENGTH_FIELDS', 'U_Length,U_CartonLength,Length')),
            'sheet_width': _first_float(row, _csv_list('SAP_JOB_CARD_HEADER_WIDTH_FIELDS', 'U_Width,U_CartonWidth,Width')),
            'sheet_height': _first_float(row, _csv_list('SAP_JOB_CARD_HEADER_HEIGHT_FIELDS', 'U_Hei,U_Height,U_CartonHeight,Height')) or selected_height,
            'gsm': _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_GSM_FIELDS', 'U_GSM,U_Gsm,U_gsm')) or None,
            'print_style': print_style,
            'print_type': print_type,
            'front_colours': _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_FRONT_COLOUR_FIELDS', 'U_Front,U_FRONT')) or None,
            'back_colours': _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_BACK_COLOUR_FIELDS', 'U_Back,U_BACK')) or None,
            'die_no': _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_DIE_NO_FIELDS', 'U_Dia,U_Die,U_DieNo,U_Die_No')) or None,
            'pasting_style': None,
            'special_instructions': _first_text(row, _csv_list('SAP_JOB_CARD_DETAIL_REMARK_FIELDS', 'Remarks,U_Remarks,Comments,ProductionOrderRemarks')) or None,
            'bom': {'steps': bom_steps},
            'process_sequence_names': process_names,
            'process_sequence_outsourcing': [],
        })

    header_title = _first_text(header_row, _csv_list('SAP_JOB_CARD_HEADER_TITLE_FIELDS', 'Name,U_Title,U_JobName,Remarks,Comments'))
    customer_code = _first_text(header_row, _csv_list('SAP_JOB_CARD_HEADER_CUSTOMER_CODE_FIELDS', 'CardCode,U_CardCode,U_CustCode'))
    customer_name = _first_text(header_row, _csv_list('SAP_JOB_CARD_HEADER_CUSTOMER_NAME_FIELDS', 'CardName,U_CustName,U_Customer,U_PrNa')) or customer_name_from_row(header_row)
    if not customer_name and selected_rows:
        customer_name = _first_text(
            selected_rows[0],
            _csv_list('SAP_JOB_CARD_HEADER_CUSTOMER_NAME_FIELDS', 'CardName,U_CustName,U_Customer,U_PrNa'),
        )
    if not customer_code:
        customer_code = _first_text(header_row, [current_app.config.get('SAP_MJD1_FIELD_CARD_CODE') or 'CardCode'])
    if not customer_code and selected_rows:
        customer_code = _first_text(
            selected_rows[0],
            _csv_list('SAP_JOB_CARD_HEADER_CUSTOMER_CODE_FIELDS', 'CardCode,U_CardCode,U_CustCode')
            + [current_app.config.get('SAP_MJD1_FIELD_CARD_CODE') or 'CardCode'],
        )
    if not customer_code and selected_lines:
        customer_code = str(selected_lines[0].get('card_code') or '').strip()
    if not customer_name and selected_lines:
        customer_name = str(selected_lines[0].get('card_name') or '').strip()
    if not customer_code and customer_name:
        try:
            bp = client.fetch_customer_by_name(customer_name)
        except SAPClientError:
            bp = None
        if bp:
            customer_code = (bp.get('CardCode') or '').strip()
            customer_name = (bp.get('CardName') or customer_name or '').strip()

    sap_so_entry = None
    sap_so_number_snap = None
    if selected_lines:
        first_line = selected_lines[0]
        sap_so_entry = first_line.get('doc_entry')
        sap_so_number_snap = first_line.get('so_no')

    if not sap_so_entry:
        fallback_so = _first_text(header_row, _csv_list('SAP_JOB_CARD_HEADER_SO_FIELDS', 'U_SoNo,SoNo,SalesOrder'))
        if fallback_so:
            ord_open = find_open_order_by_so_doc_num(client, fallback_so)
            if ord_open:
                try:
                    sap_so_entry = int(ord_open.get('DocEntry')) if ord_open.get('DocEntry') not in (None, '') else None
                except (TypeError, ValueError):
                    sap_so_entry = None
                sap_so_number_snap = str(ord_open.get('DocNum') or fallback_so)
                if not customer_code:
                    customer_code = (ord_open.get('CardCode') or '').strip()
                if not customer_name:
                    customer_name = (ord_open.get('CardName') or '').strip()

    payload: Dict[str, Any] = {
        'job_no': str(_first_value(header_row, _csv_list('SAP_JOB_CARD_DOCNUM_FIELDS', 'DocNum,DocEntry,Code')) or doc_num),
        'sap_customer_code': customer_code or None,
        'sap_customer_name_snap': customer_name or None,
        'sap_so_entry': sap_so_entry,
        'sap_so_number_snap': sap_so_number_snap,
        'priority': 'normal',
        'job_type_cat': 'Mono',
        'job_series': 'Normal',
        'sap_job_card_doc_entry': doc_entry,
        'sap_job_card_doc_num_snap': str(_first_value(header_row, _csv_list('SAP_JOB_CARD_DOCNUM_FIELDS', 'DocNum,DocEntry,Code')) or doc_num),
        'sap_job_card_series_snap': str(_first_value(header_row, _csv_list('SAP_JOB_CARD_SERIES_FIELDS', 'Series,SeriesCode,U_Series')) or series or ''),
        'sap_job_card_title_snap': header_title or None,
        'header_lines': selected_lines,
        'selected_lines': selected_lines,
        'detail_lines': detail_lines,
        'unresolved_lines': unresolved_lines,
        'warnings': [],
    }
    if unresolved_lines:
        payload['warnings'].append(
            f"{len(unresolved_lines)} SAP job card line(s) could not be matched to an open Sales Order."
        )
    return payload
