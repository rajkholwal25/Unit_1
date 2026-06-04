"""Unit 1 manufacturing: yield loss % → gross raw material kg before FG."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from app.models.job import JobDetailLine


def _yield_factor(yield_loss_pct: Union[float, int, Decimal]) -> float:
    y = max(0.0, min(float(yield_loss_pct or 0), 99.9)) / 100.0
    return 1.0 + y


def gross_kg_from_fg_net(
    net_kg: Union[float, int, Decimal],
    yield_loss_pct: Union[float, int, Decimal],
    *,
    num_convert_steps: int = 1,
) -> float:
    """Gross input kg after ``num_convert_steps`` yield steps (each × (1 + loss%)).

    One step: 1200 @ 2% → 1224. Three steps @ 2%: 1200 × 1.02³ ≈ 1273.45 RM.
    """
    net = max(0.0, float(net_kg or 0))
    f = _yield_factor(yield_loss_pct)
    n = max(0, int(num_convert_steps or 0))
    return net * (f ** n)


def step_output_kg_from_fg(
    net_fg_kg: Union[float, int, Decimal],
    yield_loss_pct: Union[float, int, Decimal],
    step_index: int,
    num_convert_steps: int,
) -> float:
    """Output kg for converting step ``step_index`` (0 = first process, last ≈ FG input)."""
    net = max(0.0, float(net_fg_kg or 0))
    n = max(0, int(num_convert_steps or 0))
    if n <= 0:
        return net
    j = max(0, min(int(step_index), n - 1))
    exp = max(0, n - 1 - j)
    return net * (_yield_factor(yield_loss_pct) ** exp)


def rm_input_kg_from_fg(
    net_fg_kg: Union[float, int, Decimal],
    yield_loss_pct: Union[float, int, Decimal],
    num_convert_steps: int,
) -> float:
    """Raw material qty before first converting step."""
    return gross_kg_from_fg_net(net_fg_kg, yield_loss_pct, num_convert_steps=num_convert_steps)


def detail_yield_loss_pct(detail_line: JobDetailLine | None, config=None) -> float:
    """Unit 1: RM gross-up uses wastage only; per-step yield is not applied."""
    _ = config
    if detail_line is not None and detail_line.yield_loss_pct is not None:
        try:
            return max(0.0, min(float(detail_line.yield_loss_pct), 99.9))
        except (TypeError, ValueError):
            pass
    return 0.0
