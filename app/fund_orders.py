"""场内基金预算换算；使用显式录入的限价、交易单位和券商费用。"""
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
        elif any(term.get(key) is None for key in ("limit_price", "lot_size", "commission_rate", "minimum_fee")):
            blocked[direction] = "限价、交易单位或券商费用尚未核实"
        elif term["limit_price"] <= 0 or not term.get("cost_reviewed"):
            blocked[direction] = "尚未确认该限价及溢价成本可接受"
        elif term.get("price_date") != plan_date:
            blocked[direction] = f"交易条件日期须与计划基准日{plan_date}一致"
    return blocked


def build_fund_orders(account: dict, transfer: dict, plan_date: str) -> dict:
    terms = {item["direction"]: item for item in account.get("fund_terms", [])}
    positions = {item["code"]: item["quantity"] for item in account.get("fund_positions", [])}
    blocked = blocked_directions(account, plan_date)
    orders = []
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
            quantity = max(0, floor(min((budget - term["minimum_fee"]) / price,
                                        budget / (price * (1 + term["commission_rate"]))) / lot) * lot)
        elif sell_budget:
            quantity = -min(floor(sell_budget / price / lot) * lot, current_quantity // lot * lot)
        if quantity:
            amount = money(abs(quantity) * price)
            fee = money(max(term["minimum_fee"], amount * term["commission_rate"]))
            # 分位取整也不能让支出超过预算。
            if quantity > 0 and amount + fee > budget:
                quantity -= lot
                amount = money(quantity * price)
                fee = money(max(term["minimum_fee"], amount * term["commission_rate"])) if quantity else 0
            if quantity:
                orders.append({
                    "direction": direction, "code": term["code"], "name": "501312" if direction == "technology" else "纳指方向",
                    "action": ("ADD" if current_quantity else "BUY") if quantity > 0 else "TRIM",
                    "shares": quantity, "delta_shares": quantity, "price": price,
                    "amount": amount, "estimated_fee": fee, "price_date": term["price_date"],
                    "commission_rate": term["commission_rate"], "minimum_fee": term["minimum_fee"],
                    "current_quantity": current_quantity, "target_quantity": current_quantity + quantity,
                    "price_basis": "人工核实限价；买入不高于此价，卖出不低于此价",
                    "funding_state": "needs_same_day_transfer" if any(a["target"] == "pingan" for a in transfer["actions"]) else "ready",
                })
        remainders[direction] = money(budget - (quantity * price + fee if quantity > 0 else 0))
    spent = money(sum(o["amount"] + o["estimated_fee"] for o in orders if o["shares"] > 0))
    assert spent <= money(sum(transfer["buy_budgets"].get(k, 0) for k in ("nasdaq", "technology")))
    return {"data_date": plan_date, "orders": orders, "blocked": blocked, "unused_budget": remainders,
            "summary": {"buy_cost": spent, "estimated_fees": money(sum(o["estimated_fee"] for o in orders))}}


def reconcile_fund_return(account: dict, transfer: dict, funds: dict) -> dict:
    """回池不能超过实际整手订单扣费后可释放的金额。保持原预算不二次分配。"""
    from copy import deepcopy

    result = deepcopy(transfer)
    incoming = sum(a["amount"] for a in result["actions"] if a["target"] == "pingan")
    net_orders = sum((1 if o["shares"] < 0 else -1) * o["amount"] - o["estimated_fee"]
                     for o in funds["orders"])
    releasable = max(0.0, money(account["pingan_available_cash"] + incoming + net_orders))
    for action in result["actions"]:
        if action["source"] == "pingan":
            action["amount"] = min(action["amount"], releasable)
    result["actions"] = [a for a in result["actions"] if a["amount"] > 0]
    return result
