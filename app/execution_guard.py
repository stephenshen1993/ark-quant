"""Evaluate next-day execution constraints without modifying a frozen plan."""
from __future__ import annotations

from app.plan_execution_read_model import _cb_buy_price_ceiling


BUY_ACTIONS = {"BUY", "ADD"}


def evaluate(plan: dict, realtime: dict) -> dict:
    """Return per-order execute/skip decisions; never resize or replace an order."""
    quote_sets = realtime.get("quotes") or {}
    available_cash = dict(realtime.get("available_cash") or {})
    decisions = []
    orders = []
    for strategy, section, code_key in (
        ("stock", plan.get("stock") or {}, "stock_code"),
        ("cb", plan.get("cb") or {}, "bond_code"),
    ):
        for order in section.get("orders") or []:
            action = order.get("action")
            if action not in {"BUY", "ADD", "SELL", "TRIM"}:
                continue
            code = str(order.get(code_key) or "").zfill(6)
            orders.append((strategy, action, order, code))
    for strategy, action, order, code in sorted(
        orders,
        key=lambda item: (item[0], 0 if item[1] in {"SELL", "TRIM"} else 1),
    ):
        quote = (quote_sets.get(strategy) or {}).get(code)
        decision = _decision(strategy, action, order, quote, available_cash)
        if decision["decision"] == "execute" and action in {"SELL", "TRIM"}:
            available_cash[strategy] = round(
                float(available_cash.get(strategy, 0)) + decision["execution_amount"], 2
            )
        if decision["decision"] == "execute" and action in BUY_ACTIONS:
            available_cash[strategy] = round(
                float(available_cash.get(strategy, 0)) - decision["execution_amount"], 2
            )
        decisions.append({"strategy": strategy, "code": code, **decision})
    return {
        "recalculated": False,
        "replacement_orders": [],
        "decisions": decisions,
    }


def _decision(strategy: str, action: str, order: dict, quote: dict | None, available_cash: dict) -> dict:
    if not isinstance(quote, dict) or quote.get("price") is None:
        return {"decision": "skip", "reason": "QUOTE_UNAVAILABLE"}
    if quote.get("suspended"):
        return {"decision": "skip", "reason": "SUSPENDED"}
    price = float(quote["price"])
    if action in BUY_ACTIONS and quote.get("limit_up") is not None and price >= float(quote["limit_up"]):
        return {"decision": "skip", "reason": "LIMIT_UP"}
    if action in {"SELL", "TRIM"} and quote.get("limit_down") is not None and price <= float(quote["limit_down"]):
        return {"decision": "skip", "reason": "LIMIT_DOWN"}
    if strategy == "cb" and action in BUY_ACTIONS and price >= _cb_buy_price_ceiling():
        return {"decision": "skip", "reason": "CB_BUY_PRICE_CEILING"}
    quantity = abs(float(order.get("delta_shares") or 0))
    amount = round(quantity * price, 2)
    if action in BUY_ACTIONS and amount > float(available_cash.get(strategy, 0)) + 0.01:
        return {"decision": "skip", "reason": "INSUFFICIENT_AVAILABLE_CASH"}
    return {"decision": "execute", "reason": None, "execution_amount": amount}
