"""在原订单之外调整现金调拨，不为现金区间新增证券交易。"""
from __future__ import annotations

from copy import deepcopy

from portfolio_rebalance import PlanValidationError
from stage_allocation import CASH_MAX, money


def reconcile_cash_reserves(account: dict, transfer: dict, batches: dict, *, pending_budgets: dict | None = None) -> dict:
    result = deepcopy(transfer)
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
