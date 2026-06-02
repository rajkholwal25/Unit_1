"""BOM builder: save lines, search raw materials, prepare SAP push."""

from decimal import Decimal

from ..extensions import db
from ..models import BomStructure, BomTemplate, GeneratedFGItem, GeneratedProcessItem, ItemMaster
from ..utils.bom_yield import gross_child_qty_per_parent
from .bom_generation import BomGenerationService, RM_WAREHOUSE, warehouse_for_parent


def default_yield_loss_pct(config) -> float:
    if not config:
        return 2.0
    try:
        return float(config.get('BOM_YIELD_LOSS_PCT', 2))
    except (TypeError, ValueError):
        return 2.0


def _process_codes_for_fg(fg: GeneratedFGItem) -> list:
    rows = GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).order_by(GeneratedProcessItem.id).all()
    if rows:
        return [r.process_code for r in rows]
    template = BomTemplate.query.get(fg.bom_template_id) if fg.bom_template_id else None
    return list(template.process_sequence) if template and template.process_sequence else []


def _exclude_codes(fg_code: str, process_items: list) -> set:
    codes = {fg_code}
    codes.update(process_items or [])
    return codes


def search_raw_materials(fg_code: str, process_items: list, query: str = '', *, limit: int = 40):
    exclude = _exclude_codes(fg_code, process_items)
    q = ItemMaster.query.filter(~ItemMaster.item_code.in_(exclude))
    term = (query or '').strip()
    if term:
        like = f'%{term}%'
        # Case-insensitive search. Prefer "starts with" results first (e.g. rm → RM-*)
        q = q.filter(
            db.or_(
                ItemMaster.item_code.ilike(like),
                ItemMaster.item_name.ilike(like),
            )
        ).order_by(
            db.case(
                (ItemMaster.item_code.ilike(f'{term}%'), 0),
                else_=1,
            ),
            ItemMaster.item_code,
        )
    else:
        q = q.order_by(ItemMaster.item_code)
    return [
        {
            'item_code': r.item_code,
            'item_name': r.item_name,
            'warehouse_code': r.warehouse_code or RM_WAREHOUSE,
            'sap_pushed': bool(r.sap_pushed),
        }
        for r in q.limit(limit).all()
    ]


def persist_bom_lines(fg: GeneratedFGItem, chain: list, *, raw_material_code: str = None, yield_loss_pct: float = None):
    BomStructure.query.filter_by(generated_fg_id=fg.id).delete(synchronize_session=False)
    for node in chain:
        proc = node.get('process')
        db.session.add(
            BomStructure(
                generated_fg_id=fg.id,
                parent_item_code=node['parent'],
                child_item_code=node['child'],
                process_sequence=[proc] if proc else None,
                line_type=node.get('line_type', 'process'),
                quantity=Decimal(str(node['quantity'])),
                warehouse_code=node.get('child_warehouse') or RM_WAREHOUSE,
                sort_order=int(node.get('sort_order', 0)),
            ),
        )
    if raw_material_code is not None:
        fg.raw_material_item_code = (raw_material_code or '').strip() or None
    if yield_loss_pct is not None:
        fg.yield_loss_pct = Decimal(str(yield_loss_pct))


def rebuild_bom_for_fg(fg, *, raw_material_code=None, yield_loss_pct=None, config=None):
    processes = _process_codes_for_fg(fg)
    loss = yield_loss_pct if yield_loss_pct is not None else float(fg.yield_loss_pct or default_yield_loss_pct(config))
    rm = raw_material_code if raw_material_code is not None else fg.raw_material_item_code
    chain = BomGenerationService.generate_chain(
        fg.item_code, processes, raw_material_code=rm, yield_loss_pct=loss,
    )
    persist_bom_lines(fg, chain, raw_material_code=rm, yield_loss_pct=loss)
    return chain


def bom_lines_for_sap(fg: GeneratedFGItem) -> list:
    rows = BomStructure.query.filter_by(generated_fg_id=fg.id).order_by(BomStructure.sort_order.desc()).all()
    loss = float(fg.yield_loss_pct or 2)
    default_qty = float(gross_child_qty_per_parent(loss))
    return [
        {
            'parent': r.parent_item_code,
            'child': r.child_item_code,
            'line_type': r.line_type or 'process',
            'quantity': float(r.quantity) if r.quantity is not None else default_qty,
            'parent_warehouse': warehouse_for_parent(r.parent_item_code, fg.item_code),
            'child_warehouse': r.warehouse_code or RM_WAREHOUSE,
            'sort_order': r.sort_order or 0,
        }
        for r in rows
    ]


def bom_preview_dict(chain: list, *, yield_loss_pct: float) -> dict:
    per_unit = float(gross_child_qty_per_parent(yield_loss_pct))
    return {
        'yield_loss_pct': yield_loss_pct,
        'qty_per_unit_parent': per_unit,
        'lines': [
            {
                'parent': n['parent'],
                'child': n['child'],
                'process': n.get('process'),
                'line_type': n.get('line_type', 'process'),
                'quantity': n.get('quantity', per_unit),
                'parent_warehouse': n.get('parent_warehouse'),
                'child_warehouse': n.get('child_warehouse'),
            }
            for n in chain
        ],
        'ready': bool(chain) and any(n.get('line_type') == 'raw_material' for n in chain),
    }
