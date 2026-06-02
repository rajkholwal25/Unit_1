from typing import List, Optional

from ..utils.bom_yield import gross_child_qty_per_parent
from .warehouse_mapping import WarehouseMappingService

RM_WAREHOUSE = 'FBD-RM'


def warehouse_for_parent(parent_code: str, fg_code: str) -> str:
    if parent_code == fg_code:
        return WarehouseMappingService.for_process('FG')
    if parent_code.startswith(f'{fg_code}-'):
        proc = parent_code.rsplit('-', 1)[-1]
        return WarehouseMappingService.for_process(proc)
    return WarehouseMappingService.for_process('FG')


def warehouse_for_child(child_code: str, fg_code: str, *, line_type: str) -> str:
    if line_type == 'raw_material':
        return RM_WAREHOUSE
    if child_code.startswith(f'{fg_code}-'):
        proc = child_code.rsplit('-', 1)[-1]
        return WarehouseMappingService.for_process(proc)
    return RM_WAREHOUSE


class BomGenerationService:
    @staticmethod
    def generate_chain(
        fg_code: str,
        processes: List[str],
        *,
        raw_material_code: Optional[str] = None,
        yield_loss_pct: float = 2.0,
    ):
        """Multi-level BOM: per 1 kg parent, qty includes 2% yield loss per step."""
        qty = float(gross_child_qty_per_parent(yield_loss_pct))
        chain = []
        current_parent = fg_code
        sort_order = 0
        for proc in reversed(processes):
            child = f'{fg_code}-{proc}'
            chain.append({
                'parent': current_parent,
                'child': child,
                'process': proc,
                'line_type': 'process',
                'quantity': qty,
                'sort_order': sort_order,
                'parent_warehouse': warehouse_for_parent(current_parent, fg_code),
                'child_warehouse': warehouse_for_child(child, fg_code, line_type='process'),
            })
            current_parent = child
            sort_order += 1

        if raw_material_code:
            chain.append({
                'parent': current_parent,
                'child': raw_material_code.strip(),
                'process': 'RM',
                'line_type': 'raw_material',
                'quantity': qty,
                'sort_order': sort_order,
                'parent_warehouse': warehouse_for_parent(current_parent, fg_code),
                'child_warehouse': RM_WAREHOUSE,
            })
        return chain
