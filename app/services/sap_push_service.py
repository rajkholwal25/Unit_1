from .sap_client import SapServiceLayerClient
from .warehouse_mapping import WarehouseMappingService

UOM_CODE_KGS = 'KGS'


def sap_item_uom_fields(config):
    """
    Manual UoM (-1). SAP UI shows names via InventoryUOM / SalesUnit / PurchaseUnit (not SalUnitMsr).
    """
    kgs = (config.get('SAP_UOM_CODE') or UOM_CODE_KGS).strip().upper()
    pack_code = (config.get('SAP_PACK_UOM_CODE') or 'Role').strip()
    group = int(config.get('SAP_UOM_GROUP_ENTRY', -1))
    kgs_entry = int(config.get('SAP_UOM_KGS_ENTRY', 1))
    return {
        'UoMGroupEntry': group,
        'InventoryUoMEntry': -1 if group == -1 else kgs_entry,
        'InventoryUOM': kgs,
        'SalesUnit': kgs,
        'PurchaseUnit': kgs,
        'SalesPackagingUnit': pack_code,
        'PurchasePackagingUnit': pack_code,
        'ItemUnitOfMeasurementCollection': [
            {'UoMType': 'iutInventory', 'UoMEntry': kgs_entry},
            {'UoMType': 'iutSales', 'UoMEntry': kgs_entry},
            {'UoMType': 'iutPurchasing', 'UoMEntry': kgs_entry},
        ],
    }


def resolve_sap_chapter_id(config, client) -> int:
    """
    HSN on Item Master = ChapterID (AbsEntry in IndiaHsn / OCHP).
    Default HSN 3921.90.94 → AbsEntry 72 (resolved from SAP if possible).
    """
    if config.get('SAP_CHAPTER_ID') not in (None, ''):
        return int(config['SAP_CHAPTER_ID'])
    hsn_code = (config.get('SAP_HSN_CODE') or '3921.90.94').strip()
    if client and hsn_code:
        esc = str(hsn_code).replace("'", "''")
        data = client.get(f"/b1s/v1/IndiaHsn?$filter=ChapterID eq '{esc}'&$top=1")
        rows = (data or {}).get('value') or []
        if rows:
            return int(rows[0]['AbsEntry'])
    return 72


def sap_item_tax_fields(config, *, chapter_id: int = None):
    rate = config.get('SAP_ITEM_TAX_RATE', 18)
    fields = {
        'U_TaxRate': int(rate) if rate is not None else 18,
        'GSTRelevnt': 'tYES',
        'GSTTaxCategory': config.get('SAP_GST_TAX_CATEGORY', 'gtc_Regular'),
        'TaxType': 'tt_Yes',
    }
    if chapter_id is not None and int(chapter_id) >= 0:
        fields['ChapterID'] = int(chapter_id)
    return fields


def resolve_is_fg(item_code: str, fg_code: str, process_items) -> bool:
    """FG code is always finished goods; everything else in the BOM set is raw material."""
    if fg_code and item_code == fg_code:
        return True
    if process_items and item_code in process_items:
        return False
    return item_code == fg_code


def sap_fg_items_group(config) -> int:
    return int(config.get('SAP_FG_ITEMS_GROUP', 100))


def sap_component_items_group(config) -> int:
    return int(config.get('SAP_COMPONENT_ITEMS_GROUP', 107))


def sap_material_type_for_group(config, items_group_code: int) -> str:
    """
    Material Type API value for Item Group. When SAP_MATERIAL_TYPE_INVERT_UI is true,
    group 100 uses mt_RawMaterial so the SAP desktop shows "Finished Goods", etc.
    """
    if int(items_group_code) == sap_fg_items_group(config):
        return config.get('SAP_MATERIAL_TYPE_FG', 'mt_FinishedGoods')
    if int(items_group_code) == sap_component_items_group(config):
        return config.get('SAP_MATERIAL_TYPE_COMPONENT', 'mt_RawMaterial')
    return config.get('SAP_MATERIAL_TYPE_COMPONENT', 'mt_RawMaterial')


def sap_material_type(config, *, is_fg: bool) -> str:
    """FG → Finished Goods; process components → Raw Material (SAP General tab)."""
    group = sap_fg_items_group(config) if is_fg else sap_component_items_group(config)
    return sap_material_type_for_group(config, group)


def sap_material_type_label(material_type: str) -> str:
    if material_type == 'mt_FinishedGoods':
        return 'Finished Goods'
    if material_type == 'mt_RawMaterial':
        return 'Raw Material'
    return material_type


def sap_ui_material_type_label(material_type: str, config) -> str:
    """Label as shown on SAP Item Master General tab (may differ from API enum name)."""
    label = sap_material_type_label(material_type)
    if config and config.get('SAP_MATERIAL_TYPE_INVERT_UI'):
        if label == 'Finished Goods':
            return 'Raw Material'
        if label == 'Raw Material':
            return 'Finished Goods'
    return label


def sap_item_master_fields(
    config, *, is_fg: bool = None, items_group_code: int = None, chapter_id: int = None,
):
    if items_group_code is None:
        items_group_code = sap_fg_items_group(config) if is_fg else sap_component_items_group(config)
    material_type = sap_material_type_for_group(config, items_group_code)
    return {
        'MaterialType': material_type,
        'PricingUnit': int(config.get('SAP_PRICING_UNIT', -1)),
        **sap_item_uom_fields(config),
        **sap_item_tax_fields(config, chapter_id=chapter_id),
    }


def sap_uom_preview_meta(config, chapter_id=None):
    uom = sap_item_uom_fields(config)
    tax = sap_item_tax_fields(config, chapter_id=chapter_id)
    code = (config.get('SAP_UOM_CODE') or UOM_CODE_KGS).strip().upper()
    pack = config.get('SAP_PACK_UOM_CODE', 'Role')
    hsn = config.get('SAP_HSN_CODE', '3921.90.94')
    return {
        'code': code,
        'fields': {**uom, **tax},
        'note': (
            f'Manual UoM (group {uom["UoMGroupEntry"]}): inventory/sales/purchase = {code}, '
            f'packaging = {pack}. FG = Finished Goods, components = Raw Material. '
            f'HSN {hsn} (ChapterID {tax.get("ChapterID", "—")}), '
            f'Tax category {tax["GSTTaxCategory"]}, rate {tax["U_TaxRate"]}%. '
            f'Pricing: primary currency.'
        ),
    }


class SapPushService:
    """Push generated item codes to SAP B1 Item Master (BOM is created manually in SAP)."""

    @staticmethod
    def preview_item_payloads(generate_payload, config):
        """Build the exact JSON bodies that will be POSTed to SAP Item Master (no API call)."""
        material = generate_payload.get('material_type', '')
        thickness = generate_payload.get('thickness', '')
        coating = generate_payload.get('coating', '')
        fg_code = generate_payload.get('fg_code', '')
        fg_name = generate_payload.get('fg_name') or f'{material} {thickness} {coating} FG'.strip()

        fg_group = int(config.get('SAP_FG_ITEMS_GROUP', 100))
        comp_group = int(config.get('SAP_COMPONENT_ITEMS_GROUP', 107))
        chapter_id = resolve_sap_chapter_id(config, None)
        if config.get('SAP_BASE_URL'):
            try:
                preview_client = SapServiceLayerClient(
                    config.get('SAP_BASE_URL'),
                    config.get('SAP_USER'),
                    config.get('SAP_PASSWORD'),
                    company_db=config.get('SAP_COMPANY_DB'),
                    verify_ssl=config.get('SAP_SSL_VERIFY', True),
                )
                chapter_id = resolve_sap_chapter_id(config, preview_client)
            except Exception:
                pass

        def build(item_code, item_name, group, sales, warehouse):
            return {
                'ItemCode': item_code,
                'ItemName': (item_name or item_code)[:100],
                'ItemsGroupCode': group,
                'InventoryItem': 'tYES',
                'SalesItem': 'tYES' if sales else 'tNO',
                'PurchaseItem': 'tNO',
                'ProcurementMethod': 'bom_Make',
                'PlanningSystem': 'bop_MRP',
                'ManageBatchNumbers': 'tYES',
                'ManageSerialNumbers': 'tNO',
                **sap_item_master_fields(config, items_group_code=group, chapter_id=chapter_id),
                'ItemWarehouseInfoCollection': [{'WarehouseCode': warehouse}],
            }

        items = []
        fg_mt = sap_material_type_for_group(config, fg_group)
        items.append({
            'role': 'Finished Good',
            'item_code': fg_code,
            'items_group_code': fg_group,
            'material_type': fg_mt,
            'material_type_label': sap_material_type_label(fg_mt),
            'endpoint': 'POST /b1s/v1/Items',
            'payload': build(
                fg_code,
                fg_name,
                fg_group,
                True,
                WarehouseMappingService.for_process('FG'),
            ),
        })
        for pi in generate_payload.get('process_items', []):
            proc = pi.split('-')[-1] if '-' in pi else ''
            comp_mt = sap_material_type_for_group(config, comp_group)
            items.append({
                'role': f'Component ({proc})',
                'item_code': pi,
                'items_group_code': comp_group,
                'material_type': comp_mt,
                'material_type_label': sap_material_type_label(comp_mt),
                'endpoint': 'POST /b1s/v1/Items',
                'payload': build(
                    pi,
                    f'{pi} {proc}'.strip(),
                    comp_group,
                    False,
                    WarehouseMappingService.for_process(proc),
                ),
            })
        uom_meta = sap_uom_preview_meta(config, chapter_id=chapter_id)
        return {
            'api': 'SAP Business One Service Layer — Item Master only (BOM not sent)',
            'unit_of_measure': uom_meta['code'],
            'uom_note': uom_meta['note'],
            'uom_config': {
                'from_env': {
                    'SAP_UOM_CODE': config.get('SAP_UOM_CODE', 'KGS'),
                    'SAP_UOM_GROUP_ENTRY': config.get('SAP_UOM_GROUP_ENTRY'),
                    'SAP_UOM_KGS_ENTRY': config.get('SAP_UOM_KGS_ENTRY'),
                    'SAP_PACK_UOM_CODE': config.get('SAP_PACK_UOM_CODE'),
                    'SAP_MATERIAL_TYPE_FG': config.get('SAP_MATERIAL_TYPE_FG'),
                    'SAP_MATERIAL_TYPE_COMPONENT': config.get('SAP_MATERIAL_TYPE_COMPONENT'),
                },
                'payload_fields': uom_meta['fields'],
            },
            'items': items,
        }

    def __init__(self, config):
        self.config = config
        self.client = SapServiceLayerClient(
            config.get('SAP_BASE_URL'),
            config.get('SAP_USER'),
            config.get('SAP_PASSWORD'),
            company_db=config.get('SAP_COMPANY_DB'),
            verify_ssl=config.get('SAP_SSL_VERIFY', True),
            retries=int(config.get('SAP_RETRY', 3)),
        )
        self.fg_group = int(config.get('SAP_FG_ITEMS_GROUP', 100))
        self.component_group = int(config.get('SAP_COMPONENT_ITEMS_GROUP', 107))
        self.chapter_id = resolve_sap_chapter_id(config, self.client)

    def _item_exists(self, item_code):
        try:
            data = self.client.get(self.client.item_path(item_code))
            return data is not None
        except Exception:
            return False

    def _build_item_payload(
        self,
        item_code,
        item_name,
        items_group_code,
        sales_item,
        warehouse,
    ):
        return {
            'ItemCode': item_code,
            'ItemName': item_name[:100] if item_name else item_code,
            'ItemsGroupCode': items_group_code,
            'InventoryItem': 'tYES',
            'SalesItem': 'tYES' if sales_item else 'tNO',
            'PurchaseItem': 'tNO',
            'ProcurementMethod': 'bom_Make',
            'PlanningSystem': 'bop_MRP',
            'ManageBatchNumbers': 'tYES',
            'ManageSerialNumbers': 'tNO',
            **sap_item_master_fields(
                self.config, items_group_code=items_group_code, chapter_id=self.chapter_id,
            ),
            'ItemWarehouseInfoCollection': [{'WarehouseCode': warehouse}],
        }

    def _sync_fields(self, items_group_code: int):
        """Item group + matching material type + UoM/tax (fixes swapped General tab values)."""
        return {
            'ItemsGroupCode': items_group_code,
            **sap_item_master_fields(
                self.config, items_group_code=items_group_code, chapter_id=self.chapter_id,
            ),
        }

    def _items_group_for_role(self, is_fg: bool) -> int:
        return self.fg_group if is_fg else self.component_group

    def push_item(self, item_code, item_name, *, is_fg=False, warehouse=None):
        items_group_code = self._items_group_for_role(is_fg)
        sync = self._sync_fields(items_group_code)
        if self._item_exists(item_code):
            self.client.patch(self.client.item_path(item_code), sync)
            label = sap_material_type_label(sync.get('MaterialType', ''))
            hsn = self.config.get('SAP_HSN_CODE', '3921.90.94')
            return {
                'status': 'updated',
                'reason': (
                    f'group {items_group_code} + Material Type ({label}) + '
                    f'HSN {hsn} + Tax {sync.get("GSTTaxCategory")} synced'
                ),
                'ItemCode': item_code,
                'ItemsGroupCode': items_group_code,
                'MaterialType': sync.get('MaterialType'),
            }

        if is_fg:
            wh = warehouse or WarehouseMappingService.for_process('FG')
            payload = self._build_item_payload(
                item_code,
                item_name,
                self.fg_group,
                sales_item=True,
                warehouse=wh,
            )
        else:
            proc = item_code.split('-')[-1] if '-' in item_code else ''
            wh = warehouse or WarehouseMappingService.for_process(proc)
            payload = self._build_item_payload(
                item_code,
                item_name,
                self.component_group,
                sales_item=False,
                warehouse=wh,
            )

        result = self.client.post('/b1s/v1/Items', payload)
        return {
            'status': 'created',
            'ItemCode': item_code,
            'ItemsGroupCode': payload.get('ItemsGroupCode'),
            'MaterialType': payload.get('MaterialType'),
            'response': result,
        }

    def push_item_master(self, payload):
        """
        Push FG + all process items to SAP Item Master only.
        BOM is not pushed — user builds BOM manually in SAP.
        """
        if not self.config.get('SAP_BASE_URL'):
            raise ValueError('SAP_BASE_URL is not configured. Add it to .env (you can set it tomorrow).')

        fg_code = payload.get('fg_code')
        if not fg_code:
            raise ValueError('fg_code is required')

        process_items = list(payload.get('process_items') or [])
        material = payload.get('material_type', '')
        thickness = payload.get('thickness', '')
        coating = payload.get('coating', '')
        fg_name = payload.get('fg_name') or f'{material} {thickness} {coating} FG'.strip()

        results = []

        fg_result = self.push_item(
            fg_code, fg_name, is_fg=resolve_is_fg(fg_code, fg_code, process_items),
        )
        results.append({'type': 'fg', 'item': fg_code, **fg_result})

        for pi in process_items:
            proc = pi.split('-')[-1] if '-' in pi else ''
            comp_name = payload.get('item_names', {}).get(pi) or f'{pi} {proc}'.strip()
            comp_result = self.push_item(
                pi, comp_name, is_fg=resolve_is_fg(pi, fg_code, process_items),
            )
            results.append({'type': 'component', 'item': pi, **comp_result})

        created = sum(1 for r in results if r.get('status') == 'created')
        updated = sum(1 for r in results if r.get('status') == 'updated')
        skipped = sum(1 for r in results if r.get('status') == 'skipped')

        return {
            'status': 'completed',
            'summary': {
                'created': created,
                'updated': updated,
                'skipped': skipped,
                'total': len(results),
            },
            'responses': results,
        }

    # Legacy BOM push — kept for reference; not used by default flow.
    def push_bom(self, tree_code, child_items):
        payload = {
            'TreeCode': tree_code,
            'TreeType': 'iProductionTree',
            'Quantity': 1,
            'Warehouse': WarehouseMappingService.for_process('FG'),
            'ProductTreeLines': child_items,
        }
        return self.client.post('/b1s/v1/ProductTrees', payload)
