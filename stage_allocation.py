"""账户目标 → 真实可用资金预算；纯计算，不执行交易。"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

POLICY_ID = "account-rebalance-2026-10-08-v2"
CASH_MIN = 300.0
CASH_MAX = 1000.0  # 旧计划兼容；新规则不因账户零星余款自动回池。
MIN_ADJUSTMENT = 1000.0
WEIGHTS = {"stock": .35, "bond": .25, "pingan": .30, "cash_pool": .10}
LABELS = {"stock": "广发账户", "bond": "华泰账户", "pingan": "平安账户",
          "cash_pool": "资金账户", "changqian": "国内长钱（待退出）",
          "overseas": "海外长钱（待退出）", "pending": "已确认在途"}
CARRIERS = {"stock": "stock", "bond": "cb", "pingan": "pingan"}
TRANSFER_KEYS = {"stock": "stock", "cb": "bond", "pingan": "pingan", "cash": "cash_pool"}


def money(value) -> float:
    return float(Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def required_amount(account: dict, key: str) -> float:
    from portfolio_rebalance import PlanValidationError
    try:
        value = Decimal(str(account[key]))
        if not value.is_finite() or value < 0:
            raise ValueError(key)
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise PlanValidationError("MISSING_ALLOCATION_FACT", f"缺少有效的配置事实：{key}") from exc
    return money(value)


def allocation_amounts(account: dict) -> dict:
    """账户总额包括账户现金；已扣账在途从来源分离，资产只计一次。"""
    from portfolio_rebalance import PlanValidationError
    current = {"cash_pool": required_amount(account, "cash_pool")}
    for key, prefix in (("stock", "stock"), ("bond", "bond"), ("pingan", "pingan")):
        total = required_amount(account, prefix + "_total")
        if required_amount(account, prefix + "_cash") > total:
            raise PlanValidationError("INVALID_CASH", "现金超过账户总额")
        current[key] = total
    pa_parts = sum(required_amount(account, k + "_total") for k in ("nasdaq", "technology", "unclassified"))
    pa_pending = sum(t["amount"] for t in account.get("pending_transfers", []) if t["source"] == "pingan" and t["debited"])
    if abs(current["pingan"] - pa_parts - account["pingan_cash"] - pa_pending) > .02:
        raise PlanValidationError("INVALID_PINGAN_TOTAL", "平安持仓与现金合计不等于账户估值")
    current["pending"] = 0.0
    for key in ("changqian", "overseas"):
        total = required_amount(account, key + "_total")
        pending = required_amount(account, key + "_pending")
        if pending > total:
            raise PlanValidationError("INVALID_PENDING", "在途金额不能超过所属账户总额")
        current[key] = money(total - pending)
        current["pending"] += pending
    for transfer in account.get("pending_transfers", []):
        if transfer["debited"]:
            key = TRANSFER_KEYS[transfer["source"]]
            current[key] = money(current[key] - transfer["amount"])
            current["pending"] += transfer["amount"]
            if current[key] < 0:
                raise PlanValidationError("INVALID_PENDING", "已扣账在途超过账户含在途总额")
    current["pending"] = money(current["pending"])
    return current


def targets_for(current: dict) -> dict:
    total = money(sum(current.values()))
    targets = {k: money(Decimal(str(total)) * Decimal(str(w))) for k, w in WEIGHTS.items()}
    targets["cash_pool"] = money(total - sum(v for k, v in targets.items() if k != "cash_pool"))
    targets.update({k: 0.0 for k in current if k not in targets})
    return targets


def proportional(gaps: dict, budget: float, *, minimum: float = MIN_ADJUSTMENT) -> dict:
    """按分向下取整，小额留存，不递归重分配。"""
    total = sum(Decimal(str(v)) for v in gaps.values())
    scale = min(Decimal(1), Decimal(str(max(0, budget))) / total) if total else Decimal(0)
    result = {}
    for key, gap in gaps.items():
        value = float((Decimal(str(gap)) * scale).quantize(Decimal(".01"), rounding=ROUND_DOWN))
        result[key] = value if value >= minimum else 0.0
    return result


def build_stage_plan(account: dict, *, blocked: dict | None = None) -> dict:
    from portfolio_rebalance import PlanValidationError
    current = allocation_amounts(account)
    targets = targets_for(current)
    projected = dict(current)
    blocked = dict(blocked or {})
    reserved = {k: 0.0 for k in TRANSFER_KEYS}
    # 当期事实的total含已扣账在途；current已分离在途，接收方只用于防重。
    for transfer in account.get("pending_transfers", []):
        source, target, amount = transfer["source"], transfer["target"], transfer["amount"]
        if not transfer["debited"]:
            projected[TRANSFER_KEYS[source]] -= amount
            reserved[source] += amount
        projected[TRANSFER_KEYS[target]] += amount
    available = {"cash": money(current["cash_pool"] - account.get("cash_unavailable", 0) - reserved["cash"])}
    if available["cash"] < 0:
        raise PlanValidationError("CASH_OVERCOMMITTED", "资金账户不可用金额与已安排调拨超过现有余额")
    for carrier, prefix in (("stock", "stock"), ("cb", "bond"), ("pingan", "pingan")):
        balance = required_amount(account, prefix + "_available_cash")
        if balance > account[prefix + "_cash"] or reserved[carrier] > balance:
            raise PlanValidationError("INVALID_AVAILABLE_CASH", "可用现金不足以覆盖已安排调拨")
        available[carrier] = money(balance - reserved[carrier])
    deltas = {k: money(targets[k] - v) for k, v in current.items()}
    remaining = {k: money(targets[k] - v) for k, v in projected.items()}
    gaps = {k: remaining[k] for k in CARRIERS if remaining[k] >= MIN_ADJUSTMENT and k not in blocked}
    reductions = {k: -remaining[k] for k in (*CARRIERS, "changqian", "overseas")
                  if remaining[k] <= -MIN_ADJUSTMENT and k not in blocked}
    allocated = proportional(gaps, available["cash"])
    used = money(sum(allocated.values()))
    pending_returns = money(account["changqian_pending"] + account["overseas_pending"] +
                            sum(t["amount"] for t in account.get("pending_transfers", []) if t["target"] == "cash"))
    # 只补本轮调出后的资金账户目标缺口；已安排回流占用缺口，不为下一轮提前筹款。
    return_limit = money(max(0, targets["cash_pool"] -
                            (current["cash_pool"] - used + pending_returns - reserved["cash"])))
    reductions = {k: v for k, v in proportional(reductions, return_limit).items() if v}
    actions = []
    for key in (*CARRIERS, "changqian", "overseas"):
        carrier = CARRIERS.get(key, key)
        for source, target, amount in (("cash_pool", carrier, allocated.get(key, 0)),
                                       (carrier, "cash_pool", reductions.get(key, 0))):
            if amount:
                immediate = source == "cash_pool"
                actions.append(dict(source=source, target=target, amount=money(amount),
                    reason="account_target_rebalance", immediate=immediate,
                    available_on="same_day" if immediate else "deferred",
                    cash_effect="immediate_cash_in" if immediate else "deferred_cash_return",
                    note="实际到账后纳入账户交易预算" if immediate else "按本轮补池需求分摊回流；可取核验及到账后才计入资金账户预算"))
    reserves, strategy_cash = {}, {}
    for key, carrier in CARRIERS.items():
        incoming, outgoing = allocated.get(key, 0), reductions.get(key, 0)
        # 留款仅从本账户计划预算中扣除，不产生额外小额跨账户转入。
        capacity = max(0, current[key] + incoming - outgoing)
        reserves[carrier] = min(CASH_MIN, capacity)
        strategy_cash[carrier] = money(available[carrier] + incoming - outgoing - reserves[carrier])
    # 平安账户内部按2:1恢复；未成交卖单不支持同批买单。
    pa_value = max(0, current["pingan"] + allocated.get("pingan", 0) - reductions.get("pingan", 0)
                   - account["pingan_cash"] + available["pingan"] - reserves["pingan"])
    pa_targets = {"nasdaq": money(Decimal(str(pa_value)) * Decimal(2) / 3)}
    pa_targets["technology"] = money(pa_value - pa_targets["nasdaq"])
    buy_gaps = {k: max(0, money(pa_targets[k] - account[k + "_total"])) for k in pa_targets}
    sell_budgets = {k: max(0, money(account[k + "_total"] - pa_targets[k])) for k in pa_targets}
    buy_budgets = proportional(buy_gaps, max(0, strategy_cash["pingan"]), minimum=0)
    # 原股票/转债订单用现金净增减控制预算，不把证券市值重复加入。
    buy_budgets.update({k: allocated[k] for k in ("stock", "bond") if k in allocated})
    planned = {k: money(allocated.get(k, 0) - reductions.get(k, 0)) for k in current if k != "cash_pool"}
    planned["cash_pool"] = money(-sum(planned.values()))
    rows = [dict(id=k, label=LABELS[k], current=v, weight=WEIGHTS.get(k, 0), target=targets[k],
                 delta=deltas[k], arranged_delta=money(projected[k]-v), planned_delta=planned[k],
                 remaining_gap=money(remaining[k]-planned[k]), blocked_reason=blocked.get(k)) for k,v in current.items()]
    return dict(policy_id=POLICY_ID, cadence="weekly", minimum_adjustment=MIN_ADJUSTMENT,
        phase="account_rebalance", allocation=dict(total=money(sum(current.values())), current=current,
            targets=targets, deltas=deltas, planned_deltas=planned, rows=rows),
        actions=actions, strategy_cash=strategy_cash, cash_reserves=reserves,
        cash_band=dict(lower=CASH_MIN, upper=None),
        transfer_deltas={"stock": money(strategy_cash["stock"]-account["stock_available_cash"]),
                         "bond": money(strategy_cash["cb"]-account["bond_available_cash"])},
        buy_budgets=buy_budgets, sell_budgets=sell_budgets, account_allocations=allocated,
        account_reductions=reductions, deferred_reductions={}, blocked=blocked,
        pending_transfers=account.get("pending_transfers", []),
        cash=dict(available=available["cash"], immediate_outflow=used, remaining=money(current["cash_pool"]-used),
            expected_returns=money(sum(reductions.values()) + pending_returns),
            pending_returns=pending_returns, pending_outflow=reserved["cash"], return_limit=return_limit,
            terminal_estimate=money(current["cash_pool"] + planned["cash_pool"] + pending_returns - reserved["cash"]),
            note="10%为静态目标；动态按已到账可调拨资金补缺口。预计回流不计本次购买力。"))
