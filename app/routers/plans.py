from __future__ import annotations
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException
from datasource import db
from datasource.youzhiyouxing import TemperatureFetchError, get_or_fetch_market_temperature
from portfolio_rebalance import PlanValidationError, build_fund_transfer_plan

router = APIRouter(prefix="/api/plan", tags=["plan"])


@router.get("/context")
def get_plan_context(refresh_temperature: bool = False):
    try:
        market_temperature = get_or_fetch_market_temperature(refresh=refresh_temperature)
    except TemperatureFetchError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MARKET_TEMPERATURE_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc
    return {
        "plan_date": market_temperature.updated_at[:10],
        "market_temperature": market_temperature.to_dict(),
    }


@router.get("/transfer")
def get_transfer_plan(refresh_temperature: bool = False):
    try:
        market_temperature = get_or_fetch_market_temperature(refresh=refresh_temperature)
    except TemperatureFetchError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MARKET_TEMPERATURE_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc

    plan_date = market_temperature.updated_at[:10]
    account = db.get_current_account_summary()
    if account:
        account = dict(account)
        account["temperature"] = market_temperature.temperature

    account_errors = _validate_account_inputs(
        plan_date,
        account,
        _account_transfer_window_end(plan_date),
        include_overseas=True,
    )
    if account_errors:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TRANSFER_INPUT_DATE_MISMATCH",
                "message": "资金调拨输入日期不一致，请先更新账户快照。",
                "plan_date": plan_date,
                "market_temperature": market_temperature.to_dict(),
                "errors": account_errors,
            },
        )

    try:
        fund_transfer = _build_fund_transfer(account)
    except PlanValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    targets, deltas, transfer_steps = _fund_transfer_compatibility(fund_transfer)

    return {
        "generated_at": datetime.now().isoformat(),
        "plan_date": plan_date,
        "market_temperature": market_temperature.to_dict(),
        "account": account,
        "targets": targets,
        "transfer_steps": transfer_steps,
        "transfer_deltas": deltas,
        "fund_transfer": fund_transfer,
        "warnings": _plan_input_warnings(plan_date, account),
    }


@router.get("")
def get_plan(refresh_temperature: bool = False):
    try:
        market_temperature = get_or_fetch_market_temperature(refresh=refresh_temperature)
    except TemperatureFetchError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MARKET_TEMPERATURE_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc

    plan_date = market_temperature.updated_at[:10]

    account = db.get_current_account_summary()
    if account:
        account = dict(account)
        account["temperature"] = market_temperature.temperature

    account_errors = _validate_account_inputs(
        plan_date,
        account,
        _account_transfer_window_end(plan_date),
        include_overseas=True,
    )
    if account_errors:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PLAN_INPUT_DATE_MISMATCH",
                "message": "账户输入日期不一致，请按下方逐项更新到计划输入窗口内。",
                "plan_date": plan_date,
                "market_temperature": market_temperature.to_dict(),
                "errors": account_errors,
            },
        )
    warnings = _plan_input_warnings(plan_date, account)
    trade_errors = _validate_strategy_inputs(plan_date)

    cb_orders = db.get_orders("cb", plan_date)
    cb_data_date = plan_date
    cb_trade_date = _trade_date_for("cb", cb_data_date)
    cb_universe = db.get_rankings("cb", plan_date)
    cb_rankings = cb_universe if not cb_orders else []

    stock_orders = db.get_orders("stock", plan_date)
    stock_data_date = plan_date
    stock_trade_date = _trade_date_for("stock", stock_data_date)
    stock_rankings = db.get_rankings("stock", plan_date) if not stock_orders else []

    try:
        fund_transfer = _build_fund_transfer(
            account,
            qualified_cb_count=len(cb_universe) if cb_universe else None,
            include_a_internal=True,
        )
    except PlanValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    targets, deltas, transfer_steps = _fund_transfer_compatibility(fund_transfer)

    def _order_summary(orders, cash_key: str) -> dict | None:
        if not orders or not account:
            return None
        delta = deltas.get(cash_key, 0)
        base = account.get(f"{cash_key}_available_cash", account.get(f"{cash_key}_cash", 0))
        def shares(order: dict) -> int:
            return order.get("delta_shares", order.get("shares", 0)) or 0
        sells = sum(o.get("amount", 0) for o in orders if shares(o) < 0)
        buys  = sum(o.get("amount", 0) for o in orders if shares(o) > 0)
        book = round(base + sells - buys, 2)
        return {
            "book_balance": book,
            "transfer_delta": round(delta, 2),
            "cash_left": round(book + delta, 2),
        }

    return {
        "generated_at": datetime.now().isoformat(),
        "plan_date": plan_date,
        "market_temperature": market_temperature.to_dict(),
        "account": account,
        "warnings": warnings,
        "trade_errors": trade_errors,
        "targets": targets,
        "transfer_steps": transfer_steps,
        "transfer_deltas": deltas,
        "fund_transfer": fund_transfer,
        "execution_sequence": _execution_sequence(cb_orders, stock_orders, transfer_steps),
        "cb": {
            "data_date": cb_data_date,
            "trade_date": cb_trade_date,
            "orders": cb_orders,
            "rankings": cb_rankings,
            "summary": _order_summary(cb_orders, "bond"),
        },
        "stock": {
            "data_date": stock_data_date,
            "trade_date": stock_trade_date,
            "orders": stock_orders,
            "rankings": stock_rankings,
            "summary": _order_summary(stock_orders, "stock"),
        },
    }


def _trade_date_for(strategy: str, data_date: str | None) -> str | None:
    if not data_date:
        return None
    meta = db.get_strategy_run_meta(strategy, data_date)
    return meta.get("trade_date") if meta else None


def _validate_plan_inputs(plan_date: str, account: dict | None) -> list[dict]:
    return (
        _validate_account_inputs(
            plan_date,
            account,
            _account_input_window_end(plan_date),
            include_overseas=True,
        )
        + _validate_strategy_inputs(plan_date)
    )


def _validate_account_inputs(
    plan_date: str,
    account: dict | None,
    account_input_end: str,
    *,
    include_overseas: bool = False,
) -> list[dict]:
    errors = []
    if not account:
        errors.append({"input": "account", "date": None, "expected": plan_date, "message": "缺少账户快照"})
    else:
        account_dates = account.get("account_snapshot_dates") or {}
        required_accounts = {
            "stock": "股票账户",
            "cb": "转债账户",
            "changqian": "长钱账户",
            "cash": "现金账户",
        }
        if include_overseas:
            required_accounts["overseas"] = "海外长钱账户"
        for account_id, label in required_accounts.items():
            actual = account_dates.get(account_id)
            if not _is_fact_date_acceptable(actual, plan_date, account_input_end):
                errors.append({
                    "input": f"account.{account_id}",
                    "date": actual,
                    "expected": f"{plan_date} 至 {account_input_end}",
                    "message": f"{label}事实日期不在计划输入窗口内",
                })

    return errors


def _build_fund_transfer(
    account: dict | None,
    *,
    qualified_cb_count: int | None = None,
    include_a_internal: bool = False,
) -> dict:
    if not account:
        raise PlanValidationError("MISSING_ACCOUNT", "缺少账户快照")
    context = {
        "temperature": account["temperature"],
        "check_type": account.get("check_type", "a_internal"),
        "new_contribution": account.get("new_contribution", 0),
        "b_purchase_limit": account.get("b_purchase_limit", 0),
        "cash_available": account.get("cash_pool", 0),
    }
    return build_fund_transfer_plan(
        account,
        context,
        qualified_cb_count,
        include_a_internal=include_a_internal,
    )


def _fund_transfer_compatibility(fund_transfer: dict) -> tuple[dict, dict, list[str]]:
    top = fund_transfer["top_level"]
    internal = fund_transfer["a_internal"]
    targets = {"A": top["targets"]["A"], "B": top["targets"]["B"], "C": top["targets"]["C"]}
    internal_deltas = internal.get("planned_deltas") or {"stock": 0.0, "bond": 0.0, "cash_pool": 0.0}
    deltas = {**top["executed_deltas"], **internal_deltas}
    if internal["status"] in {"ready", "within_threshold"}:
        targets.update(internal["final_targets"])
    actions = top["executed_actions"] + top["outflows"] + internal.get("actions", [])
    labels = {"stock": "股票账户", "bond": "转债账户", "cash_pool": "现金池"}
    steps = [
        f"{labels.get(action['source'], action['source'])} → {labels.get(action['target'], action['target'])}：{action['amount']:.2f}（{action['reason']}）"
        for action in actions
    ]
    return targets, deltas, steps


def _validate_strategy_inputs(plan_date: str) -> list[dict]:
    errors = []
    for strategy, label in (("cb", "转债榜单"), ("stock", "股票榜单")):
        dates = db.get_ranking_dates(strategy)
        has_rankings = plan_date in dates
        has_orders = bool(db.get_orders(strategy, plan_date))
        if not has_rankings and not has_orders:
            errors.append({
                "input": strategy,
                "date": dates[0] if dates else None,
                "expected": plan_date,
                "message": f"缺少 {plan_date} 的{label}",
            })
    return errors


def _plan_input_warnings(plan_date: str, account: dict | None) -> list[dict]:
    warnings = []
    if not account:
        return warnings
    account_input_end = _account_input_window_end(plan_date)
    account_dates = account.get("account_snapshot_dates") or {}
    overseas_snapshot_date = account_dates.get("overseas")
    if not _is_fact_date_acceptable(overseas_snapshot_date, plan_date, account_input_end):
        warnings.append({
            "input": "account.overseas",
            "date": overseas_snapshot_date,
            "expected": f"{plan_date} 至 {account_input_end}",
            "message": "海外长钱事实日期不在计划输入窗口内；它不参与国内再平衡，本次仅提示。",
        })
    return warnings


def _account_input_window_end(plan_date: str) -> str:
    """Accounts may be updated after close, over the weekend, or pre-open.

    Strategy rankings are still tied exactly to ``plan_date``. Account values are
    factual manual inputs, so a user may record them any time between the close
    date and the opening date this plan applies to.
    """
    trade_dates = []
    for strategy in ("cb", "stock"):
        meta = db.get_strategy_run_meta(strategy, plan_date)
        if meta and meta.get("trade_date"):
            trade_dates.append(meta["trade_date"])
    if trade_dates:
        return max(trade_dates)
    return plan_date


def _account_transfer_window_end(plan_date: str) -> str:
    """Transfer planning can use current factual account values before rankings exist."""
    input_end = _account_input_window_end(plan_date)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    return max(input_end, today)


def _is_fact_date_acceptable(fact_date: str | None, plan_date: str, window_end: str) -> bool:
    if fact_date is None:
        return False
    try:
        actual_date = date.fromisoformat(fact_date)
        start = date.fromisoformat(plan_date)
        end = date.fromisoformat(window_end)
    except ValueError:
        return False
    return start <= actual_date <= end


@router.post("/{strategy}/size-orders")
def size_orders(strategy: str):
    """根据计划日期榜单 + 持仓 + 服务端账户事实，生成具体买卖张数/股数。"""
    if strategy == "cb":
        plan_date, account, deltas = _ensure_plan_inputs_consistent()
        return _size_cb_orders(_strategy_cash_after_transfer("cb", account, deltas))
    if strategy == "stock":
        plan_date, account, deltas = _ensure_plan_inputs_consistent()
        return _size_stock_orders(_strategy_cash_after_transfer("stock", account, deltas))
    raise HTTPException(400, {"code": "INVALID_STRATEGY", "message": f"未知策略: {strategy}"})


def _size_cb_orders(cash: float) -> dict:
    import pandas as pd
    from datasource.market import fetch_cb_prices_tencent
    from strategies.cb_rotation.size_orders import size_rebalance

    plan_date = _current_plan_date()
    rankings = db.get_rankings("cb", plan_date)
    if not rankings:
        raise HTTPException(400, {"code": "NO_RANKINGS", "message": f"没有 {plan_date} 的转债榜单，请先运行策略。"})

    input_end = _account_input_window_end(plan_date)
    position_snapshot = _position_snapshot_for_plan("cb", plan_date, input_end)
    if position_snapshot is None:
        raise HTTPException(400, {"code": "NO_POSITIONS", "message": f"没有 {plan_date} 至 {input_end} 输入窗口内的转债持仓，请先在账户页保存。"})
    positions_rows = position_snapshot["items"]
    target = pd.DataFrame(rankings)[["bond_code", "bond_name"]]
    target["bond_code"] = target["bond_code"].astype(str).str.zfill(6)

    if positions_rows:
        positions = pd.DataFrame([
            {"bond_code": r["code"], "bond_name": r.get("name", ""), "shares": r["shares"]}
            for r in positions_rows
        ])
        positions["bond_code"] = positions["bond_code"].astype(str).str.zfill(6)
    else:
        positions = pd.DataFrame(columns=["bond_code", "bond_name", "shares"])

    codes = list(dict.fromkeys(list(target["bond_code"]) + list(positions["bond_code"])))
    prices = fetch_cb_prices_tencent(codes)
    if not prices:
        raise HTTPException(500, {"code": "DATA_SOURCE_UNAVAILABLE", "message": "无法获取转债实时价格。"})
    missing_prices = [c for c in codes if c not in prices]
    if missing_prices:
        raise HTTPException(500, {
            "code": "DATA_SOURCE_UNAVAILABLE",
            "message": f"缺少转债报价: {missing_prices[:5]}...",
        })

    try:
        sheet, summary = size_rebalance(target, positions, cash, prices)
    except SystemExit as exc:
        raise HTTPException(400, {
            "code": "ORDER_SIZING_FAILED",
            "message": str(exc),
        }) from exc
    _ensure_sizing_cash_nonnegative("cb", summary)

    db.init_db()
    meta = db.get_strategy_run_meta("cb", plan_date)
    run_id = meta["id"] if meta else None
    if run_id is not None:
        db.insert_cb_orders(run_id, sheet)

    account = db.get_current_account_summary()
    acct_cash = account["bond_available_cash"] if account else 0
    sells = sum(r["amount"] for r in sheet.to_dict("records") if r.get("delta_shares", 0) < 0)
    buys  = sum(r["amount"] for r in sheet.to_dict("records") if r.get("delta_shares", 0) > 0)
    book_balance = round(acct_cash + sells - buys, 2)

    return {
        "orders": sheet.to_dict("records"),
        "summary": {
            "book_balance": book_balance,
            "transfer_delta": round(cash - acct_cash, 2),
            "cash_left": round(book_balance + (cash - acct_cash), 2),
        },
    }


def _size_stock_orders(cash: float) -> dict:
    import pandas as pd
    from datasource.market import fetch_tencent_snapshot
    from strategies.stock_smallcap.size_orders import size_rebalance

    plan_date = _current_plan_date()
    rankings = db.get_rankings("stock", plan_date)
    if not rankings:
        raise HTTPException(400, {"code": "NO_RANKINGS", "message": f"没有 {plan_date} 的股票榜单，请先运行策略。"})

    input_end = _account_input_window_end(plan_date)
    position_snapshot = _position_snapshot_for_plan("stock", plan_date, input_end)
    if position_snapshot is None:
        raise HTTPException(400, {"code": "NO_POSITIONS", "message": f"没有 {plan_date} 至 {input_end} 输入窗口内的股票持仓，请先在账户页保存。"})
    positions_rows = position_snapshot["items"]
    rank_codes = {r["stock_code"] for r in rankings}
    if positions_rows:
        pos_codes = {p["code"] for p in positions_rows}
        pos_map = {p["code"]: p for p in positions_rows}
    else:
        pos_codes = set()
        pos_map = {}

    # Derive rebalance actions from rankings vs current positions
    rebalance_rows = []
    for r in rankings:
        action = "HOLD" if r["stock_code"] in pos_codes else "BUY"
        rebalance_rows.append({
            "action": action, "stock_code": r["stock_code"],
            "stock_name": r.get("stock_name", ""), "rank": r.get("rank", 0),
        })
    for code in pos_codes - rank_codes:
        p = pos_map[code]
        rebalance_rows.append({
            "action": "SELL", "stock_code": code,
            "stock_name": p.get("name", ""), "rank": 999,
        })
    reb = pd.DataFrame(rebalance_rows)

    if positions_rows:
        positions = pd.DataFrame([
            {"stock_code": p["code"], "stock_name": p.get("name", ""), "shares": p["shares"]}
            for p in positions_rows
        ])
    else:
        positions = pd.DataFrame(columns=["stock_code", "stock_name", "shares"])
    positions["stock_code"] = positions["stock_code"].astype(str).str.zfill(6)

    all_codes = list(dict.fromkeys(list(reb["stock_code"]) + list(positions["stock_code"])))
    snap = fetch_tencent_snapshot(all_codes)
    prices = dict(zip(snap["stock_code"], snap["price"]))
    missing_prices = [c for c in all_codes if c not in prices]
    if missing_prices:
        raise HTTPException(500, {
            "code": "DATA_SOURCE_UNAVAILABLE",
            "message": f"缺少报价: {missing_prices[:5]}...",
        })

    try:
        sheet, summary = size_rebalance(reb, positions, cash, prices, min_trade_value=1000)
    except SystemExit as exc:
        raise HTTPException(400, {
            "code": "ORDER_SIZING_FAILED",
            "message": str(exc),
        }) from exc
    _ensure_sizing_cash_nonnegative("stock", summary)

    db.init_db()
    meta = db.get_strategy_run_meta("stock", plan_date)
    run_id = meta["id"] if meta else None
    if run_id is not None:
        db.insert_stock_orders(run_id, sheet)

    account = db.get_current_account_summary()
    acct_cash = account["stock_available_cash"] if account else 0
    sells = sum(r["amount"] for r in sheet.to_dict("records") if r.get("delta_shares", 0) < 0)
    buys  = sum(r["amount"] for r in sheet.to_dict("records") if r.get("delta_shares", 0) > 0)
    book_balance = round(acct_cash + sells - buys, 2)

    return {
        "orders": sheet.to_dict("records"),
        "summary": {
            "book_balance": book_balance,
            "transfer_delta": round(cash - acct_cash, 2),
            "cash_left": round(book_balance + (cash - acct_cash), 2),
        },
    }


def _current_plan_date() -> str:
    try:
        market_temperature = get_or_fetch_market_temperature()
    except TemperatureFetchError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MARKET_TEMPERATURE_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc
    return market_temperature.updated_at[:10]


def _strategy_cash_after_transfer(strategy: str, account: dict, deltas: dict) -> float:
    if strategy == "cb":
        return round((account.get("bond_available_cash") or 0) + (deltas.get("bond") or 0), 2)
    if strategy == "stock":
        return round((account.get("stock_available_cash") or 0) + (deltas.get("stock") or 0), 2)
    raise ValueError(f"Unknown strategy: {strategy}")


def _shares(order: dict) -> int:
    return order.get("delta_shares", order.get("shares", 0)) or 0


def _ensure_sizing_cash_nonnegative(strategy: str, summary: dict) -> None:
    cash_left = round(summary.get("cash_left", 0) or 0, 2)
    if cash_left < -0.01:
        label = "转债账户" if strategy == "cb" else "股票账户"
        raise HTTPException(409, {
            "code": "INSUFFICIENT_RELEASABLE_CASH",
            "message": f"{label}可释放资金不足，无法满足本次调拨后的订单现金约束。",
            "cash_left": cash_left,
            "cash_in": round(summary.get("cash_in", 0) or 0, 2),
            "holdings_value": round(summary.get("holdings_value", 0) or 0, 2),
        })


def _execution_sequence(cb_orders: list[dict], stock_orders: list[dict], transfer_steps: list[str]) -> list[dict]:
    sell_actions = {"SELL", "TRIM"}
    buy_actions = {"BUY", "ADD"}

    def _phase_orders(strategy: str, orders: list[dict], actions: set[str]) -> list[dict]:
        return [
            {**order, "strategy": strategy}
            for order in orders
            if order.get("action") in actions and _shares(order) != 0
        ]

    return [
        {
            "phase": "sell",
            "label": "先卖出或减仓释放资金",
            "orders": _phase_orders("cb", cb_orders, sell_actions)
            + _phase_orders("stock", stock_orders, sell_actions),
        },
        {
            "phase": "transfer",
            "label": "再通过现金池调拨",
            "steps": transfer_steps,
        },
        {
            "phase": "buy",
            "label": "最后买入或加仓",
            "orders": _phase_orders("cb", cb_orders, buy_actions)
            + _phase_orders("stock", stock_orders, buy_actions),
        },
    ]


def _position_snapshot_for_plan(strategy: str, plan_date: str, input_end: str) -> dict | None:
    return db.get_position_snapshot_between(strategy, plan_date, input_end)


def _positions_for_plan(strategy: str, plan_date: str, input_end: str) -> list[dict]:
    snapshot = _position_snapshot_for_plan(strategy, plan_date, input_end)
    if snapshot is None:
        return []
    return snapshot["items"]


def _ensure_plan_inputs_consistent() -> tuple[str, dict, dict]:
    plan_date = _current_plan_date()
    account = db.get_current_account_summary()
    errors = _validate_plan_inputs(plan_date, account)
    if errors:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PLAN_INPUT_DATE_MISMATCH",
                "message": "交易计划输入日期不一致，请按下方逐项更新到同一个计划日。",
                "plan_date": plan_date,
                "errors": errors,
            },
        )
    account = dict(account)
    market_temperature = get_or_fetch_market_temperature()
    account["temperature"] = market_temperature.temperature
    cb_rankings = db.get_rankings("cb", plan_date)
    fund_transfer = _build_fund_transfer(
        account,
        qualified_cb_count=len(cb_rankings) if cb_rankings else None,
        include_a_internal=True,
    )
    _, deltas, _ = _fund_transfer_compatibility(fund_transfer)
    return plan_date, account, deltas
