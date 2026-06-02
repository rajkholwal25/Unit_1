"""Fetch, sync, update, and delete SAP B1 Item Master records."""

from datetime import datetime

from ..extensions import db
from ..models import GeneratedFGItem, ItemMaster
from .sap_client import SapServiceLayerClient
from .sap_push_service import (
    SapPushService,
    sap_component_items_group,
    sap_fg_items_group,
    sap_material_type_for_group,
    sap_ui_material_type_label,
)

ITEM_SELECT = (
    'ItemCode,ItemName,ItemsGroupCode,MaterialType,InventoryUOM,'
    'SalesItem,PurchaseItem,InventoryItem'
)


def _client(config) -> SapServiceLayerClient:
    return SapServiceLayerClient(
        config.get('SAP_BASE_URL'),
        config.get('SAP_USER'),
        config.get('SAP_PASSWORD'),
        company_db=config.get('SAP_COMPANY_DB'),
        verify_ssl=config.get('SAP_SSL_VERIFY', True),
        retries=int(config.get('SAP_RETRY', 3)),
    )


def sap_item_row_dict(raw: dict, config) -> dict:
    group = int(raw.get('ItemsGroupCode') or 0)
    mt = raw.get('MaterialType') or ''
    fg_group = sap_fg_items_group(config)
    comp_group = sap_component_items_group(config)
    if group == fg_group:
        role = 'fg'
    elif group == comp_group:
        role = 'component'
    else:
        role = 'other'
    code = raw.get('ItemCode') or ''
    parent = None
    if role == 'component' and code.count('-') >= 4:
        parts = code.rsplit('-', 1)
        if len(parts) == 2:
            parent = parts[0]
    return {
        'item_code': code,
        'item_name': raw.get('ItemName') or code,
        'items_group_code': group,
        'material_type_api': mt,
        'material_type_label': sap_ui_material_type_label(mt, config),
        'inventory_uom': raw.get('InventoryUOM') or '—',
        'role': role,
        'parent_fg_code': parent or '—',
        'sales_item': raw.get('SalesItem') == 'tYES',
    }


class SapItemSyncService:
    def __init__(self, config):
        if not config.get('SAP_BASE_URL'):
            raise ValueError('SAP is not configured')
        self.config = config
        self.client = _client(config)
        self.fg_group = sap_fg_items_group(config)
        self.comp_group = sap_component_items_group(config)

    def list_items(self, *, skip=0, top=50, search=None):
        params = [f'$select={ITEM_SELECT}', f'$orderby=ItemCode', f'$skip={int(skip)}', f'$top={int(top)}']
        term = (search or '').strip()
        if term:
            # SAP Service Layer OData filters are typically case-sensitive. Use tolower() for
            # case-insensitive search across ItemCode and ItemName.
            esc = SapServiceLayerClient.escape_item_code(term.lower()).replace("'", "''")
            params.append(
                "$filter="
                f"contains(tolower(ItemCode),'{esc}') or "
                f"contains(tolower(ItemName),'{esc}')"
            )
        path = f"/b1s/v1/Items?{'&'.join(params)}"
        data = self.client.get(path) or {}
        rows = [sap_item_row_dict(x, self.config) for x in data.get('value', [])]
        return {
            'items': rows,
            'skip': skip,
            'top': top,
            'count': len(rows),
            'has_more': len(rows) >= top,
        }

    def get_item(self, item_code):
        raw = self.client.get(self.client.item_path(item_code))
        if not raw:
            return None
        return sap_item_row_dict(raw, self.config)

    def find_component_codes(self, fg_code: str):
        fg = (fg_code or '').strip()
        if not fg:
            return []
        codes = set()
        for row in ItemMaster.query.filter(
            db.or_(ItemMaster.parent_fg_code == fg, ItemMaster.item_code.like(f'{fg}%'))
        ).all():
            if row.item_code != fg and (
                row.parent_fg_code == fg or row.item_code.startswith(fg + '-')
            ):
                codes.add(row.item_code)
        esc = SapServiceLayerClient.escape_item_code(fg)
        path = (
            f"/b1s/v1/Items?$select=ItemCode&$filter=startswith(ItemCode,'{esc}-')"
            '&$orderby=ItemCode'
        )
        data = self.client.get(path) or {}
        for row in data.get('value', []):
            c = row.get('ItemCode')
            if c and c != fg:
                codes.add(c)
        return sorted(codes)

    def sync_all(self, *, max_pages=200, page_size=100):
        """Pull SAP Item Master into local catalog (upsert)."""
        synced = 0
        skip = 0
        uom = (self.config.get('SAP_UOM_CODE') or 'KGS').strip().upper()
        for _ in range(max_pages):
            chunk = self.list_items(skip=skip, top=page_size)
            batch = chunk.get('items') or []
            if not batch:
                break
            for row in batch:
                self._upsert_from_sap(row, uom)
                synced += 1
            if not chunk.get('has_more'):
                break
            skip += page_size
        db.session.commit()
        return {'synced': synced, 'message': f'Synced {synced} items from SAP'}

    def _upsert_from_sap(self, row: dict, uom: str):
        code = row['item_code']
        group = row.get('items_group_code')
        role = row.get('role')
        item_type = 'fg' if role == 'fg' else 'component' if role == 'component' else 'other'
        parent = row.get('parent_fg_code')
        if parent == '—':
            parent = None
        proc = None
        if item_type == 'component' and code and '-' in code:
            proc = code.rsplit('-', 1)[-1]
        existing = ItemMaster.query.filter_by(item_code=code).first()
        if existing:
            existing.item_name = row.get('item_name') or existing.item_name
            existing.item_type = item_type if item_type != 'other' else existing.item_type
            existing.parent_fg_code = parent or existing.parent_fg_code
            existing.process_code = proc or existing.process_code
            existing.items_group_code = group
            existing.invntry_uom = row.get('inventory_uom') or uom
            existing.sap_pushed = True
            existing.sap_pushed_at = datetime.utcnow()
            existing.updated_at = datetime.utcnow()
        else:
            db.session.add(
                ItemMaster(
                    item_code=code,
                    item_name=row.get('item_name') or code,
                    item_type=item_type,
                    parent_fg_code=parent,
                    process_code=proc,
                    items_group_code=group,
                    invntry_uom=row.get('inventory_uom') or uom,
                    sal_unit_msr=uom,
                    buy_unit_msr=uom,
                    sap_pushed=True,
                    sap_pushed_at=datetime.utcnow(),
                )
            )

    def update_item(self, item_code: str, *, item_name=None, items_group_code=None):
        code = (item_code or '').strip()
        if not code:
            raise ValueError('item_code required')
        group = int(items_group_code) if items_group_code is not None else None
        if group is None:
            existing = self.client.get(self.client.item_path(code))
            if not existing:
                raise ValueError(f'Item {code} not found in SAP')
            group = int(existing.get('ItemsGroupCode') or self.fg_group)
        payload = {
            'ItemsGroupCode': group,
            'MaterialType': sap_material_type_for_group(self.config, group),
        }
        if item_name is not None:
            payload['ItemName'] = str(item_name)[:100]
        self.client.patch(self.client.item_path(code), payload)
        push = SapPushService(self.config)
        push.push_item(
            code,
            item_name or code,
            is_fg=(group == self.fg_group),
        )
        row = ItemMaster.query.filter_by(item_code=code).first()
        if row:
            if item_name:
                row.item_name = item_name[:128]
            row.items_group_code = group
            row.item_type = 'fg' if group == self.fg_group else 'component'
            row.updated_at = datetime.utcnow()
            db.session.commit()
        return {'item_code': code, 'status': 'updated', 'items_group_code': group}

    def delete_item(self, item_code: str):
        code = (item_code or '').strip()
        if not code:
            raise ValueError('item_code required')
        path = self.client.item_path(code)
        if self.client.get(path):
            self.client.delete(path)
        row = ItemMaster.query.filter_by(item_code=code).first()
        if row:
            db.session.delete(row)
            db.session.commit()
        return {'deleted': code}

    def delete_fg_with_components(self, fg_code: str):
        fg = (fg_code or '').strip()
        if not fg:
            raise ValueError('fg_code required')
        components = self.find_component_codes(fg)
        deleted = []
        errors = []
        for code in components:
            try:
                self.delete_item(code)
                deleted.append(code)
            except Exception as exc:
                errors.append({'item': code, 'error': str(exc)})
        try:
            self.delete_item(fg)
            deleted.append(fg)
        except Exception as exc:
            errors.append({'item': fg, 'error': str(exc)})
        from .generated_items import delete_generated_fg_item

        for g in GeneratedFGItem.query.filter_by(item_code=fg).all():
            delete_generated_fg_item(g)
        if not errors:
            db.session.commit()
        else:
            db.session.commit()
        return {
            'fg_code': fg,
            'deleted': deleted,
            'components': components,
            'errors': errors,
        }
