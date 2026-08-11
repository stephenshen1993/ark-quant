"""THROWAWAY PROTOTYPE — pure sizing sensitivity calculations.

Question: for one frozen account plan, how do discrete target lots change
equal-weight drift, cash usage, and explicit estimated fees?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class Target:
    rank: int
    code: str
    name: str
    price: float
    current_shares: int
    baseline_shares: int
    lot: int


@dataclass(frozen=True)
class PlanInput:
    plan_id: str
    starting_cash: float
    transfer_delta: float
    mandatory_sell_proceeds: float
    targets: tuple[Target, ...]
    fee: Callable[[float], float]


@dataclass(frozen=True)
class Metrics:
    cash_left: float
    fee: float
    buys: float
    sells: float
    max_weight_deviation: float
    total_absolute_deviation: float
    total_squared_deviation: float
    nonzero_orders: int
    total_after_fee: float


def evaluate(plan: PlanInput, target_shares: Iterable[int]) -> Metrics:
    shares = tuple(target_shares)
    if len(shares) != len(plan.targets):
        raise ValueError("target share count does not match target count")

    buys = 0.0
    sells = plan.mandatory_sell_proceeds
    nonzero_orders = 1 if plan.mandatory_sell_proceeds > 0 else 0
    final_values = []
    for target, final_shares in zip(plan.targets, shares):
        delta = final_shares - target.current_shares
        amount = abs(delta) * target.price
        if delta > 0:
            buys += amount
            nonzero_orders += 1
        elif delta < 0:
            sells += amount
            nonzero_orders += 1
        final_values.append(final_shares * target.price)

    turnover = buys + sells
    fee = plan.fee(turnover)
    cash_left = plan.starting_cash + plan.transfer_delta + sells - buys - fee
    total_after_fee = sum(final_values) + cash_left
    ideal_weight = 1.0 / len(plan.targets)
    weights = [value / total_after_fee for value in final_values] if total_after_fee else []
    deviations = [abs(weight - ideal_weight) for weight in weights]
    return Metrics(
        cash_left=round(cash_left, 2),
        fee=round(fee, 2),
        buys=round(buys, 2),
        sells=round(sells, 2),
        max_weight_deviation=max(deviations, default=0.0),
        total_absolute_deviation=sum(deviations),
        total_squared_deviation=sum(value * value for value in deviations),
        nonzero_orders=nonzero_orders,
        total_after_fee=round(total_after_fee, 2),
    )


def add_lot(plan: PlanInput, target_shares: tuple[int, ...], rank: int) -> tuple[int, ...]:
    index = rank - 1
    target = plan.targets[index]
    updated = list(target_shares)
    updated[index] += target.lot
    return tuple(updated)


def remove_lot(plan: PlanInput, target_shares: tuple[int, ...], rank: int) -> tuple[int, ...]:
    index = rank - 1
    target = plan.targets[index]
    updated = list(target_shares)
    if updated[index] - target.lot < 0:
        raise ValueError("target shares cannot be negative")
    updated[index] -= target.lot
    return tuple(updated)


def most_underweight_affordable_rank(plan: PlanInput, target_shares: tuple[int, ...]) -> int | None:
    baseline = evaluate(plan, target_shares)
    candidates = []
    ideal_value = baseline.total_after_fee / len(plan.targets)
    for target in plan.targets:
        candidate = add_lot(plan, target_shares, target.rank)
        candidate_metrics = evaluate(plan, candidate)
        if candidate_metrics.cash_left < -0.01:
            continue
        index = target.rank - 1
        candidate_value = candidate[index] * target.price
        candidates.append((ideal_value - candidate_value, -target.rank, target.rank))
    return max(candidates)[2] if candidates else None
