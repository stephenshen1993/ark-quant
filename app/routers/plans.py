from __future__ import annotations
from fastapi import APIRouter, HTTPException
from datasource import db
from app import plan_service
from app.plan_service import (
    PlanServiceError,
    account_input_window_end as _account_input_window_end,
    current_plan_date as _current_plan_date,
    ensure_order_cash_nonnegative as _ensure_order_cash_nonnegative,
    ensure_plan_inputs_consistent as _ensure_plan_inputs_consistent,
    position_snapshot_for_plan as _position_snapshot_for_plan,
    strategy_cash_after_transfer as _strategy_cash_after_transfer,
    summarize_order_cash as _summarize_order_cash,
)

router = APIRouter(prefix="/api/plan", tags=["plan"])


@router.get("/context")
def get_plan_context(refresh_temperature: bool = False):
    return _service_response(plan_service.build_plan_context, refresh_temperature)


@router.get("/transfer")
def get_transfer_plan(refresh_temperature: bool = False):
    return _service_response(plan_service.build_transfer_plan, refresh_temperature)


@router.get("")
def get_plan(refresh_temperature: bool = False):
    return _service_response(plan_service.build_current_plan, refresh_temperature)


@router.post("/generate")
def generate_plan():
    """Generate a full trading plan in one server-side workflow."""
    try:
        return plan_service.generate_complete_plan(
            size_cb_orders=_size_cb_orders,
            size_stock_orders=_size_stock_orders,
        )
    except PlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _service_response(builder, *args, **kwargs):
    try:
        return builder(*args, **kwargs)
    except PlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/{strategy}/size-orders")
def size_orders(strategy: str):
    """根据计划日期榜单 + 持仓 + 服务端账户事实，生成具体买卖张数/股数。"""
    try:
        if strategy == "cb":
            plan_date, account, deltas = _ensure_plan_inputs_consistent()
            return _size_cb_orders(
                _strategy_cash_after_transfer("cb", account, deltas),
                plan_date=plan_date,
            )
        if strategy == "stock":
            plan_date, account, deltas = _ensure_plan_inputs_consistent()
            return _size_stock_orders(
                _strategy_cash_after_transfer("stock", account, deltas),
                plan_date=plan_date,
            )
    except PlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    raise HTTPException(400, {"code": "INVALID_STRATEGY", "message": f"未知策略: {strategy}"})


def _size_cb_orders(cash: float, *, plan_date: str | None = None) -> dict:
    import pandas as pd
    from datasource.market import fetch_cb_prices_tencent
    from strategies.cb_rotation.size_orders import size_rebalance

    plan_date = plan_date or _current_plan_date()
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
    _ensure_order_cash_nonnegative("cb", summary)

    db.init_db()
    meta = db.get_strategy_run_meta("cb", plan_date)
    run_id = meta["id"] if meta else None
    if run_id is not None:
        db.insert_cb_orders(run_id, sheet)

    account = db.get_current_account_summary()
    acct_cash = account["bond_available_cash"] if account else 0
    orders = sheet.to_dict("records")

    return {
        "orders": orders,
        "summary": _summarize_order_cash(acct_cash, cash - acct_cash, orders),
    }


def _size_stock_orders(cash: float, *, plan_date: str | None = None) -> dict:
    import pandas as pd
    from datasource.market import fetch_tencent_snapshot
    from strategies.stock_smallcap.target_sizing import SizingError, size_target_state

    plan_date = plan_date or _current_plan_date()
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
        sheet, summary = size_target_state(pd.DataFrame(rankings), positions, cash, prices)
    except SizingError as exc:
        raise HTTPException(409, {
            "code": exc.code,
            "message": exc.message,
            **exc.details,
        }) from exc
    except SystemExit as exc:
        raise HTTPException(400, {
            "code": "ORDER_SIZING_FAILED",
            "message": str(exc),
        }) from exc
    _ensure_order_cash_nonnegative("stock", summary)

    db.init_db()
    meta = db.get_strategy_run_meta("stock", plan_date)
    run_id = meta["id"] if meta else None
    if run_id is not None:
        db.insert_stock_orders(run_id, sheet)

    account = db.get_current_account_summary()
    acct_cash = account["stock_available_cash"] if account else 0
    orders = sheet.to_dict("records")

    return {
        "orders": orders,
        "summary": _summarize_order_cash(acct_cash, cash - acct_cash, orders),
    }
