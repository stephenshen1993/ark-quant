"""Pure account valuation from the same reference prices used by a plan."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import math

from datasource.account_store import build_account_items
from portfolio_rebalance import PlanValidationError


def revalue_plan_account(account: dict, prices: dict, plan_date: str) -> dict:
    if prices.get("data_date") != plan_date:
        raise PlanValidationError("PLAN_PRICE_DATE_MISMATCH", "估值参考价日期与计划日不一致")
    result = deepcopy(account)
    positions = result["account_positions"]

    def value(code, quantity, group):
        price = prices.get(group, {}).get(code)
        try:
            valid = math.isfinite(float(price)) and float(price) > 0
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise PlanValidationError("PLAN_PRICE_SNAPSHOT_INCOMPLETE", f"缺少{plan_date}持仓参考价：{code}")
        return float((Decimal(str(price)) * Decimal(str(quantity))).quantize(
            Decimal(".01"), rounding=ROUND_HALF_UP))

    for carrier, prefix in (("stock", "stock"), ("cb", "bond"), ("pingan", "pingan")):
        items = []
        for item in positions[carrier]:
            code, quantity = item["code"], item["quantity"]
            group = "funds" if carrier == "pingan" else carrier
            amount = value(code, quantity, group)
            items.append({**item, "price": prices[group][code], "market_value": amount})
        pending = sum(t["amount"] for t in result.get("pending_transfers", [])
                      if t["source"] == carrier and t["debited"])
        result[prefix + "_total"] = round(
            sum(item["market_value"] for item in items) + result[prefix + "_cash"] + pending, 2)
        if carrier == "pingan":
            result["fund_positions"] = items
            directions = {"161130": "nasdaq", **{t["code"]: t["direction"] for t in result.get("fund_terms", [])}}
            directions["501312"] = "technology"
            for direction in ("nasdaq", "technology", "unclassified"):
                result[direction + "_total"] = round(sum(
                    item["market_value"] for item in items
                    if directions.get(item["code"], "unclassified") == direction), 2)
    result["total_assets"] = round(sum(result[key] for key in (
        "stock_total", "bond_total", "pingan_total", "cash_pool", "changqian_total", "overseas_total")), 2)
    result["valuation_basis"] = {"type": "plan_date_close_snapshot", "data_date": plan_date}
    result["accounts"] = build_account_items(result)
    return result
