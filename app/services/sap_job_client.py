"""sap_client.py — SAP B1 Service Layer API client.

Design:
- By default (``SAP_REUSE_HTTP_SESSION``) one ``requests.Session`` per Service Layer
  identity (URL + company DB + user) is shared across Flask requests so the
  Service Layer session cookie is reused and ``Login`` is not repeated on every
  API call. ``logout()`` is a no-op in pooled mode so cookies stay valid.
- SSL verification is configurable for self-signed SAP certs.
"""
from __future__ import annotations

import logging
import threading
import requests
import urllib3
from typing import Dict, List, Optional, Union

from flask import current_app, g

from app.extensions import db
from app.logging_config import get_logger
from app.models.audit import IntegrationEvent


# Suppress SSL warnings for self-signed SAP certificates in dev
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_log = get_logger('sap')

_sap_pool_lock = threading.Lock()
_sap_http_pool: dict = {}


class _SapPoolEntry:
    """Shared Service Layer HTTP session + login / $expand cache for one SL account."""

    __slots__ = (
        'session',
        'logged_in',
        'orders_lines_expand_nav',
        'orders_lines_via_child_url',
    )

    def __init__(self, verify_ssl: bool):
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.session.headers.update({'Content-Type': 'application/json'})
        self.logged_in = False
        self.orders_lines_expand_nav = None
        self.orders_lines_via_child_url = False


class SAPClientError(Exception):
    """Raised when an SAP Service Layer call fails."""


def _ordr_header_cancelled(cancelled) -> bool:
    """ORDR.Cancelled treated as cancelled when Y or C (per common B1 setups)."""
    if cancelled is None or cancelled == '':
        return False
    s = str(cancelled).strip().upper()
    return s in ('Y', 'C')


def _lines_from_order_payload(data: dict, expand_nav: str) -> list:
    """Line rows from GET /Orders(...) after $expand; keys vary by Service Layer version."""
    if not data:
        return []
    keys_to_try = []
    if expand_nav:
        keys_to_try.append(expand_nav)
        if len(expand_nav) > 1:
            keys_to_try.append(expand_nav[0].lower() + expand_nav[1:])
    keys_to_try.extend(
        ['DocumentLines', 'documentLines', 'OrderLines', 'orderLines', 'Lines', 'lines']
    )
    seen = set()
    for k in keys_to_try:
        if not k or k in seen:
            continue
        seen.add(k)
        v = data.get(k)
        if isinstance(v, list):
            return v
    return []


def _order_line_open_qty(line: dict, field_names: list) -> float:
    """RDR1 open quantity: try OpenCreQty / OpenQuantity / configured names (Service Layer JSON keys)."""
    for name in field_names:
        v = line.get(name)
        if v is None and name:
            # camelCase fallback
            alt = name[0].lower() + name[1:] if len(name) > 1 else name
            v = line.get(alt)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _line_status_is_closed(line: dict) -> bool:
    """Service Layer may use ``LineStatus`` or ``lineStatus``."""
    s = line.get('LineStatus')
    if s is None:
        s = line.get('lineStatus')
    if s is None:
        return False
    return str(s).strip() == 'bost_Close'


def _first_float_value(line: dict, field_names: list[str]) -> Optional[float]:
    """Return the first numeric value found across possible Service Layer field names."""
    for name in field_names:
        v = line.get(name)
        if v is None and name:
            alt = name[0].lower() + name[1:] if len(name) > 1 else name
            v = line.get(alt)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _order_line_document_quantity(line: dict) -> float:
    """Ordered qty on a sales line (Pascal/camel; Service Layer varies)."""
    for k in ('Quantity', 'quantity'):
        v = line.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _lines_from_child_collection_payload(data: dict, nav: str) -> list:
    """Parse GET ``/Orders(id)/DocumentLines`` body — not always OData ``value``."""
    if not isinstance(data, dict):
        return []
    v = data.get('value')
    if isinstance(v, list):
        return v
    for k in (
        nav,
        nav[0].lower() + nav[1:] if nav and len(nav) > 1 else nav,
        'DocumentLines',
        'documentLines',
        'OrderLines',
        'Lines',
        'lines',
    ):
        if not k:
            continue
        x = data.get(k)
        if isinstance(x, list):
            return x
    for x in data.values():
        if isinstance(x, list) and x and isinstance(x[0], dict):
            return x
    return []


def _order_line_counts_open_for_so_list(
    line: dict, field_names: list, qty_fallback: bool
) -> bool:
    """Whether this line keeps the parent Sales Order in the 'open SO' dropdown.

    When ``SAP_ORDER_LINE_OPEN_FALLBACK_QUANTITY`` is true, lines that are not
    ``bost_Close`` and have document quantity > 0 count as open even if open-qty
    fields are present but zero (some Service Layer builds return 0 incorrectly).
    Lines with ``U_JEntry`` populated are excluded.
    """
    if _line_status_is_closed(line):
        return False
    u_jentry = line.get('U_JEntry')
    if u_jentry is not None and str(u_jentry).strip():
        return False
    q = _order_line_open_qty(line, field_names)
    if q > 0:
        return True
    if not qty_fallback:
        return False
    return _order_line_document_quantity(line) > 0


class SAPClient:
    """Thin wrapper around SAP B1 Service Layer REST API."""

    def __init__(self):
        self.base_url = current_app.config['SAP_SERVICE_LAYER_URL'].rstrip('/')
        self.company_db = current_app.config['SAP_COMPANY_DB']
        self.username = current_app.config['SAP_USERNAME']
        self.password = current_app.config['SAP_PASSWORD']
        self.verify_ssl = current_app.config.get('SAP_VERIFY_SSL', False)
        self._pool_entry: Optional[_SapPoolEntry] = None
        self._logged_in = False
        self._orders_lines_expand_nav = None
        self._orders_lines_via_child_url = False
        self._empty_order_lines_logged = False

        reuse = current_app.config.get('SAP_REUSE_HTTP_SESSION', True)
        if reuse:
            key = (self.base_url, self.company_db, self.username, self.verify_ssl)
            with _sap_pool_lock:
                if key not in _sap_http_pool:
                    _sap_http_pool[key] = _SapPoolEntry(self.verify_ssl)
                self._pool_entry = _sap_http_pool[key]
            self._session = self._pool_entry.session
        else:
            self._session = requests.Session()
            self._session.verify = self.verify_ssl
            self._session.headers.update({'Content-Type': 'application/json'})

    def _is_logged_in(self) -> bool:
        if self._pool_entry:
            return self._pool_entry.logged_in
        return self._logged_in

    def _set_logged_in(self, v: bool) -> None:
        if self._pool_entry:
            self._pool_entry.logged_in = v
        else:
            self._logged_in = v

    def _nav_cached(self) -> Optional[str]:
        if self._pool_entry:
            return self._pool_entry.orders_lines_expand_nav
        return self._orders_lines_expand_nav

    def _nav_set_expand(self, nav: str, via_child: bool) -> None:
        if self._pool_entry:
            self._pool_entry.orders_lines_expand_nav = nav
            self._pool_entry.orders_lines_via_child_url = via_child
        else:
            self._orders_lines_expand_nav = nav
            self._orders_lines_via_child_url = via_child

    def _expand_via_child_url(self) -> bool:
        if self._pool_entry:
            return self._pool_entry.orders_lines_via_child_url
        return self._orders_lines_via_child_url

    # ---------------------------------------------------------- auth
    def _timeout(self):
        return int(current_app.config.get('SAP_REQUEST_TIMEOUT') or 120)

    def login(self) -> None:
        """Establish a Service Layer session. Called automatically by _call()."""
        if self._is_logged_in():
            return
        payload = {
            'CompanyDB': self.company_db,
            'UserName': self.username,
            'Password': self.password,
        }
        t = self._timeout()
        try:
            resp = self._session.post(f'{self.base_url}/Login', json=payload, timeout=t)
        except requests.exceptions.RequestException as e:
            raise SAPClientError(f'SAP login connection error: {e}') from e
        if resp.status_code != 200:
            raise SAPClientError(
                f'SAP login failed: {resp.status_code} {resp.text[:200]}'
            )
        self._set_logged_in(True)

    def logout(self) -> None:
        """Close the SAP session (skipped when reusing a pooled HTTP session)."""
        if self._pool_entry:
            return
        try:
            self._session.post(f'{self.base_url}/Logout', timeout=self._timeout())
        except Exception:
            pass
        self._set_logged_in(False)

    # --------------------------------------------------------- internal caller
    def _call(
        self,
        method: str,
        endpoint: str,
        payload: dict = None,
        params: dict = None,
        retry_login: bool = True,
        request_headers: dict = None,
    ) -> dict:
        """Make an authenticated call to Service Layer.

        Auto-logins if not already logged in.
        Retries once on 401 (session expired).
        """
        if not self._is_logged_in():
            self.login()

        url = f'{self.base_url}/{endpoint.lstrip("/")}'
        t = self._timeout()
        req_kw: dict = {
            'method': method.upper(),
            'url': url,
            'json': payload,
            'params': params,
            'timeout': t,
        }
        if request_headers:
            req_kw['headers'] = request_headers
        try:
            resp = self._session.request(**req_kw)
        except requests.exceptions.RequestException as e:
            raise SAPClientError(f'SAP connection error: {e}') from e

        if resp.status_code == 401 and retry_login:
            # Session may have expired — try once more
            self._set_logged_in(False)
            self.login()
            try:
                resp = self._session.request(**req_kw)
            except requests.exceptions.RequestException as e:
                raise SAPClientError(f'SAP connection error: {e}') from e

        if resp.status_code >= 400:
            body = (resp.text or '')[:800]
            _log.error(
                'SAP request failed: %s %s status=%s body=%s',
                method,
                endpoint,
                resp.status_code,
                body,
            )
            raise SAPClientError(
                f'SAP {method} {endpoint} failed: '
                f'{resp.status_code} {resp.text[:500]}'
            )

        return resp.json() if resp.content else {}

    # ------------------------------------------ public API helpers
    def get(self, endpoint: str, params: dict = None) -> dict:
        return self._call('GET', endpoint, params=params)

    def post(self, endpoint: str, payload: dict) -> dict:
        return self._call('POST', endpoint, payload=payload)

    def patch(
        self,
        endpoint: str,
        payload: dict,
        request_headers: dict = None,
    ) -> dict:
        return self._call(
            'PATCH',
            endpoint,
            payload=payload,
            request_headers=request_headers,
        )

    def patch_production_order(
        self,
        doc_entry: int,
        payload: dict,
        *,
        replace_collections: bool = False,
    ) -> dict:
        """PATCH header fields on an existing SAP Production Order.

        ``replace_collections`` sets ``B1S-ReplaceCollectionsOnPatch`` when the
        payload replaces ``ProductionOrderLines`` (SAP B1 Service Layer).
        """
        headers = None
        if replace_collections:
            headers = {'B1S-ReplaceCollectionsOnPatch': 'true'}
        return self.patch(
            f'/ProductionOrders({int(doc_entry)})',
            payload,
            request_headers=headers,
        )

    @staticmethod
    def _so_udf_field_names(env_key: str, default: str) -> list[str]:
        import os
        raw = os.getenv(env_key, default)
        return [x.strip() for x in str(raw).split(',') if x.strip()]

    @staticmethod
    def _line_field_value(ln: dict, field_name: str):
        """Read a line property; Service Layer UDF keys may differ only by case (e.g. ``U_width``)."""
        if not ln or not field_name:
            return None
        if field_name in ln:
            return ln[field_name]
        want = str(field_name).lower()
        for key, val in ln.items():
            if str(key).lower() == want:
                return val
        return None

    @staticmethod
    def _parse_numeric_udf_value(val) -> Optional[float]:
        if val in (None, ''):
            return None
        text = str(val).strip().replace(',', '')
        if not text:
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _line_dimension_float(cls, ln: dict, env_key: str, default: str) -> Optional[float]:
        for field in cls._so_udf_field_names(env_key, default):
            val = cls._line_field_value(ln, field)
            parsed = cls._parse_numeric_udf_value(val)
            if parsed is not None:
                return parsed
        return None

    def patch_sales_order_line_dimensions(
        self,
        doc_entry: int,
        line_updates: List[dict],
    ) -> dict:
        """PATCH RDR1 UDF width/height on open sales order lines."""
        width_fields = self._so_udf_field_names(
            'SAP_JOB_CARD_HEADER_WIDTH_FIELDS',
            'U_width,U_Wid,U_Width,U_CartonWidth,Width',
        )
        height_fields = self._so_udf_field_names(
            'SAP_JOB_CARD_HEADER_HEIGHT_FIELDS',
            'U_Hei,U_Height,U_CartonHeight,Height',
        )
        width_key = width_fields[0] if width_fields else 'U_Width'
        height_key = height_fields[0] if height_fields else 'U_Height'

        updates: list[dict] = []
        for row in line_updates or []:
            if not isinstance(row, dict):
                continue
            try:
                line_num_i = int(row.get('LineNum', row.get('line_num')))
            except (TypeError, ValueError):
                continue
            payload: dict = {'LineNum': line_num_i}
            item_code = (row.get('ItemCode') or row.get('item_code') or '').strip()
            if item_code:
                payload['ItemCode'] = item_code
            width_mm = row.get('width_mm', row.get('carton_width_mm'))
            if width_mm not in (None, ''):
                try:
                    payload[width_key] = int(round(float(width_mm)))
                except (TypeError, ValueError):
                    pass
            height_mm = row.get('height_mm', row.get('carton_height_mm'))
            if height_mm not in (None, ''):
                try:
                    payload[height_key] = int(round(float(height_mm)))
                except (TypeError, ValueError):
                    pass
            if len(payload) <= 1:
                continue
            updates.append(payload)

        if not updates:
            return {}

        return self.patch(
            f'/Orders({int(doc_entry)})',
            {'DocumentLines': updates},
        )

    def patch_sales_order_line_quantities(
        self,
        doc_entry: int,
        line_updates: List[dict],
    ) -> dict:
        """PATCH RDR1 ``Quantity`` on open sales order lines."""
        updates: list[dict] = []
        for row in line_updates or []:
            if not isinstance(row, dict):
                continue
            try:
                line_num_i = int(row.get('LineNum', row.get('line_num')))
            except (TypeError, ValueError):
                continue
            qty = row.get('quantity', row.get('Quantity'))
            if qty in (None, ''):
                continue
            try:
                qty_f = float(str(qty).strip().replace(',', ''))
            except (TypeError, ValueError):
                continue
            payload: dict = {'LineNum': line_num_i, 'Quantity': qty_f}
            item_code = (row.get('ItemCode') or row.get('item_code') or '').strip()
            if item_code:
                payload['ItemCode'] = item_code
            updates.append(payload)

        if not updates:
            return {}

        return self.patch(
            f'/Orders({int(doc_entry)})',
            {'DocumentLines': updates},
        )

    def patch_sales_order_line_job_refs(
        self,
        doc_entry: int,
        line_updates: List[dict],
    ) -> dict:
        """PATCH selected ``RDR1`` lines on a Sales Order with ``U_JEntry`` values.

        SAP Service Layer supports differential PATCH updates on ``/Orders(<DocEntry>)``.
        We include ``LineNum`` and ``ItemCode`` so the target line is unambiguous even when
        the same FG appears multiple times on the same order.
        """
        updates: list[dict] = []
        for row in line_updates or []:
            if not isinstance(row, dict):
                continue
            line_num = row.get('LineNum', row.get('line_num'))
            try:
                line_num_i = int(line_num)
            except (TypeError, ValueError):
                continue
            payload: dict = {'LineNum': line_num_i}
            item_code = (row.get('ItemCode') or row.get('item_code') or '').strip()
            if item_code:
                payload['ItemCode'] = item_code
            uje = (row.get('U_JEntry') or row.get('u_jentry') or '').strip()
            if not uje:
                continue
            payload['U_JEntry'] = uje[:254]
            updates.append(payload)

        if not updates:
            return {}

        return self.patch(
            f'/Orders({int(doc_entry)})',
            {'DocumentLines': updates},
        )

    # ------------------------------------------- domain methods
    @staticmethod
    def _odata_key(card_code: str) -> str:
        """Escape single quotes for OData string keys."""
        return card_code.replace("'", "''")

    def fetch_business_partner(self, card_code: str) -> dict:
        """GET one customer Business Partner by CardCode."""
        k = self._odata_key(card_code)
        return self.get(
            f"/BusinessPartners('{k}')",
            params={
                '$select': (
                    'CardCode,CardName,Phone1,EmailAddress,ContactPerson,'
                    'Address,City,ZipCode,Country'
                ),
            },
        )

    def fetch_customers(self) -> list:
        """GET all active customer Business Partners (paginated).

        Service Layer often caps each response (e.g. 20 rows) regardless of $top;
        advance $skip by len(batch) until a batch is empty — do not stop when len(batch) < $top.
        """
        out: list = []
        skip = 0
        page = 500
        while True:
            data = self.get(
                '/BusinessPartners',
                params={
                    '$filter': "CardType eq 'cCustomer' and Valid eq 'tYES'",
                    '$select': 'CardCode,CardName,Phone1,EmailAddress',
                    '$top': page,
                    '$skip': skip,
                },
            )
            batch = data.get('value') or []
            if not batch:
                break
            out.extend(batch)
            skip += len(batch)
            if skip > 100000:
                break
        return out

    def fetch_customer_by_name(self, card_name: str) -> Optional[dict]:
        """Return the first active customer Business Partner with an exact CardName match."""
        name = (card_name or '').strip()
        if not name:
            return None
        k = self._odata_key(name)
        data = self.get(
            '/BusinessPartners',
            params={
                '$filter': f"CardType eq 'cCustomer' and Valid eq 'tYES' and CardName eq '{k}'",
                '$select': 'CardCode,CardName',
                '$top': 1,
            },
        )
        rows = data.get('value') or []
        return rows[0] if rows else None

    def fetch_business_partners_by_series(self, series: int) -> list:
        """OCRD via Service Layer: customer-type BPs with given numbering Series (e.g. 1).

        Paginate until an empty batch; SAP may return only ~20 rows per call even if $top is larger.
        """
        out: list = []
        skip = 0
        page = 500
        s = int(series)
        while True:
            data = self.get(
                '/BusinessPartners',
                params={
                    '$filter': (
                        f"CardType eq 'cCustomer' and Valid eq 'tYES' and Series eq {s}"
                    ),
                    '$select': 'CardCode,CardName,Series',
                    '$orderby': 'CardName',
                    '$top': page,
                    '$skip': skip,
                },
            )
            batch = data.get('value') or []
            if not batch:
                break
            out.extend(batch)
            skip += len(batch)
            if skip > 100000:
                break
        return out

    def fetch_open_sales_orders(self, card_code: str) -> list:
        """GET open Sales Orders for a customer (CardCode)."""
        k = self._odata_key(card_code)
        filt = f"CardCode eq '{k}' and DocumentStatus eq 'bost_Open'"
        data = self.get(
            '/Orders',
            params={
                '$filter': filt,
                '$select': (
                    'DocEntry,DocNum,DocDate,DocDueDate,CardCode,CardName,'
                    'DocumentStatus'
                ),
                '$orderby': 'DocDate desc',
                '$top': 200,
            },
        )
        return data.get('value', [])

    def _resolve_order_lines_expand_navigation(self) -> str:
        """Pick how to read /Orders lines: $expand on header, or child collection URL.

        Some Service Layer builds reject ``$expand=DocumentLines`` on ``/Orders`` (error 201,
        entity type ``Document``) but still allow ``GET /Orders(DocEntry)/DocumentLines``.
        """
        cached = self._nav_cached()
        if cached is not None:
            return cached
        cfg = current_app.config
        raw = (cfg.get('SAP_ORDER_LINES_EXPAND_NAV') or '').strip()
        if raw:
            candidates = [x.strip() for x in raw.split(',') if x.strip()]
        else:
            candidates = [
                'DocumentLines',
                'OrderLines',
                'Lines',
                'documentLines',
                'orderLines',
                'lines',
            ]
        last_err = None
        for nav in candidates:
            try:
                self.get('/Orders', params={'$top': 1, '$expand': nav})
                self._nav_set_expand(nav, False)
                _log.info('SAP /Orders line $expand: using %r', nav)
                return nav
            except SAPClientError as e:
                last_err = e
                msg = str(e).lower()
                if 'invalid navigation property' in msg:
                    continue
                if 'code: 201' in msg or '"code" : 201' in str(e):
                    continue
                raise
        # $expand unsupported — try OData child collection /Orders(DocEntry)/<nav>
        try:
            probe = self.get('/Orders', params={'$top': 1, '$orderby': 'DocEntry'})
            rows = probe.get('value') or []
            if not rows:
                raise SAPClientError(
                    'Cannot probe sales order lines: no rows in /Orders. '
                    f'$expand failed ({last_err}).'
                ) from last_err
            de = rows[0].get('DocEntry')
            if de is None:
                raise SAPClientError(
                    'Cannot probe sales order lines: /Orders row has no DocEntry.'
                ) from last_err
            for nav in candidates:
                try:
                    self.get(f'/Orders({int(de)})/{nav}', params={'$top': 1})
                    self._nav_set_expand(nav, True)
                    _log.info(
                        'SAP /Orders lines: $expand not available; using child URL /Orders(...)/%r',
                        nav,
                    )
                    return nav
                except SAPClientError as e:
                    last_err = e
                    msg = str(e).lower()
                    if '404' in msg or 'not found' in msg:
                        continue
                    if 'invalid' in msg and 'navigation' in msg:
                        continue
                    if 'code: 201' in msg or '"code" : 201' in str(e):
                        continue
                    raise
        except SAPClientError:
            raise
        except Exception as e:
            raise SAPClientError(f'Failed to probe /Orders line child URL: {e}') from e
        raise SAPClientError(
            f'Could not read sales order lines: $expand and child URL failed for {candidates!r}. '
            f'Check Service Layer $metadata or QueryService_PostQuery. Last error: {last_err}'
        ) from last_err

    def _get_order_lines_via_child_collection(self, doc_entry: int, nav: str) -> list:
        """GET line rows from ``/Orders(DocEntry)/<nav>`` (paginated)."""
        out: list = []
        skip = 0
        page = 200
        while True:
            data = self.get(
                f'/Orders({int(doc_entry)})/{nav}',
                params={'$top': page, '$skip': skip},
            )
            batch = _lines_from_child_collection_payload(data, nav)
            out.extend(batch)
            if len(batch) < page:
                break
            skip += len(batch)
            if skip > 100000:
                break
        return out

    def _fetch_open_sales_orders_ordr_list_expand(
        self,
        filt: str,
        qty_fields: list,
        filter_cancelled_yc: bool,
        expand_nav: str,
        qty_fallback: bool,
        skip_line_filter: bool,
    ) -> list:
        """Paginated GET /Orders with $expand on line collection (single round-trip per page)."""
        out: list = []
        skip = 0
        page = 100
        while True:
            data = self.get(
                '/Orders',
                params={
                    '$filter': filt,
                    '$select': (
                        'DocEntry,DocNum,DocDate,DocDueDate,CardCode,CardName,'
                        'DocumentStatus,Cancelled'
                    ),
                    '$expand': expand_nav,
                    '$orderby': 'DocDate desc',
                    '$top': page,
                    '$skip': skip,
                },
            )
            batch = data.get('value') or []
            if not batch:
                if skip == 0:
                    _log.info(
                        'SAP open SO: GET /Orders ($expand) returned no rows for $filter=%s',
                        filt,
                    )
                break
            for order in batch:
                if filter_cancelled_yc and _ordr_header_cancelled(order.get('Cancelled')):
                    continue
                if skip_line_filter:
                    doc_num = order.get('DocNum')
                    out.append({
                        'so_no': str(doc_num) if doc_num is not None else '',
                        'doc_entry': order.get('DocEntry'),
                        'doc_num': doc_num,
                        'doc_date': order.get('DocDate'),
                        'doc_due_date': order.get('DocDueDate'),
                        'card_code': order.get('CardCode'),
                        'card_name': order.get('CardName'),
                    })
                    continue
                lines = _lines_from_order_payload(order, expand_nav)
                if not any(
                    _order_line_counts_open_for_so_list(ln, qty_fields, qty_fallback)
                    for ln in lines
                ):
                    continue
                doc_num = order.get('DocNum')
                out.append({
                    'so_no': str(doc_num) if doc_num is not None else '',
                    'doc_entry': order.get('DocEntry'),
                    'doc_num': doc_num,
                    'doc_date': order.get('DocDate'),
                    'doc_due_date': order.get('DocDueDate'),
                    'card_code': order.get('CardCode'),
                    'card_name': order.get('CardName'),
                })
            skip += len(batch)
            if len(batch) < page or skip > 100000:
                break
        return out

    def _fetch_open_sales_orders_ordr_nplus_one(
        self,
        filt: str,
        qty_fields: list,
        filter_cancelled_yc: bool,
        expand_nav: str,
        qty_fallback: bool,
        skip_line_filter: bool,
    ) -> list:
        """Fallback: list Orders without expand, then GET each Order with line collection."""
        out: list = []
        skip = 0
        page = 100
        while True:
            data = self.get(
                '/Orders',
                params={
                    '$filter': filt,
                    '$select': (
                        'DocEntry,DocNum,DocDate,DocDueDate,CardCode,CardName,'
                        'DocumentStatus,Cancelled'
                    ),
                    '$orderby': 'DocDate desc',
                    '$top': page,
                    '$skip': skip,
                },
            )
            batch = data.get('value') or []
            if not batch:
                if skip == 0:
                    _log.info('SAP open SO: GET /Orders returned no rows for $filter=%s', filt)
                break
            for order in batch:
                if filter_cancelled_yc and _ordr_header_cancelled(order.get('Cancelled')):
                    continue
                de = order.get('DocEntry')
                if de is None:
                    continue
                if skip_line_filter:
                    doc_num = order.get('DocNum')
                    out.append({
                        'so_no': str(doc_num) if doc_num is not None else '',
                        'doc_entry': order.get('DocEntry'),
                        'doc_num': doc_num,
                        'doc_date': order.get('DocDate'),
                        'doc_due_date': order.get('DocDueDate'),
                        'card_code': order.get('CardCode'),
                        'card_name': order.get('CardName'),
                    })
                    continue
                if self._expand_via_child_url():
                    lines = self._get_order_lines_via_child_collection(int(de), expand_nav)
                else:
                    detail = self.get(
                        f'/Orders({int(de)})',
                        params={'$expand': expand_nav},
                    )
                    lines = _lines_from_order_payload(detail, expand_nav)
                if not lines and not self._empty_order_lines_logged:
                    self._empty_order_lines_logged = True
                    _log.info(
                        'SAP open SO: DocEntry=%s has no lines from %r — '
                        'set SAP_ORDER_LIST_SKIP_LINE_FILTER=true to show SO headers anyway',
                        int(de),
                        expand_nav,
                    )
                if not any(
                    _order_line_counts_open_for_so_list(ln, qty_fields, qty_fallback)
                    for ln in lines
                ):
                    continue
                doc_num = order.get('DocNum')
                out.append({
                    'so_no': str(doc_num) if doc_num is not None else '',
                    'doc_entry': order.get('DocEntry'),
                    'doc_num': doc_num,
                    'doc_date': order.get('DocDate'),
                    'doc_due_date': order.get('DocDueDate'),
                    'card_code': order.get('CardCode'),
                    'card_name': order.get('CardName'),
                })
            skip += len(batch)
            if len(batch) < page or skip > 100000:
                break
        return out

    def _fetch_open_sales_orders_ordr_headers_only(
        self, filt: str, filter_cancelled_yc: bool
    ) -> list:
        """GET /Orders with ``$filter`` only — no ``$expand`` or line probes (fast path)."""
        out: list = []
        skip = 0
        page = 100
        while True:
            data = self.get(
                '/Orders',
                params={
                    '$filter': filt,
                    '$select': (
                        'DocEntry,DocNum,DocDate,DocDueDate,CardCode,CardName,'
                        'DocumentStatus,Cancelled'
                    ),
                    '$orderby': 'DocDate desc',
                    '$top': page,
                    '$skip': skip,
                },
            )
            batch = data.get('value') or []
            if not batch:
                if skip == 0:
                    _log.info(
                        'SAP open SO: GET /Orders returned no rows for $filter=%s',
                        filt,
                    )
                break
            for order in batch:
                if filter_cancelled_yc and _ordr_header_cancelled(order.get('Cancelled')):
                    continue
                doc_num = order.get('DocNum')
                out.append({
                    'so_no': str(doc_num) if doc_num is not None else '',
                    'doc_entry': order.get('DocEntry'),
                    'doc_num': doc_num,
                    'doc_date': order.get('DocDate'),
                    'doc_due_date': order.get('DocDueDate'),
                    'card_code': order.get('CardCode'),
                    'card_name': order.get('CardName'),
                })
            skip += len(batch)
            if len(batch) < page or skip > 100000:
                break
        return out

    def fetch_recent_open_sales_orders(self, limit: int = 20) -> list:
        """Recent open sales order headers from SAP (all customers)."""
        cfg = current_app.config
        parts = []
        if cfg.get('SAP_ORDER_FILTER_DOCUMENT_STATUS_OPEN', True):
            parts.append("DocumentStatus eq 'bost_Open'")
        filt = ' and '.join(parts) if parts else None
        filter_cancelled_yc = cfg.get('SAP_ORDER_FILTER_CANCELLED_YC', True)
        params = {
            '$select': (
                'DocEntry,DocNum,DocDate,DocDueDate,CardCode,CardName,'
                'DocumentStatus,Cancelled'
            ),
            '$orderby': 'DocEntry desc',
            '$top': max(1, min(int(limit), 100)),
        }
        if filt:
            params['$filter'] = filt
        data = self.get('/Orders', params=params)
        out = []
        for order in data.get('value') or []:
            if filter_cancelled_yc and _ordr_header_cancelled(order.get('Cancelled')):
                continue
            doc_num = order.get('DocNum')
            out.append({
                'so_no': str(doc_num) if doc_num is not None else '',
                'doc_entry': order.get('DocEntry'),
                'doc_num': doc_num,
                'doc_date': order.get('DocDate'),
                'doc_due_date': order.get('DocDueDate'),
                'card_code': order.get('CardCode'),
                'card_name': order.get('CardName'),
            })
        return out

    def fetch_production_orders_recent(self, limit: int = 25) -> list:
        """Latest production orders from SAP Service Layer."""
        lim = max(1, min(int(limit), 100))
        data = self.get(
            '/ProductionOrders',
            params={
                '$select': (
                    'AbsoluteEntry,DocumentNumber,ItemNo,ProductDescription,'
                    'PlannedQuantity,ProductionOrderStatus,Warehouse,'
                    'PostingDate,DueDate'
                ),
                '$orderby': 'AbsoluteEntry desc',
                '$top': lim,
            },
        )
        out = []
        for row in data.get('value') or []:
            status_raw = row.get('ProductionOrderStatus') or row.get('productionOrderStatus')
            out.append({
                'doc_entry': row.get('AbsoluteEntry'),
                'doc_num': row.get('DocumentNumber'),
                'item_no': row.get('ItemNo'),
                'item_name': (row.get('ProductDescription') or '')[:120],
                'planned_qty': row.get('PlannedQuantity'),
                'status': self._normalize_production_order_status(status_raw) or str(status_raw or ''),
                'warehouse': row.get('Warehouse'),
                'posting_date': row.get('PostingDate'),
                'due_date': row.get('DueDate'),
            })
        return out

    def fetch_open_sales_orders_ordr(self, card_code: str) -> list:
        """Open Sales Orders for a Business Partner (ORDR + RDR1 semantics).

        - Filter by ``CardCode`` (and optionally ``DocumentStatus`` = ``bost_Open``).
        - Exclude headers where ``Cancelled`` is ``Y`` or ``C`` (configurable).
        - Keep orders that have at least one line with open quantity > 0, using
          ``OpenCreQty`` / ``OpenQuantity`` / ``SAP_ORDER_LINE_OPEN_QTY_FIELDS``.

        This mirrors: ORDR INNER JOIN RDR1 ON DocEntry with line open qty > 0 and
        ``Cancelled`` not in (Y, C).
        """
        cfg = current_app.config
        self._empty_order_lines_logged = False
        k = self._odata_key(card_code)
        parts = [f"CardCode eq '{k}'"]
        if cfg.get('SAP_ORDER_FILTER_DOCUMENT_STATUS_OPEN', True):
            parts.append("DocumentStatus eq 'bost_Open'")
        filt = ' and '.join(parts)

        qty_fields = [
            x.strip()
            for x in (cfg.get('SAP_ORDER_LINE_OPEN_QTY_FIELDS') or 'OpenCreQty,OpenQuantity').split(',')
            if x.strip()
        ]
        filter_cancelled_yc = cfg.get('SAP_ORDER_FILTER_CANCELLED_YC', True)
        qty_fallback = cfg.get('SAP_ORDER_LINE_OPEN_FALLBACK_QUANTITY', True)
        skip_line_filter = cfg.get('SAP_ORDER_LIST_SKIP_LINE_FILTER', True)

        if skip_line_filter:
            out = self._fetch_open_sales_orders_ordr_headers_only(filt, filter_cancelled_yc)
        else:
            expand_nav = self._resolve_order_lines_expand_navigation()
            if self._expand_via_child_url():
                out = self._fetch_open_sales_orders_ordr_nplus_one(
                    filt, qty_fields, filter_cancelled_yc, expand_nav, qty_fallback, skip_line_filter
                )
            else:
                try:
                    out = self._fetch_open_sales_orders_ordr_list_expand(
                        filt, qty_fields, filter_cancelled_yc, expand_nav, qty_fallback, skip_line_filter
                    )
                except SAPClientError as e:
                    _log.warning(
                        'GET /Orders with $expand=%s failed; using per-order GET: %s',
                        expand_nav,
                        e,
                    )
                    out = self._fetch_open_sales_orders_ordr_nplus_one(
                        filt, qty_fields, filter_cancelled_yc, expand_nav, qty_fallback, skip_line_filter
                    )
        if not out:
            _log.info(
                'SAP open SO: 0 orders for CardCode=%r ($filter=%s). '
                'If SAP shows open orders, set SAP_ORDER_FILTER_DOCUMENT_STATUS_OPEN=false '
                'or check line Quantity / LineStatus in Service Layer.',
                card_code,
                filt,
            )
        return out

    def fetch_order_with_lines(self, doc_entry: int) -> dict:
        """GET one Sales Order with document lines for FG / qty selection."""
        expand_nav = self._resolve_order_lines_expand_navigation()
        if self._expand_via_child_url():
            data = self.get(
                f'/Orders({doc_entry})',
                params={
                    '$select': 'DocEntry,DocNum,DocDate,DocDueDate,CardCode,CardName',
                },
            )
            raw_lines = self._get_order_lines_via_child_collection(doc_entry, expand_nav)
        else:
            data = self.get(
                f'/Orders({doc_entry})',
                params={
                    '$select': 'DocEntry,DocNum,DocDate,DocDueDate,CardCode,CardName',
                    '$expand': expand_nav,
                },
            )
            raw_lines = _lines_from_order_payload(data, expand_nav)
        lines = []
        for ln in raw_lines:
            if _line_status_is_closed(ln):
                continue
            item_code = ln.get('ItemCode')
            if not item_code:
                continue
            lines.append({
                'line_num': ln.get('LineNum'),
                'item_code': item_code,
                'item_name': ln.get('ItemDescription') or ln.get('ItemName') or '',
                'quantity': ln.get('Quantity'),
                'uom': ln.get('MeasureUnit') or ln.get('UoMCode') or '',
            })
        return {
            'doc_entry': data.get('DocEntry'),
            'doc_num': data.get('DocNum'),
            'doc_due_date': data.get('DocDueDate'),
            'card_code': data.get('CardCode'),
            'card_name': data.get('CardName'),
            'lines': lines,
        }

    def fetch_order_header_for_print(self, doc_entry: int) -> dict:
        """GET Sales Order header fields used for printing slips."""
        return self.get(
            f'/Orders({int(doc_entry)})',
            params={
                '$select': 'DocEntry,DocNum,DocDate,DocDueDate,CardCode,CardName,SalesPersonCode',
            },
        )

    def fetch_latest_doc_entry_for_items(
        self,
        item_codes: List[str],
        *,
        open_orders_only: Optional[bool] = None,
        scan_limit: Optional[int] = None,
    ) -> Optional[int]:
        """Highest ``DocEntry`` among orders (within ``scan_limit``) whose lines include any item code.

        Used when the same FG appears on multiple open Sales Orders: pick the latest
        (highest) DocEntry, then read ``SalesPersonCode`` from that order header.
        """
        targets = {(c or '').strip().upper() for c in (item_codes or []) if (c or '').strip()}
        if not targets:
            return None

        try:
            lim = int(scan_limit if scan_limit is not None else (current_app.config.get('SAP_PRINT_SLP_SCAN_LIMIT') or 500))
        except (TypeError, ValueError):
            lim = 500
        open_only = (
            open_orders_only
            if open_orders_only is not None
            else bool(current_app.config.get('SAP_PRINT_SLP_OPEN_ONLY', True))
        )

        filt = []
        if open_only:
            filt.append("DocumentStatus eq 'bost_Open'")
        filter_str = ' and '.join(filt) if filt else None

        best_de: Optional[int] = None
        skip = 0
        page = min(100, max(1, lim))

        def _ic_norm(code) -> str:
            return (str(code or '')).strip().upper()

        while skip < lim:
            params = {
                '$select': 'DocEntry,DocNum',
                '$orderby': 'DocEntry desc',
                '$top': page,
                '$skip': skip,
            }
            if filter_str:
                params['$filter'] = filter_str
            data = self.get('/Orders', params=params)
            orders = data.get('value') or []
            if not orders:
                break
            for o in orders:
                de = o.get('DocEntry')
                if de is None:
                    continue
                try:
                    raw_lines = self.fetch_order_lines_raw(int(de))
                except Exception:
                    continue
                hit = any(_ic_norm(ln.get('ItemCode')) in targets for ln in (raw_lines or []))
                if hit:
                    di = int(de)
                    best_de = di if best_de is None else max(best_de, di)
            skip += len(orders)
            if len(orders) < page:
                break

        return best_de

    def fetch_order_lines_raw(self, doc_entry: int) -> List[dict]:
        """GET Sales Order raw line dicts (includes UDFs) for artwork lookup."""
        expand_nav = self._resolve_order_lines_expand_navigation()
        if self._expand_via_child_url():
            # First ensure header exists (some SL builds require it before child collection)
            self.get(f'/Orders({int(doc_entry)})', params={'$select': 'DocEntry,DocNum'})
            raw_lines = self._get_order_lines_via_child_collection(int(doc_entry), expand_nav)
        else:
            data = self.get(f'/Orders({int(doc_entry)})', params={'$expand': expand_nav})
            raw_lines = _lines_from_order_payload(data, expand_nav)
        return [ln for ln in (raw_lines or []) if isinstance(ln, dict)]

    def fetch_salesperson_name(self, sales_person_code: Optional[Union[int, str]]) -> Optional[str]:
        """Resolve Sales Rep name from SalesPersonCode via Service Layer.

        Service Layer entity is usually /SalesPersons (OSLP).
        """
        if sales_person_code in (None, ''):
            return None
        try:
            code_i = int(str(sales_person_code).strip())
        except (TypeError, ValueError):
            return None
        ent = (current_app.config.get('SAP_SALESPERSON_ENTITY') or 'SalesPersons').strip() or 'SalesPersons'

        # Try key lookup first
        try:
            row = self.get(f'/{ent}({code_i})', params={'$select': 'SalesEmployeeCode,SalesEmployeeName,SlpCode,SlpName'})
            name = row.get('SalesEmployeeName') or row.get('SlpName') or row.get('SalesPersonName')
            return (str(name).strip() or None) if name is not None else None
        except SAPClientError:
            pass

        # Fallback query lookup (field names vary)
        for fld in ('SalesEmployeeCode', 'SlpCode', 'SalesPersonCode'):
            try:
                data = self.get(
                    f'/{ent}',
                    params={
                        '$filter': f'{fld} eq {code_i}',
                        '$select': 'SalesEmployeeCode,SalesEmployeeName,SlpCode,SlpName,SalesPersonName',
                        '$top': 1,
                    },
                )
                vals = data.get('value') or []
                if not vals:
                    continue
                row = vals[0]
                name = row.get('SalesEmployeeName') or row.get('SlpName') or row.get('SalesPersonName')
                return (str(name).strip() or None) if name is not None else None
            except SAPClientError:
                continue
        return None

    def fetch_rdr1_fg_lines(self, doc_entry: int) -> list:
        """RDR1 lines for a sales order (Service Layer ``DocumentLines``): FG code, description, quantity.

        Maps DB columns: ``ItemCode``, ``Dscription``, ``Quantity``.
        RDR1 ``SubCatNum`` is exposed by Service Layer as ``SupplierCatNum`` on this install.
        """
        expand_nav = self._resolve_order_lines_expand_navigation()
        if self._expand_via_child_url():
            self.get(
                f'/Orders({int(doc_entry)})',
                params={'$select': 'DocEntry,DocNum'},
            )
            raw_lines = self._get_order_lines_via_child_collection(int(doc_entry), expand_nav)
        else:
            data = self.get(
                f'/Orders({int(doc_entry)})',
                params={
                    '$select': 'DocEntry,DocNum,DocDate,DocDueDate,CardCode,CardName',
                    '$expand': expand_nav,
                },
            )
            raw_lines = _lines_from_order_payload(data, expand_nav)
        out: list = []
        for ln in raw_lines:
            if _line_status_is_closed(ln):
                continue
            item_code = ln.get('ItemCode')
            if not item_code:
                continue
            dscription = (
                ln.get('Dscription')
                or ln.get('ItemDescription')
                or ln.get('ItemName')
                or ''
            )
            qty = _order_line_document_quantity(ln)
            line_num = ln.get('LineNum')
            supplier_cat_num = (
                ln.get('SupplierCatNum')
                or ln.get('supplierCatNum')
                or ln.get('SubCatNum')
                or ln.get('subCatNum')
                or ''
            )
            width_mm = self._line_dimension_float(
                ln,
                'SAP_JOB_CARD_HEADER_WIDTH_FIELDS',
                'U_width,U_Wid,U_WID,U_Width,U_WIDTH,U_CartonWidth,Width,CartonWidth',
            )
            height_mm = self._line_dimension_float(
                ln,
                'SAP_JOB_CARD_HEADER_HEIGHT_FIELDS',
                'U_Hei,U_Height,U_CartonHeight,Height',
            )
            out.append({
                'line_num': line_num,
                'fg_code': item_code,
                'fg_name': dscription,
                'supplier_cat_num': str(supplier_cat_num).strip() if supplier_cat_num is not None else '',
                'quantity': qty,
                'dispatch_qty': qty,
                'sap_quantity': qty,
                'carton_width_mm': width_mm,
                'carton_height_mm': height_mm,
                'sap_carton_width_mm': width_mm,
                'sap_carton_height_mm': height_mm,
            })
        return out

    @staticmethod
    def _item_is_active_row(item: dict) -> bool:
        """OITM / Items: Service Layer ``Valid`` — only ``tYES`` counts as active."""
        v = item.get('Valid')
        if v is None or v == '':
            return True
        s = str(v).strip().upper()
        if s == 'TNO':
            return False
        return s in ('TYES', 'YES')

    def fetch_items(self, item_type_filter: str = None) -> list:
        """GET items from SAP Item Master (OITM via Service Layer ``/Items``).

        Only **active** items are returned (``Valid eq 'tYES'``) so the local mirror
        matches usable OITM rows for BOM / raw-material pickers.

        item_type_filter: 'it_FixedAssets' | 'it_Items' | etc.
        """
        out: list = []
        skip = 0
        page = 500
        select = 'ItemCode,ItemName,ItemType,SalesUnit,ItemsGroupCode,Valid,DefaultWarehouse'
        active_f = "Valid eq 'tYES'"
        while True:
            params = {
                '$select': select,
                '$top': page,
                '$skip': skip,
                '$orderby': 'ItemCode',
            }
            if item_type_filter:
                params['$filter'] = f"(ItemType eq '{item_type_filter}') and ({active_f})"
            else:
                params['$filter'] = active_f
            data = self.get('/Items', params=params)
            batch = data.get('value') or []
            if not batch:
                break
            out.extend(batch)
            # SAP SL often ignores $top and returns ~20 rows; always advance by actual batch size.
            skip += len(batch)
            if skip > 500000:
                break
        return out

    def create_production_order(self, payload: dict) -> dict:
        """POST a Production Order to SAP.

        Returns the full SAP response including DocEntry.
        """
        return self.post('/ProductionOrders', payload)

    @staticmethod
    def _normalize_production_order_status(raw) -> str:
        """Map Service Layer status to ``planned`` | ``released`` | ``closed`` | ``cancelled`` | ````."""
        if raw is None or raw == '':
            return ''
        s = str(raw).strip().upper()
        if 'CLOSED' in s or s.endswith('CLOSED'):
            return 'closed'
        if 'CANCEL' in s:
            return 'cancelled'
        if 'RELEASED' in s or s.endswith('RELEASED'):
            return 'released'
        if 'PLANN' in s:  # Planned / Planning
            return 'planned'
        return ''

    def _fetch_production_order_status(self, doc_entry: int) -> str:
        try:
            dto = self.get(
                f'/ProductionOrders({int(doc_entry)})',
                params={'$select': 'ProductionOrderStatus'},
            )
        except SAPClientError:
            return ''
        raw = dto.get('ProductionOrderStatus') if isinstance(dto, dict) else None
        if raw is None and isinstance(dto, dict):
            raw = dto.get('productionOrderStatus')
        return self._normalize_production_order_status(raw)

    def _resolve_production_order_lines_navigation(self) -> tuple[str, bool]:
        """Pick how to read /ProductionOrders lines: $expand or child collection URL."""
        candidates = [
            'ProductionOrderLines',
            'productionOrderLines',
            'Lines',
            'lines',
        ]
        last_err = None
        for nav in candidates:
            try:
                self.get('/ProductionOrders', params={'$top': 1, '$expand': nav})
                return nav, False
            except SAPClientError as e:
                last_err = e
                msg = str(e).lower()
                if 'invalid navigation property' in msg:
                    continue
                if 'code: 201' in msg or '"code" : 201' in str(e):
                    continue
                raise

        try:
            probe = self.get('/ProductionOrders', params={'$top': 1, '$orderby': 'AbsoluteEntry'})
            rows = probe.get('value') or []
            if not rows:
                raise SAPClientError(
                    'Cannot probe production order lines: no rows in /ProductionOrders. '
                    f'$expand failed ({last_err}).'
                ) from last_err
            de = rows[0].get('AbsoluteEntry')
            if de is None:
                de = rows[0].get('DocEntry')
            if de is None:
                raise SAPClientError(
                    'Cannot probe production order lines: /ProductionOrders row has no AbsoluteEntry.'
                ) from last_err
            for nav in candidates:
                try:
                    self.get(f'/ProductionOrders({int(de)})/{nav}', params={'$top': 1})
                    return nav, True
                except SAPClientError as e:
                    last_err = e
                    msg = str(e).lower()
                    if '404' in msg or 'not found' in msg:
                        continue
                    if 'invalid' in msg and 'navigation' in msg:
                        continue
                    if 'code: 201' in msg or '"code" : 201' in str(e):
                        continue
                    raise
        except SAPClientError:
            raise
        except Exception as e:
            raise SAPClientError(f'Failed to probe /ProductionOrders line child URL: {e}') from e

        raise SAPClientError(
            f'Could not read production order lines: $expand and child URL failed for {candidates!r}. '
            f'Last error: {last_err}'
        ) from last_err

    def fetch_production_order_lines_raw(self, doc_entry: int) -> List[dict]:
        """GET production-order line dicts (includes UDFs / issued quantity fields)."""
        nav, via_child = self._resolve_production_order_lines_navigation()
        if via_child:
            self.get(f'/ProductionOrders({int(doc_entry)})', params={'$select': 'AbsoluteEntry'})
            data = self.get(f'/ProductionOrders({int(doc_entry)})/{nav}', params={'$top': 500})
            raw_lines = data.get('value') if isinstance(data, dict) else None
            if not isinstance(raw_lines, list):
                raw_lines = []
        else:
            data = self.get(
                f'/ProductionOrders({int(doc_entry)})',
                params={'$expand': nav},
            )
            raw_lines = []
            if isinstance(data, dict):
                for k in (nav, nav[0].lower() + nav[1:] if len(nav) > 1 else nav, 'ProductionOrderLines', 'productionOrderLines', 'Lines', 'lines'):
                    v = data.get(k)
                    if isinstance(v, list):
                        raw_lines = v
                        break
                if not raw_lines:
                    for v in data.values():
                        if isinstance(v, list) and v and isinstance(v[0], dict):
                            raw_lines = v
                            break
        return [ln for ln in raw_lines if isinstance(ln, dict)]

    def production_order_has_issued_material(self, doc_entry: int) -> bool:
        """True when any production-order line shows issued material or a closed line state."""
        lines = self.fetch_production_order_lines_raw(doc_entry)
        for line in lines:
            issued_qty = _first_float_value(line, ('IssuedQty', 'IssuedQuantity'))
            if issued_qty is not None and issued_qty > 0:
                return True

            planned_qty = _first_float_value(line, ('PlannedQuantity', 'BaseQuantity'))
            open_qty = _first_float_value(line, ('OpenQty', 'OpenQuantity', 'RemainingQty', 'RemainingQuantity'))
            if planned_qty is not None and open_qty is not None and (planned_qty - open_qty) > 0.0001:
                return True

            if _line_status_is_closed(line):
                return True
        return False

    def close_production_order(self, doc_entry: int) -> None:
        """End a production order by **cancelling** it in SAP (no release required).

        Uses ``POST /ProductionOrders(id)/Cancel``, which applies to **Planned** (and other
        open) special production orders without first releasing or PATCHing to Closed.

        The method name is ``close_production_order`` for existing callers (e.g. superseded
        BOM cleanup) that conceptually “remove” the live SAP document.
        """
        entry = int(doc_entry)
        ep = f'/ProductionOrders({entry})'
        status = self._fetch_production_order_status(entry)
        if status in ('closed', 'cancelled'):
            return
        try:
            self.post(f'{ep}/Cancel', {})
        except SAPClientError as e:
            err = str(e)
            if self._production_order_cancel_benign(err):
                _log.info('SAP: %s/Cancel no-op: %s', ep, err[:220])
                return
            raise

    def mark_production_order_closed(self, doc_entry: int) -> None:
        """Close a production order in SAP by setting ``ProductionOrderStatus`` to closed."""
        entry = int(doc_entry)
        status = self._fetch_production_order_status(entry)
        if status in ('closed', 'cancelled'):
            return
        try:
            self.patch_production_order(
                entry,
                {'ProductionOrderStatus': 'boposClosed'},
            )
        except SAPClientError as e:
            err = str(e)
            if self._production_order_close_benign(err):
                _log.info('SAP: /ProductionOrders(%s) close no-op: %s', entry, err[:220])
                return
            raise

    def report_production_order_completion(
        self,
        doc_entry: int,
        quantity: float,
        batch_number: str,
        *,
        remarks: str = '',
    ) -> dict:
        """Post a Receipt from Production for a production order."""
        qty = float(quantity)
        batch = (batch_number or '').strip()
        if qty <= 0:
            raise SAPClientError('Completion quantity must be greater than zero.')
        if not batch:
            raise SAPClientError('Batch number is required for production completion.')

        payload: dict = {
            'Comments': (remarks or '').strip()[:254],
            'DocumentLines': [
                {
                    'BaseType': 202,
                    'BaseEntry': int(doc_entry),
                    'Quantity': qty,
                    'TransactionType': 'botrntComplete',
                    'BatchNumbers': [
                        {
                            'BatchNumber': batch[:32],
                            'Quantity': qty,
                        }
                    ],
                }
            ],
        }
        if not payload['Comments']:
            payload.pop('Comments')
        return self.post('/InventoryGenEntries', payload)

    @staticmethod
    def _production_order_cancel_benign(err_text: str) -> bool:
        """True when Cancel failed for a harmless reason (already cancelled/closed, missing doc)."""
        t = (err_text or '').lower()
        return any(
            x in t
            for x in (
                'already closed',
                'already cancelled',
                'document is closed',
                'status is closed',
                'cannot be cancelled',
                'no document',
                'not found',
                'does not exist',
            )
        )

    @staticmethod
    def _production_order_close_benign(err_text: str) -> bool:
        """True when Close failed for a harmless reason (already closed/cancelled, missing doc)."""
        t = (err_text or '').lower()
        return any(
            x in t
            for x in (
                'already closed',
                'already cancelled',
                'document is closed',
                'status is closed',
                'no document',
                'not found',
                'does not exist',
            )
        )

    def release_production_order(self, doc_entry: int) -> None:
        """PATCH a Production Order to Released status (SAP B1 Service Layer)."""
        self.patch(
            f'/ProductionOrders({doc_entry})',
            {'ProductionOrderStatus': 'boposReleased'}
        )

    def search_items(self, q: str, limit: int = 80) -> list:
        """Filter Items by ItemCode prefix (typeahead).

        Only **active** OITM rows are returned (``Valid eq 'tYES'``).

        Different SAP B1 Service Layer builds expose different OData function sets.
        Some accept v4 functions like ``contains`` / ``startswith``, while others
        only accept the older v2 ``substringof`` syntax. We attempt the v4-style
        filter first (fast prefix match) and fall back to v2-style on 4xx errors
        so the raw-material autocomplete keeps working.

        For this UI we want **prefix** matching on ItemCode (when the user types
        2-3 initial characters). Some builds reject ``tolower`` in ``$filter``;
        we then try a case-sensitive prefix and an upper-case variant for typical
        all-caps item codes.
        """
        q = (q or '').strip()
        if len(q) < 1:
            return []
        q_lower = q.lower().replace("'", "''")
        q_esc = q.replace("'", "''")
        q_esc_u = q.upper().replace("'", "''")
        select = 'ItemCode,ItemName'
        params_base = {
            '$select': select,
            '$orderby': 'ItemCode',
            '$top': limit,
        }

        active = "Valid eq 'tYES'"
        # v4-ish preferred: case-insensitive prefix on ItemCode
        filt_v4 = f"startswith(tolower(ItemCode), '{q_lower}') and {active}"

        # v2-ish fallback: often works even when v4 functions are rejected
        filt_v2 = f"startswith(tolower(ItemCode), '{q_lower}') and {active}"

        # Minimal fallback: no tolower, just case-sensitive prefix (try raw + upper)
        filt_v3 = (
            f"(startswith(ItemCode, '{q_esc}') or startswith(ItemCode, '{q_esc_u}')) and {active}"
        )

        def _bad_request(err: SAPClientError) -> bool:
            msg = str(err).lower()
            return ' 400 ' in msg or 'status=400' in msg or 'bad request' in msg

        last_err: Optional[SAPClientError] = None
        for filt in (filt_v4, filt_v2, filt_v3):
            try:
                data = self.get('/Items', params={**params_base, '$filter': filt})
                return data.get('value', [])
            except SAPClientError as e:
                last_err = e
                if not _bad_request(e):
                    raise
                continue

        if last_err:
            raise last_err
        return []

    def fetch_item(self, item_code: str) -> dict:
        """GET one Item Master row by ItemCode."""
        k = (item_code or '').replace("'", "''")
        return self.get(f"/Items('{k}')")

    def fetch_item_foreign_name(self, item_code: str) -> Optional[str]:
        """Return OITM foreign name for an item code.

        In some Service Layer builds the property is exposed as **ForeignName**
        (even though the DB column is OITM.FrgnName). Requesting "FrgnName"
        may raise: "Property 'FrgnName' of 'Item' is invalid".
        """
        code = (item_code or '').strip()
        if not code:
            return None
        k = code.replace("'", "''")
        def _pick(d: dict) -> Optional[str]:
            if not isinstance(d, dict):
                return None
            for key in ('ForeignName', 'foreignName'):
                v = d.get(key)
                if v is not None:
                    s = str(v).strip()
                    if s:
                        return s
            # last resort: scan keys by normalized name (some builds change casing)
            for kk, vv in d.items():
                if not isinstance(kk, str):
                    continue
                if kk.strip().casefold() in ('foreignname',):
                    s = str(vv).strip() if vv is not None else ''
                    if s:
                        return s
            return None

        # 1) Direct key lookup
        row = self.get(
            f"/Items('{k}')",
            params={'$select': 'ItemCode,ForeignName,ItemName'},
        )
        v = _pick(row)
        if v:
            return v

        # 2) Fallback: OData filter query (some Service Layer builds omit fields on key lookup)
        data = self.get(
            "/Items",
            params={
                '$filter': f"ItemCode eq '{k}'",
                '$select': 'ItemCode,ForeignName,ItemName',
                '$top': 1,
            },
        )
        vals = data.get('value') or []
        if vals and isinstance(vals[0], dict):
            return _pick(vals[0])
        return None

    def fetch_oscn_substitute(self, item_code: str) -> Optional[str]:
        """Return OSCN.Substitute for an item code (if available).

        Service Layer entity naming varies. Common options:
        - ItemCatalogNumbers (maps OSCN)
        - Items(<code>)/ItemCatalogNumbers (navigation)
        """
        code = (item_code or '').strip()
        if not code:
            return None
        k = code.replace("'", "''")

        def _pick(v) -> Optional[str]:
            if v is None:
                return None
            s = str(v).strip()
            return s or None

        def _extract_subs(rows: List[dict]) -> List[str]:
            out: List[str] = []
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                # Service Layer field naming varies across builds
                cand = (
                    r.get('Substitute')
                    or r.get('substitute')
                    or r.get('CatalogNumber')
                    or r.get('catalogNumber')
                    or r.get('BpCatalogNumber')
                    or r.get('BPcatalogNumber')
                    or r.get('BPCatalogNumber')
                    or r.get('BP_CatalogNumber')
                )
                v = _pick(cand)
                if v and v not in out:
                    out.append(v)
            return out

        # 1) Query collection endpoints that commonly map OSCN
        for entity in (
            'ItemCatalogNumbers',
            'BusinessPartnerCatalogNumbers',
            'CatalogNumbers',
            'ItemsCatalogNumbers',
            'ItemCatalogNumber',
        ):
            try:
                data = self.get(
                    f'/{entity}',
                    params={
                        '$filter': f"ItemCode eq '{k}'",
                        '$top': 20,
                    },
                )
                vals = data.get('value') or []
                subs = _extract_subs(vals)
                if subs:
                    return ', '.join(subs[:5])
            except SAPClientError:
                continue

        # 2) Navigation fallback
        try:
            data = self.get(
                f"/Items('{k}')/ItemCatalogNumbers",
                params={'$top': 20},
            )
            vals = data.get('value') or []
            subs = _extract_subs(vals)
            if subs:
                return ', '.join(subs[:5])
        except SAPClientError:
            pass

        # 3) Practical fallback seen in many installs:
        # OSCN "BP Catalog Number" is surfaced as OITM.SupplierCatalogNo in Service Layer.
        try:
            row = self.get(
                f"/Items('{k}')",
                params={'$select': 'ItemCode,SupplierCatalogNo'},
            )
            sub = _pick(row.get('SupplierCatalogNo') or row.get('supplierCatalogNo'))
            if sub:
                return sub
        except SAPClientError:
            pass

        return None

    def create_item(self, payload: dict) -> dict:
        """POST one Item Master row to SAP."""
        return self.post('/Items', payload)

    def set_item_valid(self, item_code: str, *, valid: bool) -> dict:
        """PATCH OITM row (``/Items``) — ``Valid`` = ``tYES`` / ``tNO``."""
        k = (item_code or '').strip().replace("'", "''")
        if not k:
            raise SAPClientError('Item code is required for set_item_valid.')
        return self.patch(f"/Items('{k}')", {'Valid': 'tYES' if valid else 'tNO'})

    def ensure_item_exists(
        self,
        item_code: str,
        item_name: str,
        base_fg_code: str = None,
        *,
        item_group_code: int = 100,
        sales_uom: str = 'PCS',
    ) -> dict:
        """Ensure an item exists in SAP Item Master; create if missing.

        Returns:
            {'created': bool, 'item_code': str}
        """
        code = (item_code or '').strip()
        name = (item_name or '').strip() or code
        if not code:
            raise SAPClientError('Item code is required.')
        try:
            row = self.fetch_item(code)
            existing = str(row.get('ItemName') or '').strip()
            new_name = (name or '').strip()[:100]
            # ItemCode unchanged; refresh ItemName when we have a proper display name.
            if new_name and new_name.casefold() != code.casefold() and new_name != existing:
                k = code.replace("'", "''")
                try:
                    self.patch(f"/Items('{k}')", {'ItemName': new_name})
                    _log.info('SAP Item %s: ItemName updated', code)
                except SAPClientError as pe:
                    _log.warning('SAP Item %s: could not patch ItemName: %s', code, pe)
            return {'created': False, 'item_code': code}
        except SAPClientError as e:
            msg = str(e)
            # 404/not found in Service Layer should continue to create.
            if '404' not in msg and 'Not Found' not in msg and 'No matching records' not in msg:
                raise
        manufacturer_id = -1
        if base_fg_code:
            try:
                fg_item = self.fetch_item(base_fg_code)
                manufacturer_id = fg_item.get('Manufacturer', -1)
            except Exception as e:
                _log.warning(f'Could not fetch base FG {base_fg_code} for manufacturer: {e}')

        payload = {
            'ItemCode': code[:50],
            'ItemName': name[:100],
            'ItemsGroupCode': int(item_group_code),
            'InventoryUOM': (sales_uom or 'PCS')[:10],
            'ManageBatchNumbers': 'tYES',
            'GSTRelevnt': 'tYES',
            'U_TaxRate': '18',
            'Manufacturer': manufacturer_id,
            'CostAccountingMethod': 'bis_FIFO',
            'InventoryItem': 'tYES',
            'SalesItem': 'tNO',
            'PurchaseItem': 'tNO',
        }
        try:
            self.create_item(payload)
            return {'created': True, 'item_code': code}
        except SAPClientError as e:
            msg = str(e).lower()
            if 'already exists' in msg or '-10' in msg:
                _log.info(f"Item {code} already exists in SAP (caught during creation).")
                return {'created': False, 'item_code': code}
            raise

    def create_special_production_order(
        self,
        *,
        item_no: str,
        planned_qty: float,
        posting_date: str,   # 'YYYY-MM-DD'
        due_date: str,       # 'YYYY-MM-DD'
        warehouse: str,
        remarks: str = '',
        lines: List[dict],   # [{'ItemNo': ..., 'BaseQuantity': ..., 'Warehouse': ...}]
        u_job_ent: str | None = None,
        u_cat: str | None = None,
        u_pcode: str | None = None,
    ) -> dict:
        """Create a Special Production Order in SAP (no BOM required).

        Returns:
            {'abs_entry': int, 'doc_num': int}
        Raises:
            SAPClientError on failure.
        """
        payload = {
            'ItemNo': item_no,
            'ProductionOrderType': 'bopotSpecial',
            'ProductionOrderStatus': 'boposPlanned',
            'PlannedQuantity': planned_qty,
            'PostingDate': posting_date,
            'DueDate': due_date,
            'Warehouse': warehouse,
            'Remarks': remarks[:254] if remarks else '',
            'ProductionOrderLines': [
                {
                    'ItemNo': ln['ItemNo'],
                    'BaseQuantity': float(ln.get('BaseQuantity', 0)) if 'BaseQuantity' in ln else None,
                    'PlannedQuantity': float(ln.get('PlannedQuantity', 0)) if 'PlannedQuantity' in ln else None,
                    'Warehouse': ln.get('Warehouse') or warehouse,
                    'ProductionOrderIssueType': ln.get('ProductionOrderIssueType', 'im_Manual'),
                    **(
                        {'ItemName': (ln.get('ItemName') or '').strip()[:100]}
                        if (str(ln.get('ItemName') or '').strip())
                        else {}
                    ),
                }
                for ln in (lines or [])
            ],
        }
        uje = (u_job_ent or '').strip()
        if uje:
            # SAP B1 user-defined field on OWOR — must exist on the target company DB.
            payload['U_JobEnt'] = uje[:254]
        cat = (u_cat or '').strip()
        if cat:
            payload['U_Cat'] = cat[:20]
        pc = (u_pcode or '').strip()
        if pc:
            # SAP B1 user-defined field on OWOR for the process master code.
            payload['U_PCode'] = pc[:20]
        _log.info('SAP → POST /ProductionOrders item=%s qty=%s wh=%s', item_no, planned_qty, warehouse)
        try:
            resp = self.post('/ProductionOrders', payload)
        except SAPClientError as e:
            # Common SAP validation: DueDate must be within allowed range.
            # Many installs accept DueDate == PostingDate even when other due dates are rejected.
            msg = str(e)
            if ('code' in msg and '-5002' in msg) and ('OWOR.DueDate' in msg or 'DueDate' in msg):
                if due_date != posting_date:
                    _log.warning(
                        "SAP OWOR.DueDate validation failed (due=%s post=%s). Retrying with DueDate=PostingDate.",
                        due_date,
                        posting_date,
                    )
                    payload['DueDate'] = posting_date
                    resp = self.post('/ProductionOrders', payload)
                else:
                    raise
            else:
                raise
        _log.info('SAP → Created ProductionOrder DocEntry=%s DocNum=%s', resp.get('AbsoluteEntry'), resp.get('DocumentNumber'))
        return {
            'abs_entry': resp.get('AbsoluteEntry'),
            'doc_num': resp.get('DocumentNumber'),
        }

    # -------------------------------- event logging helper
    @staticmethod
    def log_event(
        job_id: str,
        action: str,
        request_payload: dict = None,
    ) -> 'IntegrationEvent':
        """Create a pending IntegrationEvent row. Caller must commit."""
        event = IntegrationEvent(
            job_id=job_id,
            action=action,
            state='pending',
            request_payload=request_payload,
        )
        db.session.add(event)
        db.session.flush()
        return event


def get_sap_client() -> SAPClient:
    """Return the SAPClient for the current request, creating it if needed.

    Cached on ``g`` for the lifetime of one HTTP request; the underlying
    ``requests.Session`` may still be shared via ``SAP_REUSE_HTTP_SESSION``.
    """
    if 'sap_client' not in g:
        g.sap_client = SAPClient()
    return g.sap_client
