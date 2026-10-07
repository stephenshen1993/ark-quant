"""已确认阶段配置：目标 → 调拨预算。纯计算，不读账户、不执行交易。"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

POLICY_ID = "growth-2026-10-08-preserve-strategy-cash"
CASH_MIN = 300.0
CASH_MAX = 1000.0
MIN_ADJUSTMENT = 1000.0
WEIGHTS = {"stock": .35, "nasdaq": .20, "technology": .10, "bond": .25, "cash_pool": .10}
LABELS = {
    "stock": "小市值", "nasdaq": "纳指方向", "technology": "501312",
    "bond": "主动转债", "cash_pool": "直接现金", "changqian": "国内长钱（待迁移）",
    "overseas": "海外长钱（待迁移）", "pending": "赎回在途", "unclassified": "待归类持仓",
}
CARRIERS = {"stock": "stock", "bond": "cb", "nasdaq": "pingan", "technology": "pingan"}


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
    """券商现金从策略市值扣出；投顾完整余额与在途单次计量。"""
    from portfolio_rebalance import PlanValidationError

    current = {}
    cash = required_amount(account, "cash_pool")
    for key, prefix in (("stock", "stock"), ("bond", "bond")):
        total = required_amount(account, f"{prefix}_total")
        balance = required_amount(account, f"{prefix}_cash")
        if balance > total:
            raise PlanValidationError("INVALID_CASH", f"{key}现金超过账户总额")
        current[key] = money(total - balance)
        cash += balance
    cash += required_amount(account, "pingan_cash")
    for key in ("nasdaq", "technology", "unclassified"):
        current[key] = required_amount(account, f"{key}_total")
    pa_total = required_amount(account, "pingan_total")
    if abs(pa_total - sum(current[k] for k in ("nasdaq", "technology", "unclassified"))
           - account["pingan_cash"]) > .02:
        raise PlanValidationError("INVALID_PINGAN_TOTAL", "平安持仓与现金合计不等于账户估值")
    current["cash_pool"] = money(cash)
    for key in ("changqian", "overseas"):
        total = required_amount(account, f"{key}_total")
        pending = required_amount(account, f"{key}_pending")
        if pending > total:
            raise PlanValidationError("INVALID_PENDING", "在途金额不能超过所属账户总额")
        current[key] = money(total - pending)
    current["pending"] = money(account["changqian_pending"] + account["overseas_pending"])
    return current


def targets_for(current: dict) -> dict:
    total = money(sum(current.values()))
    targets = {key: money(Decimal(str(total)) * Decimal(str(weight))) for key, weight in WEIGHTS.items()}
    targets["cash_pool"] = money(total - sum(value for key, value in targets.items() if key != "cash_pool"))
    targets.update({key: 0.0 for key in current if key not in targets})
    return targets


def proportional(gaps: dict, budget: float) -> dict:
    """向下到分，不超预算；分配后小额留池，不递归重分配。"""
    total = sum(gaps.values())
    scale = min(1.0, budget / total) if total else 0
    return {
        key: value if value >= MIN_ADJUSTMENT else 0.0
        for key, gap in gaps.items()
        for value in [float((Decimal(str(gap)) * Decimal(str(scale))).quantize(Decimal(".01"), rounding=ROUND_DOWN))]
    }


def build_stage_plan(account: dict, *, blocked: dict | None = None) -> dict:
    """资金未到账不支持买入；不同券商可用资金不等同于已转入资金池。"""
    from portfolio_rebalance import PlanValidationError

    current = allocation_amounts(account)
    targets = targets_for(current)
    deltas = {key: money(targets[key] - value) for key, value in current.items()}
    blocked = dict(blocked or {})
    available = {"cash": required_amount(account, "cash_pool")}
    for carrier, prefix in (("stock", "stock"), ("cb", "bond"), ("pingan", "pingan")):
        available[carrier] = required_amount(account, f"{prefix}_available_cash")
        if available[carrier] > account[f"{prefix}_cash"]:
            raise PlanValidationError("INVALID_AVAILABLE_CASH", "可用现金超过现金余额")

    deferred_reductions = {key: -delta for key, delta in deltas.items()
             if key in (*CARRIERS, "changqian", "overseas") and delta <= -MIN_ADJUSTMENT and key not in blocked}
    sells = {}  # 当前迁移阶段：目标减配只列后续缺口，不生成本期释放任务。
    gaps = {key: delta for key, delta in deltas.items()
            if key in CARRIERS and delta >= MIN_ADJUSTMENT and key not in blocked}
    # 本期只分配现有现金；后续目标回流不计即时或终态购买力。
    reserves = {}
    reserve_topups = {}
    for carrier in ("stock", "cb", "pingan"):
        keys = [key for key, c in CARRIERS.items() if c == carrier]
        active = any(key not in blocked and (current[key] > 0 or (key in gaps and available["cash"] + available[carrier] >= CASH_MIN)) for key in keys)
        reserve = CASH_MIN if active else min(CASH_MIN, available[carrier])
        release = sum(sells.get(key, 0) for key in keys)
        reserves[carrier] = reserve
        reserve_topups[carrier] = money(max(0, reserve - available[carrier] - release))
    if sum(reserve_topups.values()) > available["cash"]:
        raise PlanValidationError("CASH_RESERVE_SHORTFALL", "已有现金不足以预留每账户300元；到账后更新事实再生成")
    trading_available = {c: max(0, available[c] - reserves[c]) for c in reserves}
    bank_available = money(available["cash"] - sum(reserve_topups.values()))
    spendable = bank_available + sum(trading_available.values())
    allocated = proportional(gaps, spendable)
    carrier_need = {carrier: sum(allocated.get(key, 0) for key, c in CARRIERS.items() if c == carrier)
                    for carrier in ("stock", "cb", "pingan")}
    bank_need = {carrier: max(0.0, need - trading_available[carrier]) for carrier, need in carrier_need.items()}
    bank_total = sum(bank_need.values())
    bank_scale = min(1.0, bank_available / bank_total) if bank_total else 1.0
    for carrier, need in carrier_need.items():
        cap = trading_available[carrier] + bank_need[carrier] * bank_scale
        if need > cap and need:
            reduced = proportional({key: allocated[key] for key, c in CARRIERS.items() if c == carrier and key in allocated}, cap)
            allocated.update(reduced)
    planned = {key: -sells.get(key, 0) + allocated.get(key, 0) for key in current if key != "cash_pool"}
    planned["cash_pool"] = money(-sum(planned.values()))
    actions = []
    strategy_cash = {}
    for carrier in ("stock", "cb", "pingan"):
        keys = [key for key, c in CARRIERS.items() if c == carrier]
        buys = sum(allocated.get(key, 0) for key in keys)
        release = sum(sells.get(key, 0) for key in keys)
        # 平安按新增配置预算买入；原策略保留本账户现金，预留仅约束订单预算。
        strategy_cash[carrier] = money(buys - release)
        incoming = money(max(0, buys - trading_available[carrier]) + reserve_topups[carrier])
        outgoing = money(max(0, available[carrier] + incoming + release - buys - reserves[carrier]))
        if not buys and not release:
            outgoing = money(max(0, available[carrier] - CASH_MAX))
        if carrier in {"stock", "cb"}:
            outgoing = 0.0
            strategy_cash[carrier] = money(max(0, available[carrier] + incoming - reserves[carrier]))
        for source, target, amount, immediate in (
            ("cash_pool", carrier, incoming, True),
            (carrier, "cash_pool", outgoing, False),
        ):
            if amount <= 0 or (not immediate and not release and available[carrier] - buys <= CASH_MAX):
                continue
            actions.append({
                "source": source, "target": target,
                "amount": amount, "reason": "stage_target_rebalance",
                "immediate": immediate,
                "available_on": "same_day" if immediate else "deferred",
                "cash_effect": "immediate_cash_in" if immediate else "deferred_cash_return",
                "note": "到账后才可买入" if immediate else "卖出与可取核验后回池；不计当期购买力",
            })
    for key in ("changqian", "overseas"):
        if sells.get(key):
            actions.append({"source": key, "target": "cash_pool", "amount": sells[key],
                            "reason": "stage_target_rebalance", "immediate": False,
                            "available_on": "deferred", "cash_effect": "deferred_cash_return",
                            "note": "平台确认赎回并到账后更新账户；在途不可买入"})
    used_bank = money(sum(a["amount"] for a in actions if a["source"] == "cash_pool"))
    if used_bank > available["cash"] + .01:
        raise PlanValidationError("CASH_OVERCOMMITTED", "资金池被重复占用")
    rows = [{"id": key, "label": LABELS[key], "current": value,
             "weight": WEIGHTS.get(key, 0), "target": targets[key], "delta": deltas[key],
             "planned_delta": money(planned.get(key, 0)),
             "remaining_gap": money(deltas[key] - planned.get(key, 0)),
             "blocked_reason": blocked.get(key)} for key, value in current.items()]
    return {"policy_id": POLICY_ID, "cadence": "weekly", "minimum_adjustment": MIN_ADJUSTMENT,
            "allocation": {"total": money(sum(current.values())), "current": current, "targets": targets,
                           "deltas": deltas, "planned_deltas": planned, "rows": rows},
            "actions": actions, "strategy_cash": strategy_cash,
            "cash_reserves": reserves, "cash_band": {"lower": CASH_MIN, "upper": CASH_MAX},
            "transfer_deltas": {"stock": money(strategy_cash["stock"] - available["stock"]),
                                "bond": money(strategy_cash["cb"] - available["cb"])},
            "buy_budgets": allocated,
            "sell_budgets": sells, "deferred_reductions": deferred_reductions, "blocked": blocked,
            "phase": "existing_cash_first",
            "cash": {"available": available["cash"], "immediate_outflow": used_bank,
                     "remaining": money(available["cash"] - used_bank),
                     "terminal_estimate": money(current["cash_pool"] + planned["cash_pool"]),
                     "note": "当前先用已有现金；高配减持列后续目标，基金订单生成后再按新增投入列条件赎回。现金目标允许迁移中暂时偏离。"}}
