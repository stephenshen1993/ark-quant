"""Compatibility adapter for the shared exact equal-weight optimizer."""
from __future__ import annotations

from strategies.discrete_target_sizing import STOCK_FEE_SCHEDULE
from strategies.equal_weight_milp import (
    EqualWeightTargetResult as MilpTargetResult,
    solve_equal_weight_targets,
)


def solve_stock_targets(
    *,
    target_codes: list[str],
    holdings: dict[str, int],
    prices: dict[str, float],
    cash: float,
    lot: int,
    max_single_weight: float,
    ordinary_order_threshold: float,
) -> MilpTargetResult:
    """Solve stock targets with stock-specific execution constraints."""
    return solve_equal_weight_targets(
        target_codes=target_codes,
        holdings=holdings,
        prices=prices,
        cash=cash,
        lot=lot,
        max_single_weight=max_single_weight,
        ordinary_order_threshold=ordinary_order_threshold,
        allow_target_sells=False,
        fee_schedule=STOCK_FEE_SCHEDULE,
    )
