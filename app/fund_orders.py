"""场内基金预算换算；使用核实限价、交易单位；费用由账户现金区间吸收。"""
from __future__ import annotations

from datetime import date
from math import floor

from datasource.account_store import validate_nonnegative_finite
from datasource.position_store import normalize_security_code
from stage_allocation import money


def normalize_terms(terms: list[dict]) -> list[dict]:
    from app.account_current_state import CurrentAccountError

    result, directions, codes = [], set(), set()
    for raw in terms:
        try:
            direction = raw["direction"]
            code = normalize_security_code(raw["code"])
            if direction not in {"nasdaq", "technology"} or direction in directions or code in codes:
                raise ValueError("每个方向只配置一个执行标的，代码不可重复")
            if (direction == "technology") != (code == "501312"):
                raise ValueError("501312属于独立方向，不能同时抵扣纳指预算")
            item = {"direction": direction, "code": code}
            for key in ("limit_price", "lot_size", "commission_rate", "minimum_fee"):
                value = raw.get(key)
                item[key] = None if value is None else validate_nonnegative_finite(value, key)
            if item["lot_size"] is not None and (item["lot_size"] <= 0 or not item["lot_size"].is_integer()):
                raise ValueError("最小交易单位必须为正整数")
            if item["commission_rate"] is not None and item["commission_rate"] >= 1:
                raise ValueError("佣金费率使用小数，例如万一填写0.0001")
            item["price_date"] = raw.get("price_date")
            if item["price_date"]:
                date.fromisoformat(item["price_date"])
            item["cost_reviewed"] = raw.get("cost_reviewed") is True
            result.append(item)
            directions.add(direction)
            codes.add(code)
        except (KeyError, TypeError, ValueError) as exc:
            raise CurrentAccountError(f"场内基金交易条件无效：{exc}") from exc
    return sorted(result, key=lambda item: item["direction"])


def blocked_directions(account: dict, plan_date: str) -> dict:
    by_direction = {item["direction"]: item for item in account.get("fund_terms", [])}
    blocked = {}
    for direction in ("nasdaq", "technology"):
        term = by_direction.get(direction)
        if not term:
            blocked[direction] = "请在平安账户明确执行标的及交易条件"
        elif any(term.get(key) is None for key in ("limit_price", "lot_size")):
            blocked[direction] = "限价或交易单位尚未核实"
        elif term["limit_price"] <= 0 or (not term.get("cost_reviewed") and not term.get("reference_only")):
            blocked[direction] = "尚未确认该限价及溢价成本可接受"
        elif term.get("price_date") != plan_date:
            blocked[direction] = f"交易条件日期须与计划基准日{plan_date}一致"
    return blocked


def build_fund_orders(account: dict, transfer: dict, plan_date: str) -> dict:
    terms = {item["direction"]: item for item in account.get("fund_terms", [])}
    positions = {item["code"]: item["quantity"] for item in account.get("fund_positions", [])}
    blocked = blocked_directions(account, plan_date)
    orders = []
    reference_only = any(t.get("reference_only") for t in terms.values())
    remainders = {}
    for direction in ("nasdaq", "technology"):
        if direction in blocked:
            continue
        term = terms[direction]
        price, lot = term["limit_price"], int(term["lot_size"])
        budget = transfer["buy_budgets"].get(direction, 0)
        sell_budget = transfer["sell_budgets"].get(direction, 0)
        current_quantity = int(positions.get(term["code"], 0))
        quantity = 0
        fee = 0
        if budget:
            quantity = max(0, floor(budget / price / lot) * lot)
        elif sell_budget:
            quantity = -min(floor(sell_budget / price / lot) * lot, current_quantity // lot * lot)
        if quantity:
            amount = money(abs(quantity) * price)
            if quantity:
                orders.append({
                    "direction": direction, "code": term["code"], "name": "501312" if direction == "technology" else "纳指方向",
                    "action": ("ADD" if current_quantity else "BUY") if quantity > 0 else "TRIM",
                    "shares": quantity, "delta_shares": quantity, "price": price,
                    "amount": amount, "estimated_fee": fee, "price_date": term["price_date"],
                    "commission_rate": term.get("commission_rate"), "minimum_fee": term.get("minimum_fee"),
                    "current_quantity": current_quantity, "target_quantity": current_quantity + quantity,
                    "price_basis": ("计划日参考价，仅计算预算份额；执行前核对盘口与溢价" if term.get("reference_only")
                                    else "人工核实限价；买入不高于此价，卖出不低于此价"),
                    "reference_only": bool(term.get("reference_only")),
                    "quote_timestamp": term.get("quote_timestamp"),
                    "source_url": term.get("source_url"),
                    "funding_state": "needs_same_day_transfer" if any(a["target"] == "pingan" for a in transfer["actions"]) else "ready",
                })
        remainders[direction] = money(budget - (quantity * price + fee if quantity > 0 else 0))
    spent = money(sum(o["amount"] + o["estimated_fee"] for o in orders if o["shares"] > 0))
    assert spent <= money(sum(transfer["buy_budgets"].get(k, 0) for k in ("nasdaq", "technology")))
    return {"data_date": plan_date, "orders": orders, "blocked": blocked, "unused_budget": remainders,
            "reference_only": reference_only,
            "review_required": "参考工具及收盘价用于预算草案，实际委托前核对盘口、最新净值日期和溢价；不要求用户提供公开行情。" if reference_only else None,
            "pending_buy_budget": money(sum(transfer["buy_budgets"].get(k, 0) for k in blocked)),
            "summary": {"buy_cost": spent, "estimated_fees": money(sum(o["estimated_fee"] for o in orders))}}


def reconcile_fund_return(account: dict, transfer: dict, funds: dict) -> dict:
    """整手订单后以共享账户现金区间核对回池金额。"""
    from app.cash_reserve import reconcile_cash_reserves

    result = reconcile_cash_reserves(account, transfer, {"pingan": funds["orders"]},
                                     pending_budgets={"pingan": funds.get("pending_buy_budget", 0)})
    return add_migration_redemption(account, result, funds["orders"])


def add_migration_redemption(account: dict, transfer: dict, orders: list[dict]) -> dict:
    """有条件的赎回草案：成交后按实际净新增投入申请，不能视作已到账现金。"""
    from copy import deepcopy

    result = deepcopy(transfer)
    result["actions"] = [a for a in result["actions"] if a.get("reason") != "matched_migration_redemption"]
    # 基金内部卖出换入不是新增投入；整手余款、现金预留也不是投入。
    net_buy = money(max(0, sum((1 if o.get("delta_shares", 0) > 0 else -1) * o["amount"]
                              for o in orders if o.get("delta_shares", 0))))
    pending = money(account.get("changqian_pending", 0) + account.get("overseas_pending", 0))
    remaining = money(max(0, net_buy - pending))
    amounts = {}
    for source in ("changqian", "overseas"):
        holding = max(0, account.get(source + "_total", 0) - account.get(source + "_pending", 0))
        amount = money(min(holding, remaining))
        amounts[source] = amount
        remaining = money(remaining - amount)
        if amount > 0:
            result["actions"].append(dict(source=source, target="cash_pool", amount=amount,
                reason="matched_migration_redemption", immediate=False, available_on="deferred",
                cash_effect="deferred_cash_return", conditional=True,
                note="成交后按实际净新增投入，扣除已有赎回在途；国内长钱优先，不足再赎海外长钱。少买少赎、未买不赎，不计本期购买力。"))
        if source in result.get("deferred_reductions", {}):
            result["deferred_reductions"][source] = money(max(0, holding - amount))
    result["migration_redemption"] = dict(reference_amount=money(sum(amounts.values())),
        amounts=amounts, planned_net_investment=net_buy, existing_pending=pending,
        conditional=True)
    return result
