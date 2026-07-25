"""Rule-complete target-state sizing for the small-cap stock strategy."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

LOT = 100
TARGET_COUNT = 20
MAX_SINGLE_WEIGHT = 0.10
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


def _order_row(action, code, names, prices, current, target):
    delta = target - current
    amount = round(abs(delta) * prices[code], 2)
    return {
        "action": action,
        "stock_code": code,
        "stock_name": names.get(code, ""),
        "price": round(prices[code], 3),
        "current_shares": int(current),
        "target_shares": int(target),
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
    if target_count <= 0:
        raise SizingError("CAPACITY_CONFLICT", "小市值目标不能为空", target_count=target_count)
    if budget < 0:
        raise SizingError("INSUFFICIENT_RELEASABLE_CASH", "股票可执行预算不能为负", budget=budget)

    held_frame = _normalise(positions, "stock_code") if not positions.empty else positions.copy()
    held = dict(zip(held_frame.get("stock_code", []), held_frame.get("shares", [])))
    target_codes = list(target["stock_code"])
    all_codes = set(target_codes) | set(held)
    missing = sorted(code for code in all_codes if code not in prices or pd.isna(prices[code]) or prices[code] <= 0)
    if missing:
        raise SizingError("MISSING_QUOTE", "缺少股票报价，无法定额", codes=missing)

    holdings_value = sum(int(shares) * prices[code] for code, shares in held.items())
    total_value = round(holdings_value + budget, 2)
    if total_value <= 0:
        raise SizingError("CAPACITY_CONFLICT", "可执行预算不足以形成 Top 20 目标", total_value=total_value)
    cap_value = total_value * MAX_SINGLE_WEIGHT
    minimum_costs = {code: prices[code] * LOT for code in target_codes}
    cap_conflicts = [code for code, cost in minimum_costs.items() if cost > cap_value + 0.01]
    if cap_conflicts or sum(minimum_costs.values()) > total_value + 0.01:
        raise SizingError(
            "CAPACITY_CONFLICT",
            "可执行预算无法让策略目标各至少持有一手且满足单只 10% 上限",
            cap_conflicts=cap_conflicts,
            minimum_required=round(sum(minimum_costs.values()), 2),
            total_value=total_value,
        )

    per_target = total_value / target_count
    desired = {
        code: max(LOT, int(min(per_target, cap_value) / prices[code] // LOT) * LOT)
        for code in target_codes
    }

    def target_value(code):
        return desired[code] * prices[code]

    def cash_left(targets):
        proceeds = sum(held[code] * prices[code] for code in held if code not in targets)
        deltas = sum((targets[code] - held.get(code, 0)) * prices[code] for code in targets)
        return budget + proceeds - deltas

    left = cash_left(desired)
    while left < -0.01:
        candidates = [code for code in target_codes if desired[code] > LOT]
        if not candidates:
            raise SizingError("CAPACITY_CONFLICT", "策略目标最低一手无法由可执行预算支持")
        code = max(candidates, key=lambda item: (target_value(item), target_codes.index(item)))
        desired[code] -= LOT
        left += minimum_costs[code]

    for code in target_codes:
        if target_value(code) > cap_value + 0.01:
            raise SizingError("CAPACITY_CONFLICT", "整手目标超过单只 10% 上限", stock_code=code)

    ordinary_threshold = max(MIN_TRADE_VALUE, per_target * 0.10)
    active_targets = desired.copy()
    required_codes = set(held) - set(target_codes)
    for code in target_codes:
        if held.get(code, 0) * prices[code] > cap_value + 0.01 and desired[code] < held[code]:
            required_codes.add(code)

    for code in set(held) | set(target_codes):
        current = int(held.get(code, 0))
        intended = int(desired.get(code, 0))
        if current == intended or code in required_codes:
            continue
        if abs(intended - current) * prices[code] < ordinary_threshold:
            active_targets[code] = current

    left = cash_left(active_targets)
    rank = {code: index for index, code in enumerate(target_codes)}
    while True:
        candidates = []
        for code in target_codes:
            next_value = prices[code] * LOT
            if active_targets[code] * prices[code] + next_value > cap_value + 0.01:
                continue
            delta_after = (active_targets[code] + LOT - held.get(code, 0)) * prices[code]
            has_existing_ordinary_buy = active_targets[code] > held.get(code, 0) and (active_targets[code] - held.get(code, 0)) * prices[code] >= ordinary_threshold
            if next_value <= left + 0.01 and (has_existing_ordinary_buy or delta_after >= ordinary_threshold):
                candidates.append(code)
        if not candidates:
            break
        code = max(candidates, key=lambda item: ((desired[item] - active_targets[item]) * prices[item], -rank[item]))
        active_targets[code] += LOT
        left -= prices[code] * LOT

    rows = []
    names = dict(zip(target["stock_code"], target.get("stock_name", pd.Series(dtype=str))))
    if not held_frame.empty:
        names.update(dict(zip(held_frame["stock_code"], held_frame.get("stock_name", pd.Series(dtype=str)))))
    for code in sorted(set(held) - set(target_codes)):
        rows.append(_order_row("SELL", code, names, prices, int(held[code]), 0))
    for code in target_codes:
        current, target_shares = int(held.get(code, 0)), int(active_targets[code])
        action = "HOLD"
        if target_shares > current:
            action = "BUY" if current == 0 else "ADD"
        elif target_shares < current:
            action = "TRIM"
        rows.append(_order_row(action, code, names, prices, current, target_shares))

    sheet = pd.DataFrame(rows)
    summary = {
        "total_value": round(total_value, 2),
        "holdings_value": round(holdings_value, 2),
        "cash_in": round(budget, 2),
        "per_target": round(per_target, 2),
        "cash_left": round(left, 2),
        "n_target": target_count,
        "ordinary_order_threshold": round(ordinary_threshold, 2),
        "warnings": [],
    }
    return sheet, summary
