from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path


AVAILABILITY_LABELS = {
    "same_day": "当日可用",
    "next_trading_day": "下一交易日可用",
    "deferred": "等待未来资金可用",
}

REASON_LABELS = {
    "a_internal_rebalance": "主动组合内部再平衡",
    "half_band_repair": "组合偏离修复",
    "monthly_soft_repair": "新增资金柔性补偏",
}

FUNDING_STATE_ORDER = {
    "ready": 0,
    "needs_same_day_transfer": 1,
    "waits_for_funds": 2,
    "blocked": 3,
}

SELL_ACTIONS = {"SELL", "TRIM"}
BUY_ACTIONS = {"BUY", "ADD"}
ACTION_EXECUTION_ORDER = {
    "SELL": 0,
    "TRIM": 1,
    "BUY": 2,
    "ADD": 3,
}


@lru_cache(maxsize=1)
def _cb_buy_price_ceiling() -> float:
    """Return the strategy's configured hard execution ceiling."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "cb_rotation.json"
    with config_path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    return float(config["filters"]["max_cb_price"])


def build_execution_read_model(
    *,
    plan_date: str,
    account_read_model: dict | None,
    fund_transfer: dict | None,
    cb: dict | None,
    stock: dict | None,
) -> dict:
    read_model = account_read_model or {}
    funding_actions = _raw_funding_actions(fund_transfer)
    execution_date = _execution_date(stock, cb)
    return {
        "plan_date": plan_date,
        "funding_plan": _funding_plan(
            funding_actions,
            account_read_model,
            execution_date,
        ),
        "account_trading_plans": _account_trading_plans(
            plan_date=plan_date,
            account_read_model=account_read_model,
            funding_actions=funding_actions,
            sections=(
                {
                    "strategy_id": "stock",
                    "account_id": _account_id_for_strategy("stock", read_model),
                    "plan": stock,
                },
                {
                    "strategy_id": "cb",
                    "account_id": _account_id_for_strategy("cb", read_model),
                    "plan": cb,
                },
            ),
        ),
    }


def _funding_plan(
    actions: list[dict],
    account_read_model: dict | None,
    execution_date: str | None,
) -> dict | None:
    grouped = {availability: [] for availability in AVAILABILITY_LABELS}
    for action in actions:
        amount = _money(action.get("amount"))
        availability = action.get("available_on")
        if availability not in grouped:
            continue
        source_id, source_name = _account_identity(
            action.get("source"), account_read_model
        )
        target_id, target_name = _account_identity(
            action.get("target"), account_read_model
        )
        available_date = _availability_date(
            availability,
            execution_date,
            action.get("available_date"),
        )
        grouped[availability].append({
            "source_account_id": source_id,
            "source_account_name": source_name,
            "target_account_id": target_id,
            "target_account_name": target_name,
            "amount": amount,
            "reason": action.get("reason"),
            "reason_label": REASON_LABELS.get(
                action.get("reason"), "按计划完成资金调拨"
            ),
            "availability": availability,
            "available_date": available_date,
            "display_date": available_date or "日期待确认",
            "cash_effect": action.get("cash_effect"),
        })

    groups = []
    for availability, label in AVAILABILITY_LABELS.items():
        group_actions = grouped[availability]
        if not group_actions:
            continue
        dates = {action["available_date"] for action in group_actions}
        available_date = next(iter(dates)) if len(dates) == 1 else None
        display_date = (
            (available_date or "日期待确认")
            if len(dates) == 1
            else "多个日期"
        )
        groups.append({
            "availability": availability,
            "label": label,
            "available_date": available_date,
            "display_date": display_date,
            "actions": group_actions,
        })
    return {"groups": groups} if groups else None


def _raw_funding_actions(fund_transfer: dict | None) -> list[dict]:
    if not fund_transfer:
        return []
    top_level = fund_transfer.get("top_level") or {}
    internal = fund_transfer.get("a_internal") or {}
    actions = (
        list(top_level.get("executed_actions") or [])
        + list(top_level.get("outflows") or [])
        + list(internal.get("actions") or [])
    )
    return [
        action
        for action in actions
        if _money(action.get("amount")) > 0
        and "A" not in {action.get("source"), action.get("target")}
    ]


def _account_trading_plans(
    *,
    plan_date: str,
    account_read_model: dict | None,
    funding_actions: list[dict],
    sections: tuple[dict, ...],
) -> list[dict]:
    read_model = account_read_model or {}
    accounts = read_model.get("accounts") or []
    account_by_id = {account["id"]: account for account in accounts}
    portfolio_by_id = {
        portfolio["id"]: portfolio
        for portfolio in read_model.get("portfolios") or []
    }
    account_order = {
        account["id"]: index for index, account in enumerate(accounts)
    }
    plans = []
    resolved_actions = [
        (
            action,
            _account_id_for_transfer_endpoint(action.get("source"), read_model),
            _account_id_for_transfer_endpoint(action.get("target"), read_model),
        )
        for action in funding_actions
    ]
    for section_spec in sections:
        strategy = section_spec["strategy_id"]
        account_id = section_spec["account_id"]
        section = section_spec.get("plan") or {}
        orders = [
            normalized
            for order in section.get("orders") or []
            if (
                normalized := _normalize_order(
                    strategy,
                    order,
                    price_basis_date=section.get("data_date") or plan_date,
                )
            ) is not None
        ]
        if not orders:
            continue

        account = account_by_id.get(account_id) or {}
        related_actions = [
            item
            for item in resolved_actions
            if account_id in {item[1], item[2]}
        ]
        transfer_in = _money(sum(
            _money(action.get("amount"))
            for action, _, target_account_id in related_actions
            if target_account_id == account_id
        ))
        transfer_out = _money(sum(
            _money(action.get("amount"))
            for action, source_account_id, _ in related_actions
            if source_account_id == account_id
        ))
        incoming_availability = [
            action.get("available_on")
            for action, _, target_account_id in related_actions
            if target_account_id == account_id
        ]
        incoming_actions = [
            _account_prerequisite_action(
                action,
                account_read_model=account_read_model,
                execution_date=section.get("trade_date"),
            )
            for action, _, target_account_id in related_actions
            if target_account_id == account_id
        ]
        expected_sell = _money(sum(
            order["estimated_amount"]
            for order in orders
            if order["action"] in SELL_ACTIONS
        ))
        expected_buy = _money(sum(
            order["estimated_amount"]
            for order in orders
            if order["action"] in BUY_ACTIONS
        ))
        summary = section.get("summary") or {}
        estimated_fees = _money(summary.get("estimated_fees"))
        starting_available = summary.get("starting_cash")
        if starting_available is None:
            starting_available = account.get("available_cash")
        starting_available = _money(starting_available)
        expected_ending = _money(
            starting_available
            + transfer_in
            - transfer_out
            + expected_sell
            - expected_buy
            - estimated_fees
        )
        state, available_on = _funding_state(
            incoming_availability,
            expected_ending,
        )
        available_date, display_date = _account_funding_date(
            state=state,
            availability=available_on,
            execution_date=section.get("trade_date"),
            incoming_actions=[
                action
                for action, _, target_account_id in related_actions
                if target_account_id == account_id
            ],
        )
        phases = [
            {"phase": "sell", "orders": [
                order
                for order in sorted(orders, key=_order_execution_key)
                if order["action"] in SELL_ACTIONS
            ]},
            {"phase": "buy", "orders": [
                order
                for order in sorted(orders, key=_order_execution_key)
                if order["action"] in BUY_ACTIONS
            ]},
        ]
        phases = [phase for phase in phases if phase["orders"]]
        portfolio = portfolio_by_id.get(account.get("portfolio_id")) or {}
        plans.append({
            "account_id": account_id,
            "account_name": account.get("name") or account_id,
            "portfolio_name": portfolio.get("name") or "",
            "strategy_id": strategy,
            "strategy_name": (account.get("strategy_names") or [strategy])[0],
            "trade_date": section.get("trade_date"),
            "execution_guardrails": _execution_guardrails(
                strategy=strategy,
                plan_date=plan_date,
                section=section,
            ),
            "funding": {
                "state": state,
                "available_on": available_on,
                "available_date": available_date,
                "display_date": display_date,
                "transfer_in": transfer_in,
                "transfer_out": transfer_out,
                "incoming_actions": incoming_actions,
                "blocked_reason": (
                    "计划后预计资金余额不足" if state == "blocked" else None
                ),
            },
            "trade_summary": {
                "sell_count": sum(
                    1 for order in orders if order["action"] in SELL_ACTIONS
                ),
                "sell_estimated_amount": expected_sell,
                "buy_count": sum(
                    1 for order in orders if order["action"] in BUY_ACTIONS
                ),
                "buy_estimated_amount": expected_buy,
                "estimated_fees": estimated_fees,
            },
            "cash": {
                "starting_available": starting_available,
                "transfer_in": transfer_in,
                "transfer_out": transfer_out,
                "expected_sell": expected_sell,
                "expected_buy": expected_buy,
                "estimated_fees": estimated_fees,
                "expected_ending": expected_ending,
            },
            "phases": phases,
        })

    return sorted(
        plans,
        key=lambda plan: (
            FUNDING_STATE_ORDER[plan["funding"]["state"]],
            account_order.get(plan["account_id"], len(account_order)),
        ),
    )


def _account_prerequisite_action(
    action: dict,
    *,
    account_read_model: dict | None,
    execution_date: str | None,
) -> dict:
    source_id, source_name = _account_identity(action.get("source"), account_read_model)
    target_id, target_name = _account_identity(action.get("target"), account_read_model)
    available_date = _availability_date(
        action.get("available_on"),
        execution_date,
        action.get("available_date"),
    )
    return {
        "source_account_id": source_id,
        "source_account_name": source_name,
        "target_account_id": target_id,
        "target_account_name": target_name,
        "amount": _money(action.get("amount")),
        "availability": action.get("available_on"),
        "available_date": available_date,
        "display_date": available_date or "日期待确认",
    }


def _execution_guardrails(
    *,
    strategy: str,
    plan_date: str,
    section: dict,
) -> dict:
    rules = []
    if strategy == "cb":
        rules.append({
            "kind": "buy_price_ceiling",
            "applies_to": ["BUY", "ADD"],
            "comparison": "strictly_below",
            "max_price": _cb_buy_price_ceiling(),
            "check": "at_execution",
        })
    elif strategy == "stock":
        from strategies.stock_smallcap.target_sizing import MAX_SINGLE_WEIGHT

        rules.append({
            "kind": "single_position_cap",
            "max_weight": float(MAX_SINGLE_WEIGHT),
            "check": "validated_at_generation",
        })
    return {
        "price_basis_date": section.get("data_date") or plan_date,
        "reference_price_is_limit": False,
        "rules": rules,
    }


def _normalize_order(
    strategy: str,
    order: dict,
    *,
    price_basis_date: str,
) -> dict | None:
    action = order.get("action")
    quantity = float(order.get("delta_shares", order.get("shares", 0)) or 0)
    amount = _money(abs(float(order.get("amount") or 0)))
    if action not in SELL_ACTIONS | BUY_ACTIONS or quantity == 0 or amount <= 0:
        return None
    is_cb = strategy == "cb"
    code = order.get("bond_code" if is_cb else "stock_code") or ""
    name = order.get("bond_name" if is_cb else "stock_name") or ""
    price = order.get("price")
    current_quantity = _quantity_or_none(order.get("current_shares"))
    target_quantity = _quantity_or_none(order.get("target_shares"))
    ideal_target_quantity = _quantity_or_none(order.get("ideal_target_shares"))
    executable_target_quantity = _quantity_or_none(
        order.get("executable_target_shares", order.get("target_shares"))
    )
    residual_quantity = _quantity_or_none(order.get("residual_shares"))
    reference_price = round(float(price), 3) if price is not None else None
    return {
        "action": action,
        "execution_priority": ACTION_EXECUTION_ORDER[action],
        "code": code,
        "name": name,
        "quantity": abs(quantity),
        "current_quantity": current_quantity,
        "target_quantity": target_quantity,
        "ideal_target_quantity": ideal_target_quantity,
        "executable_target_quantity": executable_target_quantity,
        "residual_quantity": residual_quantity,
        "execution_reason": order.get("execution_reason", "frozen_target"),
        "unit": "张" if is_cb else "股",
        "reference_price": reference_price,
        "price_basis_date": price_basis_date,
        "current_value": _quantity_value(current_quantity, reference_price),
        "ideal_target_value": _quantity_value(ideal_target_quantity, reference_price),
        "executable_target_value": _quantity_value(executable_target_quantity, reference_price),
        "budget_occupancy": amount if action in BUY_ACTIONS else 0.0,
        "max_execution_price": (
            _cb_buy_price_ceiling()
            if is_cb and action in BUY_ACTIONS
            else None
        ),
        "estimated_amount": amount,
    }


def _order_execution_key(order: dict) -> tuple[int, float, int, str]:
    """Order the executable list by cash dependency, then market impact.

    Sells must be visible before buys because their proceeds fund the latter.
    Within either phase, execute the larger estimated amount first to reduce
    the gap between the frozen close-based plan and the next open.
    """
    action = order["action"]
    phase = 0 if action in SELL_ACTIONS else 1
    amount = _money(order.get("estimated_amount"))
    action_order = int(order.get("execution_priority", ACTION_EXECUTION_ORDER[action]))
    return (phase, -amount, action_order, str(order.get("code") or ""))


def _quantity_or_none(value: object) -> int | float | None:
    if value is None:
        return None
    quantity = float(value)
    return int(quantity) if quantity.is_integer() else quantity


def _quantity_value(quantity: int | float | None, price: float | None) -> float | None:
    if quantity is None or price is None:
        return None
    return _money(float(quantity) * price)


def _funding_state(
    incoming_availability: list[str | None],
    expected_ending: float,
) -> tuple[str, str | None]:
    if expected_ending < -0.01:
        return "blocked", _latest_availability(incoming_availability)
    available_on = _latest_availability(incoming_availability)
    if available_on in {"next_trading_day", "deferred"}:
        return "waits_for_funds", available_on
    if available_on == "same_day":
        return "needs_same_day_transfer", available_on
    return "ready", None


def _latest_availability(values: list[str | None]) -> str | None:
    order = {"same_day": 0, "next_trading_day": 1, "deferred": 2}
    known = [value for value in values if value in order]
    return max(known, key=order.__getitem__) if known else None


def _account_identity(
    raw_id: str | None,
    account_read_model: dict | None,
) -> tuple[str, str]:
    read_model = account_read_model or {}
    accounts = read_model.get("accounts") or []
    account_by_id = {account["id"]: account for account in accounts}
    account_id = _account_id_for_transfer_endpoint(raw_id, read_model)
    account = account_by_id.get(account_id) or {}
    return account_id or "unknown", account.get("name") or raw_id or "未知账户"


def _account_id_for_strategy(strategy_id: str, read_model: dict) -> str | None:
    legacy_map = (
        (read_model.get("legacy_adapter") or {}).get("strategy_to_account_id")
        or {}
    )
    account_ids = {account.get("id") for account in read_model.get("accounts") or []}
    mapped_id = legacy_map.get(strategy_id)
    if mapped_id in account_ids:
        return mapped_id
    return strategy_id if strategy_id in account_ids else None


def _account_id_for_transfer_endpoint(raw_id: str | None, read_model: dict) -> str | None:
    if not raw_id:
        return None
    account_ids = {account.get("id") for account in read_model.get("accounts") or []}
    if raw_id in account_ids:
        return raw_id

    strategy_id = {"bond": "cb"}.get(raw_id, raw_id)
    strategy_account_id = _account_id_for_strategy(strategy_id, read_model)
    if strategy_account_id:
        return strategy_account_id

    portfolio = next(
        (
            item
            for item in read_model.get("portfolios") or []
            if item.get("id") == raw_id
        ),
        None,
    )
    portfolio_account_ids = (portfolio or {}).get("account_ids") or []
    return portfolio_account_ids[0] if len(portfolio_account_ids) == 1 else raw_id


def _execution_date(*sections: dict | None) -> str | None:
    return next(
        (
            section.get("trade_date")
            for section in sections
            if section and section.get("trade_date")
        ),
        None,
    )


def _availability_date(
    availability: str | None,
    execution_date: str | None,
    explicit_date: str | None = None,
) -> str | None:
    if explicit_date:
        return explicit_date
    if not execution_date:
        return None
    if availability == "same_day":
        return execution_date
    if availability != "next_trading_day":
        return None
    try:
        next_date = date.fromisoformat(execution_date) + timedelta(days=1)
    except ValueError:
        return None
    while next_date.weekday() >= 5:
        next_date += timedelta(days=1)
    return next_date.isoformat()


def _account_funding_date(
    *,
    state: str,
    availability: str | None,
    execution_date: str | None,
    incoming_actions: list[dict],
) -> tuple[str | None, str]:
    if state == "blocked":
        return None, "尚不可用"
    effective_availability = availability or "same_day"
    explicit_date = next(
        (
            action.get("available_date")
            for action in incoming_actions
            if action.get("available_on") == effective_availability
            and action.get("available_date")
        ),
        None,
    )
    available_date = _availability_date(
        effective_availability,
        execution_date,
        explicit_date,
    )
    return available_date, available_date or "日期待确认"


def _money(value) -> float:
    return round(float(value or 0), 2)
