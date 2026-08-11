"""Rule-complete target-state sizing for the small-cap stock strategy."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.discrete_target_sizing import (
    DiscreteSizingError,
    MAX_SINGLE_WEIGHT as COMMON_MAX_SINGLE_WEIGHT,
    TARGET_COUNT as COMMON_TARGET_COUNT,
    score_targets,
    size_discrete_targets,
    stock_fee_estimate,
)

LOT = 100
TARGET_COUNT = COMMON_TARGET_COUNT
MAX_SINGLE_WEIGHT = COMMON_MAX_SINGLE_WEIGHT
MIN_TRADE_VALUE = 1_000.0


@dataclass
class SizingError(ValueError):
    code: str
    message: str
    details: dict

    def __init__(self, code: str, message: str, **details):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details


def _normalise(frame: pd.DataFrame, code_column: str) -> pd.DataFrame:
    result = frame.copy()
    result[code_column] = result[code_column].astype(str).str.extract(r"(\d{6})", expand=False).str.zfill(6)
    return result


def _order_row(
    action,
    code,
    names,
    prices,
    current,
    target,
    *,
    ideal_target=None,
    execution_reason="frozen_target",
):
    delta = target - current
    amount = round(abs(delta) * prices[code], 2)
    ideal_target = target if ideal_target is None else ideal_target
    return {
        "action": action,
        "stock_code": code,
        "stock_name": names.get(code, ""),
        "price": round(prices[code], 3),
        "current_shares": int(current),
        "target_shares": int(target),
        "ideal_target_shares": int(ideal_target),
        "executable_target_shares": int(target),
        "residual_shares": int(ideal_target - target),
        "execution_reason": execution_reason,
        "delta_shares": int(delta),
        "amount": amount,
        "est_cost": 0.0,
    }


def size_target_state(
    rankings: pd.DataFrame,
    positions: pd.DataFrame,
    budget: float,
    prices: dict[str, float],
) -> tuple[pd.DataFrame, dict]:
    """Generate one net order per code from the formal Top-20 target-state rules."""
    target = _normalise(rankings, "stock_code").sort_values("rank")
    target = target.drop_duplicates("stock_code", keep="first")
    target_count = len(target)
    if target_count != TARGET_COUNT:
        raise SizingError(
            "CAPACITY_CONFLICT",
            "小市值合格候选必须恰为 Top 20，不能静默降级",
            qualified_count=target_count,
            required_count=TARGET_COUNT,
        )
    held_frame = _normalise(positions, "stock_code") if not positions.empty else positions.copy()
    held = dict(zip(held_frame.get("stock_code", []), held_frame.get("shares", [])))
    target_codes = list(target["stock_code"])
    try:
        desired, shared_summary = size_discrete_targets(
            target_codes=target_codes,
            holdings=held,
            prices=prices,
            cash=budget,
            lot=LOT,
            max_single_weight=MAX_SINGLE_WEIGHT,
            fee_estimator=lambda candidate: stock_fee_estimate(candidate, held, target_codes, prices),
        )
    except DiscreteSizingError as exc:
        raise SizingError(exc.code, exc.message, **exc.details) from exc

    holdings_value = shared_summary["holdings_value"]
    total_value = shared_summary["total_value"]
    cap_value = total_value * MAX_SINGLE_WEIGHT
    per_target = total_value / target_count

    def cash_left(targets):
        proceeds = sum(held[code] * prices[code] for code in held if code not in targets)
        deltas = sum((targets[code] - held.get(code, 0)) * prices[code] for code in targets)
        return budget + proceeds - deltas

    def available_cash(targets):
        return cash_left(targets) - stock_fee_estimate(targets, held, target_codes, prices)

    ordinary_threshold = max(MIN_TRADE_VALUE, per_target * 0.10)
    active_targets = desired.copy()
    required_codes = set(held) - set(target_codes)
    for code in target_codes:
        if held.get(code, 0) * prices[code] > cap_value + 0.01 and desired[code] < held[code]:
            required_codes.add(code)

    for code in sorted(set(held) | set(target_codes)):
        current = int(held.get(code, 0))
        intended = int(desired.get(code, 0))
        if current == intended or code in required_codes:
            continue
        if abs(intended - current) * prices[code] < ordinary_threshold:
            # The execution threshold is an optional friction reduction, not
            # permission to invalidate the feasible target state selected by
            # the common allocator.  In particular, a small trim can be the
            # cash release that makes an otherwise larger buy executable.
            trial = active_targets.copy()
            trial[code] = current
            if available_cash(trial) >= -0.01:
                active_targets = trial

    left = available_cash(active_targets)
    rank = {code: index for index, code in enumerate(target_codes)}
    while True:
        candidates = []
        for code in target_codes:
            next_value = prices[code] * LOT
            if active_targets[code] * prices[code] + next_value > cap_value + 0.01:
                continue
            delta_after = (active_targets[code] + LOT - held.get(code, 0)) * prices[code]
            has_existing_ordinary_buy = active_targets[code] > held.get(code, 0) and (active_targets[code] - held.get(code, 0)) * prices[code] >= ordinary_threshold
            trial = active_targets.copy()
            trial[code] += LOT
            if available_cash(trial) >= -0.01 and (has_existing_ordinary_buy or delta_after >= ordinary_threshold):
                candidates.append(code)
        if not candidates:
            break
        current_score = score_targets(
            active_targets,
            target_codes=target_codes,
            prices=prices,
            cash_left=left,
        )
        scored = []
        for code in candidates:
            trial = active_targets.copy()
            trial[code] += LOT
            score = score_targets(
                trial,
                target_codes=target_codes,
                prices=prices,
                cash_left=available_cash(trial),
            )
            if score < current_score:
                scored.append((score, code))
        if not scored:
            break
        _, code = min(scored, key=lambda item: (item[0], rank[item[1]]))
        active_targets[code] += LOT
        left = available_cash(active_targets)

    rows = []
    names = dict(zip(target["stock_code"], target.get("stock_name", pd.Series(dtype=str))))
    if not held_frame.empty:
        names.update(dict(zip(held_frame["stock_code"], held_frame.get("stock_name", pd.Series(dtype=str)))))
    for code in sorted(set(held) - set(target_codes)):
        rows.append(
            _order_row(
                "SELL", code, names, prices, int(held[code]), 0,
                ideal_target=0,
                execution_reason="mandatory_exit",
            )
        )
    for code in target_codes:
        current, target_shares = int(held.get(code, 0)), int(active_targets[code])
        action = "HOLD"
        if target_shares > current:
            action = "BUY" if current == 0 else "ADD"
        elif target_shares < current:
            action = "TRIM"
        reason = "frozen_target"
        if code in required_codes:
            reason = "mandatory_risk_reduction"
        elif target_shares != desired[code]:
            reason = "ordinary_order_below_threshold"
        rows.append(
            _order_row(
                action, code, names, prices, current, target_shares,
                ideal_target=desired[code],
                execution_reason=reason,
            )
        )

    sheet = pd.DataFrame(rows)
    summary = {
        "total_value": round(total_value, 2),
        "holdings_value": round(holdings_value, 2),
        "cash_in": round(budget, 2),
        "per_target": round(per_target, 2),
        "cash_left": round(left, 2),
        "cash_left_before_fees": round(cash_left(active_targets), 2),
        "estimated_fees": stock_fee_estimate(active_targets, held, target_codes, prices),
        "n_target": target_count,
        "ordinary_order_threshold": round(ordinary_threshold, 2),
        "max_overall_deviation": score_targets(
            active_targets,
            target_codes=target_codes,
            prices=prices,
            cash_left=left,
        )[0],
        "total_overall_deviation": score_targets(
            active_targets,
            target_codes=target_codes,
            prices=prices,
            cash_left=left,
        )[1],
        "uninvestable_cash": max(0.0, round(left, 2)),
        "warnings": [],
    }
    return sheet, summary
