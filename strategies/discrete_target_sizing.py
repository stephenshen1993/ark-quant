"""Common discrete target-state sizing for next-day account trade plans.

The module deliberately has no database or market-data dependency.  It turns
one frozen Top-20, holdings, prices and a same-day cash budget into target
units.  Both stock and convertible-bond adapters retain their own execution
filters, but must use this identical capacity and candidate-comparison logic.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


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


def score_targets(
    targets: dict[str, int],
    *,
    target_codes: list[str],
    prices: dict[str, float],
    cash_left: float,
    total_value: float | None = None,
) -> tuple[float, float, float]:
    """Return the first three public tie-breakers for a candidate state.

    The remaining investable cash is a virtual position whose target weight is
    zero.  Rounding only happens in callers' presentation layers; comparisons
    use the raw values to keep a deterministic result.
    """
    total_value = total_value or sum(targets[code] * prices[code] for code in target_codes) + max(0.0, cash_left)
    target_weight = 1 / len(target_codes)
    deviations = [
        abs((targets[code] * prices[code] / total_value) - target_weight)
        for code in target_codes
    ]
    cash_weight = max(0.0, cash_left) / total_value
    return (max(*deviations, cash_weight), sum(deviations) + cash_weight, max(0.0, cash_left))


def stock_fee_estimate(targets: dict[str, int], holdings: dict[str, int], target_codes: list[str], prices: dict[str, float]) -> float:
    deltas = [
        (targets[code] - holdings.get(code, 0)) * prices[code]
        for code in target_codes
    ]
    exits = [holdings[code] * prices[code] for code in holdings if code not in targets]
    nonzero_orders = sum(bool(amount) for amount in deltas) + len(exits)
    sell_amount = sum(-amount for amount in deltas if amount < 0) + sum(exits)
    return round(5.0 * nonzero_orders + sell_amount * 0.0006, 2) if nonzero_orders else 0.0


def cb_fee_estimate(targets: dict[str, int], holdings: dict[str, int], target_codes: list[str], prices: dict[str, float]) -> float:
    turnover = sum(abs((targets[code] - holdings.get(code, 0)) * prices[code]) for code in target_codes)
    turnover += sum(holdings[code] * prices[code] for code in holdings if code not in targets)
    return round(max(10.0, turnover * 0.0001), 2) if turnover else 0.0


def size_discrete_targets(
    *,
    target_codes: list[str],
    holdings: dict[str, int],
    prices: dict[str, float],
    cash: float,
    lot: int,
    max_single_weight: float = MAX_SINGLE_WEIGHT,
    fee_estimator: Callable[[dict[str, int]], float] | None = None,
) -> tuple[dict[str, int], dict]:
    """Choose one Top-20 target state under the shared lexicographic policy.

    Sells outside the target set are mandatory and therefore counted in the
    budget before target states are compared.  The target state only changes in
    whole lots and never creates an additional sell merely to spend cash.
    """
    target_codes = list(dict.fromkeys(target_codes))
    if len(target_codes) != TARGET_COUNT:
        raise DiscreteSizingError(
            "CAPACITY_CONFLICT",
            "合格候选必须恰为 Top 20，不能静默降级",
            qualified_count=len(target_codes),
            required_count=TARGET_COUNT,
        )
    if lot <= 0:
        raise ValueError("lot must be positive")

    all_codes = set(target_codes) | set(holdings)
    missing = sorted(code for code in all_codes if code not in prices or prices[code] <= 0)
    if missing:
        raise DiscreteSizingError("MISSING_QUOTE", "缺少报价，无法定额", codes=missing)

    holdings = {code: int(shares) for code, shares in holdings.items()}
    total_value = sum(holdings[code] * prices[code] for code in holdings) + cash
    if total_value <= EPSILON:
        raise DiscreteSizingError("CAPACITY_CONFLICT", "可执行预算不足以形成 Top 20 目标", total_value=total_value)

    cap_value = total_value * max_single_weight
    lot_costs = {code: prices[code] * lot for code in target_codes}
    cap_conflicts = [code for code, cost in lot_costs.items() if cost > cap_value + EPSILON]
    minimum_required = sum(lot_costs.values())
    if cap_conflicts or minimum_required > total_value + EPSILON:
        raise DiscreteSizingError(
            "CAPACITY_CONFLICT",
            "可执行预算无法让 Top 20 各至少持有一手且满足单只 10% 上限",
            cap_conflicts=cap_conflicts,
            minimum_required=round(minimum_required, 2),
            total_value=round(total_value, 2),
        )

    per_target = total_value / TARGET_COUNT
    targets = {
        code: max(lot, int(min(per_target, cap_value) / prices[code] // lot) * lot)
        for code in target_codes
    }

    mandatory_sale_proceeds = sum(
        holdings[code] * prices[code] for code in holdings if code not in targets
    )

    def cash_left(candidate: dict[str, int]) -> float:
        target_deltas = sum(
            (candidate[code] - holdings.get(code, 0)) * prices[code]
            for code in target_codes
        )
        return cash + mandatory_sale_proceeds - target_deltas

    def fees(candidate: dict[str, int]) -> float:
        return round(max(0.0, fee_estimator(candidate) if fee_estimator else 0.0), 2)

    def available_cash(candidate: dict[str, int]) -> float:
        return cash_left(candidate) - fees(candidate)

    def ordinary_order_count(candidate: dict[str, int]) -> int:
        return len([code for code in target_codes if candidate[code] != holdings.get(code, 0)]) + len(
            [code for code in holdings if code not in candidate]
        )

    def candidate_score(candidate: dict[str, int]) -> tuple[float, float, float, float, int]:
        available = available_cash(candidate)
        return (*score_targets(
            candidate,
            target_codes=target_codes,
            prices=prices,
            cash_left=available,
        ), fees(candidate), ordinary_order_count(candidate))

    def target_balance_score(candidate: dict[str, int]) -> tuple[float, float]:
        """Measure target-only equality against the frozen portfolio value.

        ``candidate_score`` rightly gives the cash virtual position the same
        priority as every security.  It must not, however, be used as the
        *path* selector for one-lot residual allocation: while cash is large,
        it can repeatedly select the highest-priced security simply because
        that one lot reduces cash the fastest.  This score picks the next lot
        that leaves the actual target positions most even; feasibility and the
        public global comparator are still checked separately.
        """
        target_weight = 1 / len(target_codes)
        deviations = [
            abs((candidate[code] * prices[code] / total_value) - target_weight)
            for code in target_codes
        ]
        return max(deviations), sum(deviations)

    # If floor rounding still spends too much, choose the decrement that gives
    # the best *whole portfolio* state rather than blindly reducing the last
    # ranked security.
    while available_cash(targets) < -EPSILON:
        candidates = []
        for code in target_codes:
            if targets[code] <= lot:
                continue
            trial = targets.copy()
            trial[code] -= lot
            candidates.append((candidate_score(trial), code, trial))
        if not candidates:
            raise DiscreteSizingError("CAPACITY_CONFLICT", "策略目标最低一手无法由可执行预算支持")
        _, _, targets = min(candidates, key=lambda item: (item[0], target_codes.index(item[1])))

    # A next lot is accepted only when it improves the documented global
    # comparator.  Among those feasible moves, choose the target state that
    # is most even *after* the lot.  Ranking purely by the global score here
    # is a greedy local trap: it can keep allocating the most expensive lot
    # to the same security because it shrinks the cash virtual position fastest.
    while True:
        current_score = candidate_score(targets)
        candidates = []
        for code in target_codes:
            if targets[code] * prices[code] + lot_costs[code] > cap_value + EPSILON:
                continue
            trial = targets.copy()
            trial[code] += lot
            if available_cash(trial) < -EPSILON:
                continue
            score = candidate_score(trial)
            if score < current_score:
                candidates.append((target_balance_score(trial), score, code, trial))
        if not candidates:
            break
        _, _, _, targets = min(
            candidates,
            key=lambda item: (item[0], item[1], target_codes.index(item[2])),
        )

    # The preceding allocation grows the portfolio one lot at a time.  A
    # final exchange pass removes any local-greedy concentration that remains
    # because prices and current holdings differ across targets.  Each move is
    # a full one-lot transfer and is accepted only if the documented global
    # comparator strictly improves, so it cannot violate cash or the cap.
    while True:
        current_score = candidate_score(targets)
        candidates = []
        for source in target_codes:
            if targets[source] <= lot:
                continue
            for destination in target_codes:
                if destination == source:
                    continue
                if targets[destination] * prices[destination] + lot_costs[destination] > cap_value + EPSILON:
                    continue
                trial = targets.copy()
                trial[source] -= lot
                trial[destination] += lot
                if available_cash(trial) < -EPSILON:
                    continue
                score = candidate_score(trial)
                if score < current_score:
                    candidates.append((score, source, destination, trial))
        if not candidates:
            break
        _, _, _, targets = min(
            candidates,
            key=lambda item: (item[0], target_codes.index(item[1]), target_codes.index(item[2])),
        )

    left_before_fees = cash_left(targets)
    estimated_fees = fees(targets)
    left = left_before_fees - estimated_fees
    metrics = score_targets(
        targets,
        target_codes=target_codes,
        prices=prices,
        cash_left=left,
    )
    return targets, {
        "total_value": round(total_value, 2),
        "holdings_value": round(total_value - cash, 2),
        "cash_in": round(cash, 2),
        "per_target": round(per_target, 2),
        "cash_left": round(left, 2),
        "cash_left_before_fees": round(left_before_fees, 2),
        "estimated_fees": estimated_fees,
        "n_target": len(target_codes),
        "target_slot_count": TARGET_COUNT,
        "max_overall_deviation": metrics[0],
        "total_overall_deviation": metrics[1],
        "uninvestable_cash": max(0.0, round(left, 2)),
        "mandatory_sale_proceeds": round(mandatory_sale_proceeds, 2),
    }
