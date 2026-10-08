"""在原订单之外调整现金调拨，不为现金区间新增证券交易。"""
from __future__ import annotations

from copy import deepcopy

from portfolio_rebalance import PlanValidationError
from stage_allocation import CASH_MAX, money


def reconcile_cash_reserves(account: dict, transfer: dict, batches: dict, *, pending_budgets: dict | None = None) -> dict:
    result = deepcopy(transfer)
    if result.get("phase") == "account_rebalance":
        return _reconcile_account_rebalance(account, result, batches)
    for carrier, orders in batches.items():
        pending = (pending_budgets or {}).get(carrier, 0)
        reserve = result.get("cash_reserves", {}).get(carrier, 0) + pending
        upper = CASH_MAX + pending
        prefix = {"stock": "stock", "cb": "bond", "pingan": "pingan"}[carrier]
        actions = result["actions"]
        incoming = sum(a["amount"] for a in actions if a["target"] == carrier)
        outgoing = sum(a["amount"] for a in actions if a["source"] == carrier)
        net = sum((1 if o.get("delta_shares", o.get("shares", 0)) < 0 else -1) * o.get("amount", 0)
                  for o in orders if o.get("delta_shares", o.get("shares", 0)))
        ending = money(account.get(f"{prefix}_available_cash", 0) + incoming - outgoing + net)
        if ending < reserve:
            # 先少回池，仍不足才补入银行现金；不花别的账户待回池款。
            reduction = min(outgoing, reserve - ending)
            outgoing = money(outgoing - reduction)
            ending = money(ending + reduction)
            incoming = money(incoming + max(0, reserve - ending))
        elif ending > upper and not (carrier in {"stock", "cb"} and result.get("phase") == "existing_cash_first"):
            outgoing = money(outgoing + ending - upper)
        if carrier != "pingan":
            # 同一账户原轮动已产生余款时，取消不必要的双向银证调拨。
            offset = min(incoming, outgoing)
            incoming = money(incoming - offset)
            outgoing = money(outgoing - offset)
        actions[:] = [a for a in actions if a["source"] != carrier and a["target"] != carrier]
        for source, target, amount, immediate in (
            ("cash_pool", carrier, incoming, True), (carrier, "cash_pool", outgoing, False),
        ):
            if amount:
                actions.append(dict(source=source, target=target, amount=amount,
                    reason="stage_target_rebalance", immediate=immediate,
                    available_on="same_day" if immediate else "deferred",
                    cash_effect="immediate_cash_in" if immediate else "deferred_cash_return",
                    note="到账后才可买入；含现金预留" if immediate else "卖出与可取核验后回池；保留账户现金区间"))
        if carrier != "pingan":
            result["transfer_deltas"][prefix] = money(incoming - outgoing)
    used = money(sum(a["amount"] for a in result["actions"] if a["source"] == "cash_pool"))
    if used > result["cash"]["available"] + .01:
        raise PlanValidationError("CASH_RESERVE_SHORTFALL", "现有银行现金不足以覆盖订单与现金预留；不能预支待回池资金")
    result["cash"].update(immediate_outflow=used, remaining=money(result["cash"]["available"] - used))
    return result


def _reconcile_account_rebalance(account: dict, result: dict, batches: dict) -> dict:
    """取整余款留本账户；不借现金区间追加顶层调拨。"""
    shortfalls = dict(result.get("return_shortfalls", {}))
    for carrier, orders in batches.items():
        prefix = {"stock": "stock", "cb": "bond", "pingan": "pingan"}[carrier]
        actions = result["actions"]
        incoming = sum(a["amount"] for a in actions if a["target"] == carrier)
        outgoing = sum(a.get("target_amount", a["amount"]) for a in actions if a["source"] == carrier)
        reserved = sum(t["amount"] for t in account.get("pending_transfers", [])
                       if t["source"] == carrier and not t["debited"])
        net = sum((1 if o.get("delta_shares", o.get("shares", 0)) < 0 else -1) * o.get("amount", 0)
                  for o in orders if o.get("delta_shares", o.get("shares", 0)))
        balance = money(account.get(prefix + "_available_cash", 0) - reserved + incoming + net)
        if balance < -.01:
            raise PlanValidationError("CASH_OVERCOMMITTED", "订单超过本账户真实可用预算")
        actual_return = money(min(outgoing, max(0, balance - result["cash_reserves"].get(carrier, 0))))
        shortfalls[carrier] = money(outgoing - actual_return)
        for action in actions:
            if action["source"] == carrier:
                action["target_amount"] = outgoing
                action["amount"] = actual_return
                action["remaining_amount"] = money(outgoing - actual_return)
                action["note"] = "预计卖出后回池；须核验实际成交及可取金额，未到账不支持买入"
        if carrier != "pingan":
            result["transfer_deltas"][prefix] = money(incoming - actual_return - reserved)
    # 订单取整或资金约束缩减回款后，配置表也须展示实际计划与未完成缺口。
    allocation = result["allocation"]
    planned = dict.fromkeys(allocation["current"], 0)
    for action in result["actions"]:
        source = {"cb": "bond"}.get(action["source"], action["source"])
        target = {"cb": "bond"}.get(action["target"], action["target"])
        planned[source] = money(planned[source] - action["amount"])
        planned[target] = money(planned[target] + action["amount"])
    allocation["planned_deltas"] = planned
    for row in allocation["rows"]:
        row["planned_delta"] = planned[row["id"]]
        row["remaining_gap"] = money(row["delta"] - row["arranged_delta"] - row["planned_delta"])
    result["return_shortfalls"] = shortfalls
    result["cash"]["expected_returns"] = money(sum(a["amount"] for a in result["actions"] if a["target"] == "cash_pool") + result["cash"].get("pending_returns", 0))
    result["cash"]["terminal_estimate"] = money(result["cash"]["remaining"] + result["cash"]["expected_returns"] - result["cash"].get("pending_outflow", 0))
    return result
