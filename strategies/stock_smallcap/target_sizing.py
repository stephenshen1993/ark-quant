"""Rule-complete target-state sizing for the small-cap stock strategy."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.discrete_target_sizing import (
    DiscreteSizingError,
    MAX_SINGLE_WEIGHT as COMMON_MAX_SINGLE_WEIGHT,
    TARGET_COUNT as COMMON_TARGET_COUNT,
)
from strategies.stock_smallcap.milp_target_sizing import solve_stock_targets

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
    target = _normalise(rankings, "stock_code").sort_values(["rank", "stock_code"])
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
    missing_prices = sorted(
        code
        for code in set(target_codes) | set(held)
        if code not in prices or prices[code] <= 0
    )
    if missing_prices:
        raise SizingError("MISSING_QUOTE", "缺少报价，无法定额", codes=missing_prices)
    holdings_value = sum(int(shares) * prices[code] for code, shares in held.items())
    total_value = holdings_value + budget
    per_target = total_value / target_count
    ordinary_threshold = max(MIN_TRADE_VALUE, per_target * 0.10)
    try:
        optimized = solve_stock_targets(
            target_codes=target_codes,
            holdings=held,
            prices=prices,
            cash=budget,
            lot=LOT,
            max_single_weight=MAX_SINGLE_WEIGHT,
            ordinary_order_threshold=ordinary_threshold,
        )
    except DiscreteSizingError as exc:
        raise SizingError(exc.code, exc.message, **exc.details) from exc

    active_targets = optimized.targets
    optimizer_summary = optimized.summary
    cap_value = total_value * MAX_SINGLE_WEIGHT
    required_codes = set(held) - set(target_codes)
    for code in target_codes:
        if (
            held.get(code, 0) * prices[code] > cap_value + 0.01
            and active_targets[code] < held[code]
        ):
            required_codes.add(code)

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
        elif target_shares < current:
            raise SizingError(
                "SOLVER_NOT_OPTIMAL",
                "股票定额器生成了未经策略授权的普通卖出",
                code=code,
                current_shares=current,
                target_shares=target_shares,
            )
        rows.append(
            _order_row(
                action, code, names, prices, current, target_shares,
                ideal_target=target_shares,
                execution_reason=reason,
            )
        )

    sheet = pd.DataFrame(rows)
    summary = {
        **optimizer_summary,
        "ordinary_order_threshold": round(ordinary_threshold, 2),
        "warnings": [],
    }
    return sheet, summary
