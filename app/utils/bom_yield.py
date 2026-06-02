"""BOM quantity gross-up for per-step yield loss (per 1 unit parent output)."""

from decimal import Decimal, ROUND_HALF_UP
from typing import Union


def gross_child_qty_per_parent(
    loss_pct: Union[float, int, Decimal] = 2.0,
    *,
    places: int = 6,
) -> Decimal:
    loss = Decimal(str(loss_pct))
    if loss < 0:
        loss = Decimal('0')
    if loss >= 100:
        loss = Decimal('99.9')
    factor = Decimal('1') / (Decimal('1') - loss / Decimal('100'))
    quant = Decimal('1').scaleb(-places)
    return factor.quantize(quant, rounding=ROUND_HALF_UP)
