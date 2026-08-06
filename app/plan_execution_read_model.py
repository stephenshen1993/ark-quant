from __future__ import annotations


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


def build_execution_read_model(
    *,
    plan_date: str,
    account_read_model: dict | None,
    fund_transfer: dict | None,
    cb: dict | None,
    stock: dict | None,
) -> dict:
    funding_actions = _raw_funding_actions(fund_transfer)
    return {
        "plan_date": plan_date,
        "funding_plan": _funding_plan(funding_actions, account_read_model),
        "account_trading_plans": _account_trading_plans(
            account_read_model=account_read_model,
            funding_actions=funding_actions,
            sections=(("stock", stock), ("cb", cb)),
        ),
    }


def _funding_plan(
    actions: list[dict],
    account_read_model: dict | None,
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
            "cash_effect": action.get("cash_effect"),
        })

    groups = [
        {
            "availability": availability,
            "label": label,
            "actions": grouped[availability],
        }
        for availability, label in AVAILABILITY_LABELS.items()
        if grouped[availability]
    ]
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
    account_read_model: dict | None,
    funding_actions: list[dict],
    sections: tuple[tuple[str, dict | None], ...],
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
    for strategy, section in sections:
        section = section or {}
        orders = [
            normalized
            for order in section.get("orders") or []
            if (normalized := _normalize_order(strategy, order)) is not None
        ]
        if not orders:
            continue

        account_id = _account_id(strategy, read_model)
        account = account_by_id.get(account_id) or {}
        related_actions = [
            action
            for action in funding_actions
            if account_id
            in {
                _account_id(action.get("source"), read_model),
                _account_id(action.get("target"), read_model),
            }
        ]
        transfer_in = _money(sum(
            _money(action.get("amount"))
            for action in related_actions
            if _account_id(action.get("target"), read_model) == account_id
        ))
        transfer_out = _money(sum(
            _money(action.get("amount"))
            for action in related_actions
            if _account_id(action.get("source"), read_model) == account_id
        ))
        incoming_availability = [
            action.get("available_on")
            for action in related_actions
            if _account_id(action.get("target"), read_model) == account_id
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
        )
        state, available_on = _funding_state(
            incoming_availability,
            expected_ending,
        )
        phases = [
            {"phase": "sell", "orders": [
                order for order in orders if order["action"] in SELL_ACTIONS
            ]},
            {"phase": "buy", "orders": [
                order for order in orders if order["action"] in BUY_ACTIONS
            ]},
        ]
        phases = [phase for phase in phases if phase["orders"]]
        portfolio = portfolio_by_id.get(account.get("portfolio_id")) or {}
        plans.append({
            "account_id": account_id,
            "account_name": account.get("name") or account_id,
            "portfolio_name": portfolio.get("name") or "",
            "strategy_name": (account.get("strategy_names") or [strategy])[0],
            "trade_date": section.get("trade_date"),
            "funding": {
                "state": state,
                "available_on": available_on,
                "transfer_in": transfer_in,
                "transfer_out": transfer_out,
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
            },
            "cash": {
                "starting_available": starting_available,
                "transfer_in": transfer_in,
                "transfer_out": transfer_out,
                "expected_sell": expected_sell,
                "expected_buy": expected_buy,
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


def _normalize_order(strategy: str, order: dict) -> dict | None:
    action = order.get("action")
    quantity = float(order.get("delta_shares", order.get("shares", 0)) or 0)
    amount = _money(abs(float(order.get("amount") or 0)))
    if action not in SELL_ACTIONS | BUY_ACTIONS or quantity == 0 or amount <= 0:
        return None
    is_cb = strategy == "cb"
    code = order.get("bond_code" if is_cb else "stock_code") or ""
    name = order.get("bond_name" if is_cb else "stock_name") or ""
    price = order.get("price")
    return {
        "action": action,
        "code": code,
        "name": name,
        "quantity": abs(quantity),
        "unit": "张" if is_cb else "股",
        "reference_price": round(float(price), 3) if price is not None else None,
        "estimated_amount": amount,
    }


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
    account_id = _account_id(raw_id, read_model)
    account = account_by_id.get(account_id) or {}
    return account_id or "unknown", account.get("name") or raw_id or "未知账户"


def _account_id(raw_id: str | None, read_model: dict) -> str | None:
    legacy_map = (
        (read_model.get("legacy_adapter") or {}).get("strategy_to_account_id")
        or {}
    )
    aliases = {**legacy_map, "bond": "cb"}
    account_id = aliases.get(raw_id, raw_id)
    if raw_id in {portfolio.get("id") for portfolio in read_model.get("portfolios") or []}:
        portfolio = next(
            item
            for item in read_model.get("portfolios") or []
            if item.get("id") == raw_id
        )
        if len(portfolio.get("account_ids") or []) == 1:
            account_id = portfolio["account_ids"][0]
    return account_id


def _money(value) -> float:
    return round(float(value or 0), 2)
