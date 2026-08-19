"""Shared discrete target sizing contracts, fees, and compatibility entry point."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from typing import Literal


TARGET_COUNT = 20
MAX_SINGLE_WEIGHT = 0.10
EPSILON = 0.01


@dataclass
class DiscreteSizingError(ValueError):
    code: str
    message: str
    details: dict

    def __init__(self, code: str, message: str, **details):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class FeeSchedule:
    """Linear transaction-cost schedule understood exactly by the MILP."""

    name: str
    fixed_per_order: float = 0.0
    proportional_rate: float = 0.0
    proportional_basis: Literal["turnover", "sell"] = "turnover"
    minimum_fee: float = 0.0

    @property
    def fixed_per_order_cents(self) -> int:
        return int(
            (Decimal(str(self.fixed_per_order)) * 100).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )

    @property
    def minimum_fee_cents(self) -> int:
        return int(
            (Decimal(str(self.minimum_fee)) * 100).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )

    @property
    def proportional_cents_per_mill(self) -> Fraction:
        return Fraction(str(self.proportional_rate)) / 10

    def proportional_fee_cents(self, turnover_mills: int) -> int:
        raw = (
            Decimal(turnover_mills)
            * Decimal(str(self.proportional_rate))
            / Decimal(10)
        )
        return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def estimate(
        self,
        targets: dict[str, int],
        holdings: dict[str, int],
        target_codes: list[str],
        prices: dict[str, float],
    ) -> float:
        deltas = {
            code: (targets[code] - holdings.get(code, 0)) * prices[code]
            for code in target_codes
        }
        exits = {
            code: holdings[code] * prices[code]
            for code in holdings
            if code not in targets
        }
        order_count = sum(bool(amount) for amount in deltas.values()) + len(exits)
        if not order_count:
            return 0.0
        if self.proportional_basis == "sell":
            basis = sum(-amount for amount in deltas.values() if amount < 0)
            basis += sum(exits.values())
        else:
            basis = sum(abs(amount) for amount in deltas.values())
            basis += sum(exits.values())
        raw = (
            Decimal(str(self.fixed_per_order)) * order_count
            + Decimal(str(basis)) * Decimal(str(self.proportional_rate))
        )
        fee = max(Decimal(str(self.minimum_fee)), raw)
        return float(fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


STOCK_FEE_SCHEDULE = FeeSchedule(
    name="stock_a_share",
    fixed_per_order=5.0,
    proportional_rate=0.0006,
    proportional_basis="sell",
)
CB_FEE_SCHEDULE = FeeSchedule(
    name="convertible_bond",
    proportional_rate=0.0001,
    proportional_basis="turnover",
    minimum_fee=10.0,
)


def score_targets(
    targets: dict[str, int],
    *,
    target_codes: list[str],
    prices: dict[str, float],
    cash_left: float,
    total_value: float | None = None,
) -> tuple[float, float, float]:
    """Return max deviation, L1 deviation, and residual cash."""
    total_value = total_value or sum(
        targets[code] * prices[code]
        for code in target_codes
    ) + max(0.0, cash_left)
    target_weight = 1 / len(target_codes)
    deviations = [
        abs((targets[code] * prices[code] / total_value) - target_weight)
        for code in target_codes
    ]
    cash_weight = max(0.0, cash_left) / total_value
    return (
        max(*deviations, cash_weight),
        sum(deviations) + cash_weight,
        max(0.0, cash_left),
    )


def stock_fee_estimate(
    targets: dict[str, int],
    holdings: dict[str, int],
    target_codes: list[str],
    prices: dict[str, float],
) -> float:
    return STOCK_FEE_SCHEDULE.estimate(targets, holdings, target_codes, prices)


def cb_fee_estimate(
    targets: dict[str, int],
    holdings: dict[str, int],
    target_codes: list[str],
    prices: dict[str, float],
) -> float:
    return CB_FEE_SCHEDULE.estimate(targets, holdings, target_codes, prices)


def size_discrete_targets(
    *,
    target_codes: list[str],
    holdings: dict[str, int],
    prices: dict[str, float],
    cash: float,
    lot: int,
    max_single_weight: float = MAX_SINGLE_WEIGHT,
    fee_schedule: FeeSchedule = CB_FEE_SCHEDULE,
    allow_target_sells: bool = True,
    ordinary_order_threshold: float = 0.0,
) -> tuple[dict[str, int], dict]:
    """Compatibility interface for the shared exact equal-weight optimizer."""
    target_codes = list(dict.fromkeys(target_codes))
    if len(target_codes) != TARGET_COUNT:
        raise DiscreteSizingError(
            "CAPACITY_CONFLICT",
            "合格候选必须恰为 Top 20，不能静默降级",
            qualified_count=len(target_codes),
            required_count=TARGET_COUNT,
        )
    from strategies.equal_weight_milp import solve_equal_weight_targets

    result = solve_equal_weight_targets(
        target_codes=target_codes,
        holdings=holdings,
        prices=prices,
        cash=cash,
        lot=lot,
        max_single_weight=max_single_weight,
        ordinary_order_threshold=ordinary_order_threshold,
        allow_target_sells=allow_target_sells,
        fee_schedule=fee_schedule,
    )
    return result.targets, result.summary
