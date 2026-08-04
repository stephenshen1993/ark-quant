"""体系级资金调拨的纯计算。

本模块只根据已经确认的事实快照和人工发起的检查上下文计算目标与
可执行边界；不读取数据库、不产生券商或基金平台动作，也不替账户内
股票、可转债策略决定标的。
"""
from __future__ import annotations

from math import isfinite


class PlanValidationError(ValueError):
    """A plan input cannot safely form a funding plan."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_CHECK_TYPES = {
    "monthly_contribution",
    "quarterly",
    "a_internal",
    "b_recovery",
    "ad_hoc",
}


def build_fund_transfer_plan(
    account: dict,
    context: dict,
    qualified_cb_count: int | None = None,
    *,
    qualified_cb_lot_costs: list[float] | None = None,
    include_a_internal: bool = True,
) -> dict:
    """Build target amounts and safe execution boundaries for one manual check."""
    temperature = _temperature(context.get("temperature"))
    check_type = context.get("check_type", "a_internal")
    if check_type not in _CHECK_TYPES:
        raise PlanValidationError("INVALID_CHECK_TYPE", f"未知检查类型: {check_type}")

    current = _current_amounts(account)
    weights = _top_level_weights(temperature)
    total_assets = sum(current[part] for part in ("A", "B", "C"))
    raw_targets = {part: total_assets * weight for part, weight in weights.items()}
    top_level = _top_level_plan(current, weights, raw_targets, check_type, context)
    cash_available = _nonnegative(context.get("cash_available", account.get("cash_pool", 0)), "cash_available")
    top_immediate_outflow = sum(
        action["amount"] for action in top_level["executed_actions"] if action["immediate"]
    )
    a_internal = (
        _a_internal_plan(
            account=account,
            temperature=temperature,
            a_current=current["A"],
            approved_a_delta=top_level["executed_deltas"]["A"],
            qualified_cb_count=qualified_cb_count,
            qualified_cb_lot_costs=qualified_cb_lot_costs,
            cash_available=max(0.0, cash_available - top_immediate_outflow),
        )
        if include_a_internal
        else _a_internal_not_requested()
    )
    internal_immediate_outflow = sum(
        action["amount"] for action in a_internal.get("actions", []) if action["immediate"]
    )
    immediate_outflow = top_immediate_outflow + internal_immediate_outflow
    return {
        "temperature": temperature,
        "top_level": top_level,
        "a_internal": a_internal,
        "cash": {
            "available": cash_available,
            "immediate_outflow": _money(immediate_outflow),
            "remaining": _money(cash_available - immediate_outflow),
        },
    }


def _a_internal_not_requested() -> dict:
    """Keep account-internal execution out of a pure funding check."""
    return {
        "status": "not_requested",
        "pause_reason": "资金调拨不读取账户内榜单；交易计划生成时再合并账户事实和榜单。",
        "immediate_actions": [],
    }


def _temperature(value: object) -> float:
    try:
        temperature = float(value)
    except (TypeError, ValueError) as exc:
        raise PlanValidationError("INVALID_TEMPERATURE", "温度必须是 0 至 100 的有限数值") from exc
    if not isfinite(temperature) or not 0 <= temperature <= 100:
        raise PlanValidationError("INVALID_TEMPERATURE", "温度必须是 0 至 100 的有限数值")
    return temperature


def _nonnegative(value: object, field: str) -> float:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise PlanValidationError("INVALID_AMOUNT", f"{field} 必须是非负有限数值") from exc
    if not isfinite(amount) or amount < 0:
        raise PlanValidationError("INVALID_AMOUNT", f"{field} 必须是非负有限数值")
    return amount


def _current_amounts(account: dict) -> dict[str, float]:
    stock = _nonnegative(account.get("stock_total"), "stock_total")
    bond = _nonnegative(account.get("bond_total"), "bond_total")
    cash = _nonnegative(account.get("cash_pool"), "cash_pool")
    domestic_long_term = _nonnegative(account.get("changqian_total"), "changqian_total")
    overseas_long_term = _nonnegative(account.get("overseas_total"), "overseas_total")
    return {
        "A": stock + bond + cash,
        "B": overseas_long_term,
        "C": domestic_long_term,
        "stock": stock,
        "bond": bond,
        "cash_pool": cash,
    }


def _top_level_weights(temperature: float) -> dict[str, float]:
    u = min(max(temperature / 50.0, 0.0), 1.0)
    return {"A": 0.70 - 0.05 * u, "B": 0.15 + 0.05 * u, "C": 0.15}


def _top_level_plan(
    current: dict[str, float],
    weights: dict[str, float],
    targets: dict[str, float],
    check_type: str,
    context: dict,
) -> dict:
    total_assets = sum(current[part] for part in ("A", "B", "C"))
    deltas = {part: targets[part] - current[part] for part in targets}
    deviations = {
        part: current[part] / total_assets - weights[part] if total_assets else 0.0
        for part in weights
    }
    bandwidths = {part: min(0.03, weights[part] * 0.10) for part in weights}
    triggered = any(abs(deviations[part]) > bandwidths[part] for part in weights)
    b_limit = _nonnegative(context.get("b_purchase_limit", 0), "b_purchase_limit")
    ideal_actions: list[dict] = []
    executed_actions: list[dict] = []
    outflows: list[dict] = []
    executed_deltas = {"A": 0.0, "B": 0.0, "C": 0.0}

    if check_type == "monthly_contribution":
        budget = min(
            _nonnegative(context.get("new_contribution", 0), "new_contribution"),
            _nonnegative(context.get("cash_available", 0), "cash_available"),
        )
        allocations = _monthly_allocations(deltas, budget, b_limit)
        for target, amount in allocations.items():
            if amount <= 0:
                continue
            executed_actions.append(_inflow_action(target, amount, "monthly_soft_repair", True))
            executed_deltas[target] += amount
            executed_deltas["A"] -= amount
        b_status = _b_purchase_status(deltas["B"], b_limit)
    elif check_type in {"quarterly", "ad_hoc"} and triggered:
        ideal_deltas = _half_band_deltas(current, weights, deviations, bandwidths)
        ideal_actions = [
            _inflow_action(target, amount, "half_band_repair", False)
            for target, amount in ideal_deltas.items()
            if amount >= 1000
        ]
        executable_inflows = _constrained_hard_inflows(ideal_deltas, b_limit)
        for target, amount in executable_inflows.items():
            executed_actions.append(_inflow_action(target, amount, "half_band_repair", False))
            executed_deltas[target] += amount
        for source, amount in ideal_deltas.items():
            if amount >= -1000:
                continue
            outflow_amount = -amount
            outflows.append(_transfer_action(source, "cash_pool", outflow_amount, "half_band_repair", False))
            executed_deltas[source] -= outflow_amount
        b_status = _b_purchase_status(max(ideal_deltas.get("B", 0.0), 0.0), b_limit)
    else:
        b_status = _b_purchase_status(max(deltas["B"], 0.0), b_limit)

    return {
        "status": "ready",
        "check_type": check_type,
        "current": _money_map({part: current[part] for part in ("A", "B", "C")} ),
        "weights": weights,
        "targets": _money_map(targets),
        "deltas": _money_map(deltas),
        "deviations": deviations,
        "bandwidths": bandwidths,
        "hard_rebalance_triggered": triggered if check_type in {"quarterly", "ad_hoc"} else False,
        "old_holding_sales_allowed": check_type in {"quarterly", "ad_hoc"} and triggered,
        "b_purchase_status": b_status,
        "ideal_actions": ideal_actions,
        "executed_actions": executed_actions,
        "executed_deltas": _money_map(executed_deltas),
        "outflows": outflows,
        "inflows": executed_actions,
    }


def _monthly_allocations(deltas: dict[str, float], budget: float, b_limit: float) -> dict[str, float]:
    eligible = {
        "B": min(deltas["B"], b_limit) if b_limit >= 1000 else 0.0,
        "C": deltas["C"],
    }
    eligible = {name: amount for name, amount in eligible.items() if amount >= 1000}
    if not eligible or budget < 1000:
        return {}
    total_gap = sum(eligible.values())
    allocations = {name: min(amount, budget * amount / total_gap) for name, amount in eligible.items()}
    allocations = {name: amount for name, amount in allocations.items() if amount >= 1000}
    return _money_map(allocations)


def _half_band_deltas(
    current: dict[str, float],
    weights: dict[str, float],
    deviations: dict[str, float],
    bandwidths: dict[str, float],
) -> dict[str, float]:
    total_assets = sum(current[part] for part in ("A", "B", "C"))
    ratios = [bandwidths[part] / (2 * abs(deviations[part])) for part in weights if deviations[part] != 0]
    ratio = min(ratios)
    repaired_weights = {part: weights[part] + ratio * deviations[part] for part in weights}
    return {part: repaired_weights[part] * total_assets - current[part] for part in weights}


def _constrained_hard_inflows(ideal_deltas: dict[str, float], b_limit: float) -> dict[str, float]:
    inflows: dict[str, float] = {}
    for target, amount in ideal_deltas.items():
        if amount < 1000:
            continue
        if target == "B":
            amount = min(amount, b_limit) if b_limit >= 1000 else 0.0
        if amount >= 1000:
            inflows[target] = amount
    return inflows


def _b_purchase_status(required_amount: float, limit: float) -> str:
    if required_amount < 1000:
        return "not_required"
    if limit < 1000:
        return "unavailable"
    if limit < required_amount:
        return "partial"
    return "normal"


def _inflow_action(target: str, amount: float, reason: str, immediate: bool) -> dict:
    return _transfer_action(
        source="cash_pool",
        target=target,
        amount=amount,
        reason=reason,
        immediate=immediate,
    )


def _transfer_action(source: str, target: str, amount: float, reason: str, immediate: bool) -> dict:
    if immediate:
        available_on = "same_day"
        cash_effect = "immediate_cash_in" if source == "cash_pool" else "immediate_cash_return"
    else:
        available_on = "next_trading_day" if target == "cash_pool" else "deferred"
        cash_effect = "deferred_cash_return" if target == "cash_pool" else "deferred_cash_in"
    return {
        "source": source,
        "origin": source,
        "target": target,
        "amount": _money(amount),
        "reason": reason,
        "immediate": immediate,
        "available_on": available_on,
        "cash_effect": cash_effect,
    }


def _a_internal_weights(temperature: float) -> dict[str, float]:
    x = min(max((temperature - 50.0) / 50.0, -1.0), 1.0)
    if x <= 0:
        stock = (0.45 - 0.17 * x) / 0.85
        bond = (0.30 + 0.10 * x) / 0.85
        cash = (0.10 + 0.07 * x) / 0.85
    else:
        stock = (0.45 - 0.20 * x) / 0.85
        bond = (0.30 + 0.10 * x) / 0.85
        cash = (0.10 + 0.10 * x) / 0.85
    return {"stock": stock, "bond": bond, "cash_pool": cash}


def _a_internal_plan(
    *,
    account: dict,
    temperature: float,
    a_current: float,
    approved_a_delta: float,
    qualified_cb_count: int | None,
    qualified_cb_lot_costs: list[float] | None,
    cash_available: float,
) -> dict:
    q_out = max(0.0, -approved_a_delta)
    a_exec = a_current - q_out
    if a_exec < 0 or a_exec > a_current:
        raise PlanValidationError("INVALID_A_EXEC", "A 可执行预算超出当前 A 组合金额")

    weights = _a_internal_weights(temperature)
    base_targets = {name: a_exec * weight for name, weight in weights.items()}
    current = {
        "stock": _nonnegative(account.get("stock_total"), "stock_total"),
        "bond": _nonnegative(account.get("bond_total"), "bond_total"),
        "cash_pool": _nonnegative(account.get("cash_pool"), "cash_pool"),
    }
    if qualified_cb_count is None:
        return {
            "status": "paused",
            "pause_reason": "缺少同日完整的可转债榜单，不能确认可投资性安全阀。",
            "a_current": _money(a_current),
            "a_exec": _money(a_exec),
            "q_out": _money(q_out),
            "weights": weights,
            "base_targets": _money_map(base_targets),
            "final_targets": {},
            "current": current,
            "deltas": {},
            "safety_valve_cash": None,
            "immediate_actions": [],
        }

    if not isinstance(qualified_cb_count, int) or qualified_cb_count < 0:
        raise PlanValidationError("INVALID_CB_CANDIDATE_COUNT", "可转债合格候选数必须是非负整数")
    slot_budget = base_targets["bond"] / 20.0
    if qualified_cb_lot_costs is None:
        executable_count = min(qualified_cb_count, 20)
    else:
        lot_costs = [_nonnegative(cost, "qualified_cb_lot_cost") for cost in qualified_cb_lot_costs]
        affordable_count = sum(1 for cost in lot_costs if cost <= slot_budget + 0.01)
        executable_count = min(qualified_cb_count, affordable_count, 20)
    executable_bond_target = base_targets["bond"] * executable_count / 20.0
    safety_valve_cash = base_targets["bond"] - executable_bond_target
    final_targets = {
        "stock": base_targets["stock"],
        "bond": executable_bond_target,
        "cash_pool": base_targets["cash_pool"] + safety_valve_cash,
    }
    deltas = {name: final_targets[name] - current[name] for name in final_targets}
    source_outflows = {
        name: -amount for name, amount in deltas.items()
        if name in {"stock", "bond"} and amount <= -1000
    }
    eligible_inflows = {
        name: amount for name, amount in deltas.items()
        if name in {"stock", "bond"} and amount >= 1000
    }
    inflow_budget = min(cash_available, sum(eligible_inflows.values()))
    planned_inflows: dict[str, float] = {}
    if inflow_budget >= 1000 and eligible_inflows:
        total_gap = sum(eligible_inflows.values())
        planned_inflows = {
            name: min(gap, inflow_budget * gap / total_gap)
            for name, gap in eligible_inflows.items()
        }
        planned_inflows = {
            name: amount for name, amount in planned_inflows.items() if amount >= 1000
        }

    planned_deltas = {"stock": 0.0, "bond": 0.0, "cash_pool": 0.0}
    actions: list[dict] = []
    for source, amount in source_outflows.items():
        planned_deltas[source] -= amount
        actions.append(_transfer_action(source, "cash_pool", amount, "a_internal_rebalance", False))
    for target, amount in planned_inflows.items():
        planned_deltas[target] += amount
        actions.append(_transfer_action("cash_pool", target, amount, "a_internal_rebalance", True))
    planned_deltas["cash_pool"] = -planned_deltas["stock"] - planned_deltas["bond"]

    return {
        "status": "ready" if actions else "within_threshold",
        "a_current": _money(a_current),
        "a_exec": _money(a_exec),
        "q_out": _money(q_out),
        "weights": weights,
        "base_targets": _money_map(base_targets),
        "final_targets": _money_map(final_targets),
        "current": current,
        "deltas": _money_map(deltas),
        "planned_deltas": _money_map(planned_deltas),
        "deferred_gaps": _money_map({
            name: deltas[name] - planned_deltas[name] for name in final_targets
        }),
        "qualified_cb_count": qualified_cb_count,
        "executable_cb_count": executable_count,
        "cb_slot_budget": _money(slot_budget),
        "safety_valve_cash": _money(safety_valve_cash),
        "actions": actions,
        "immediate_actions": [action for action in actions if action["immediate"]],
    }


def _money(value: float) -> float:
    return round(value, 2)


def _money_map(values: dict[str, float]) -> dict[str, float]:
    return {name: _money(value) for name, value in values.items()}
