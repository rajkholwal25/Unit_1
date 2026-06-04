"""Bridge Unit 2 job/BOM code to Unit 1 warehouse mapping."""

from flask import current_app

from .warehouse_mapping import WarehouseMappingService


def default_sap_warehouse() -> str:
    return (
        current_app.config.get('SAP_DEFAULT_WAREHOUSE')
        or WarehouseMappingService.default_po_warehouse()
    )


def warehouse_for_process_code(process_code: str) -> str:
    return WarehouseMappingService.for_process(process_code)


def process_wh_by_tail() -> dict:
    """Tail / process_code → warehouse for BOM studio (Unit 1 FBD-*)."""
    tails = [
        'EMB', 'MET', 'MTL', 'SLT', 'HRI', 'COT', 'COAT', 'ALO', 'ALOX', 'MAT',
        'FG', 'PK-PACK', 'RM', 'PRI', 'PRIB', 'LAM', 'DIE', 'CORU', 'PST',
    ]
    return {t: WarehouseMappingService.for_process(t) for t in tails}
