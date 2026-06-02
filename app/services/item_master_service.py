"""Item Master = inventory catalog of all unique item codes (upsert only, no duplicates)."""

from datetime import datetime

from ..extensions import db
from ..models import ItemMaster
from ..utils.thickness import parse_thickness, thickness_display
from .sap_push_service import UOM_CODE_KGS
from .warehouse_mapping import WarehouseMappingService


def _uom_code(config):
    if config:
        return (config.get('SAP_UOM_CODE') or UOM_CODE_KGS).strip().upper()
    return UOM_CODE_KGS


def _upsert_row(**kwargs):
    """Insert or update by item_code — never creates a duplicate row."""
    code = kwargs.pop('item_code')
    row = ItemMaster.query.filter_by(item_code=code).first()
    if row:
        for k, v in kwargs.items():
            if v is None:
                continue
            if k == 'sap_pushed' and row.sap_pushed:
                continue
            setattr(row, k, v)
        row.updated_at = datetime.utcnow()
    else:
        row = ItemMaster(item_code=code, **kwargs)
        db.session.add(row)
    return row


def sync_from_generator_save(payload, fg_id, config=None):
    """
    Add FG + components to Item Master catalog.
    New process codes are added; existing codes are updated, never removed.
    """
    fg_code = payload.get('fg_code')
    if not fg_code:
        return []

    material = payload.get('material_type', '')
    thickness = parse_thickness(payload.get('thickness'))
    coating = payload.get('coating', '')
    uom = _uom_code(config)
    fg_group = int((config or {}).get('SAP_FG_ITEMS_GROUP', 100))
    comp_group = int((config or {}).get('SAP_COMPONENT_ITEMS_GROUP', 107))
    th_label = thickness_display(thickness) if thickness is not None else ''
    fg_name = f'{material} {th_label} {coating} FG'.strip()
    added = []

    if not item_exists(fg_code):
        added.append(fg_code)
    _upsert_row(
        item_code=fg_code,
        item_name=fg_name[:128],
        item_type='fg',
        parent_fg_code=None,
        process_code=None,
        material_type=material,
        thickness=thickness,
        coating=coating,
        pattern_id=payload.get('pattern_id'),
        bom_template_id=payload.get('template_id'),
        generated_fg_id=fg_id,
        warehouse_code=WarehouseMappingService.for_process('FG'),
        items_group_code=fg_group,
        invntry_uom=uom,
        sal_unit_msr=uom,
        buy_unit_msr=uom,
        sales_item=True,
    )

    for pi in payload.get('process_items', []):
        proc = pi.split('-')[-1] if '-' in pi else ''
        if not item_exists(pi):
            added.append(pi)
        _upsert_row(
            item_code=pi,
            item_name=f'{pi} {proc}'.strip()[:128],
            item_type='component',
            parent_fg_code=fg_code,
            process_code=proc,
            material_type=material,
            thickness=thickness,
            coating=coating,
            pattern_id=payload.get('pattern_id'),
            bom_template_id=payload.get('template_id'),
            generated_fg_id=fg_id,
            warehouse_code=WarehouseMappingService.for_process(proc),
            items_group_code=comp_group,
            invntry_uom=uom,
            sal_unit_msr=uom,
            buy_unit_msr=uom,
            sales_item=False,
        )

    return added


def mark_sap_pushed(item_codes):
    now = datetime.utcnow()
    for code in item_codes:
        if not code:
            continue
        row = ItemMaster.query.filter_by(item_code=code).first()
        if row:
            row.sap_pushed = True
            row.sap_pushed_at = now
            row.updated_at = now


def delete_for_fg(fg):
    """Item Master is kept for inventory — deleting a saved BOM does not remove catalog items."""
    if not fg:
        return


def delete_catalog_items(item_codes):
    """Remove rows from local Item Master catalog."""
    for code in item_codes or []:
        if not code:
            continue
        row = ItemMaster.query.filter_by(item_code=code).first()
        if row:
            db.session.delete(row)
    db.session.commit()


def find_component_codes_for_fg(fg_code: str):
    fg = (fg_code or '').strip()
    if not fg:
        return []
    rows = ItemMaster.query.filter(
        db.or_(
            ItemMaster.parent_fg_code == fg,
            ItemMaster.item_code.like(f'{fg}-%'),
        )
    ).all()
    return sorted({r.item_code for r in rows if r.item_code != fg})


def item_exists(item_code):
    code = (item_code or '').strip()
    if not code:
        return False
    return ItemMaster.query.filter(
        db.func.upper(ItemMaster.item_code) == code.upper()
    ).first() is not None


def search_items(query=None, limit=200):
    q = ItemMaster.query
    term = (query or '').strip()
    if term:
        like = f'%{term}%'
        q = q.filter(
            db.or_(
                ItemMaster.item_code.ilike(like),
                ItemMaster.item_name.ilike(like),
                ItemMaster.parent_fg_code.ilike(like),
                ItemMaster.material_type.ilike(like),
                ItemMaster.process_code.ilike(like),
            )
        )
    return q.order_by(ItemMaster.updated_at.desc()).limit(limit).all()
