from __future__ import annotations

import pandas as pd

from app import plan_service
from app.plan_service import PlanServiceError
from datasource import db


def size_strategy_orders(strategy: str) -> dict:
    if strategy not in {"cb", "stock"}:
        raise PlanServiceError(
            400,
            {"code": "INVALID_STRATEGY", "message": f"未知策略: {strategy}"},
        )

    plan_date, account, deltas = plan_service.ensure_plan_inputs_consistent()
    if strategy == "cb":
        return size_cb_orders(
            plan_service.strategy_cash_after_transfer("cb", account, deltas),
            plan_date=plan_date,
        )
    return size_stock_orders(
        plan_service.strategy_cash_after_transfer("stock", account, deltas),
        plan_date=plan_date,
    )


def size_cb_orders(cash: float, *, plan_date: str | None = None) -> dict:
    from datasource.market import fetch_cb_prices_tencent
    from strategies.cb_rotation.size_orders import size_rebalance

    plan_date = plan_date or plan_service.current_plan_date()
    rankings = db.get_rankings("cb", plan_date)
    if not rankings:
        raise PlanServiceError(
            400,
            {"code": "NO_RANKINGS", "message": f"没有 {plan_date} 的转债榜单，请先运行策略。"},
        )

    input_end = plan_service.account_input_window_end(plan_date)
    position_snapshot = plan_service.position_snapshot_for_plan("cb", plan_date, input_end)
    if position_snapshot is None:
        raise PlanServiceError(
            400,
            {
                "code": "NO_POSITIONS",
                "message": f"没有 {plan_date} 至 {input_end} 输入窗口内的转债持仓，请先在账户页保存。",
            },
        )
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
        raise PlanServiceError(
            500,
            {"code": "DATA_SOURCE_UNAVAILABLE", "message": "无法获取转债实时价格。"},
        )
    missing_prices = [code for code in codes if code not in prices]
    if missing_prices:
        raise PlanServiceError(
            500,
            {
                "code": "DATA_SOURCE_UNAVAILABLE",
                "message": f"缺少转债报价: {missing_prices[:5]}...",
            },
        )

    try:
        sheet, summary = size_rebalance(target, positions, cash, prices)
    except SystemExit as exc:
        raise PlanServiceError(
            400,
            {
                "code": "ORDER_SIZING_FAILED",
                "message": str(exc),
            },
        ) from exc
    plan_service.ensure_order_cash_nonnegative("cb", summary)
    _persist_strategy_orders("cb", plan_date, sheet)

    account = db.get_current_account_summary()
    account_cash = account["bond_available_cash"] if account else 0
    orders = sheet.to_dict("records")
    return {
        "orders": orders,
        "summary": plan_service.summarize_order_cash(account_cash, cash - account_cash, orders),
    }


def size_stock_orders(cash: float, *, plan_date: str | None = None) -> dict:
    from datasource.market import fetch_tencent_snapshot
    from strategies.stock_smallcap.target_sizing import SizingError, size_target_state

    plan_date = plan_date or plan_service.current_plan_date()
    rankings = db.get_rankings("stock", plan_date)
    if not rankings:
        raise PlanServiceError(
            400,
            {"code": "NO_RANKINGS", "message": f"没有 {plan_date} 的股票榜单，请先运行策略。"},
        )

    input_end = plan_service.account_input_window_end(plan_date)
    position_snapshot = plan_service.position_snapshot_for_plan("stock", plan_date, input_end)
    if position_snapshot is None:
        raise PlanServiceError(
            400,
            {
                "code": "NO_POSITIONS",
                "message": f"没有 {plan_date} 至 {input_end} 输入窗口内的股票持仓，请先在账户页保存。",
            },
        )
    positions_rows = position_snapshot["items"]
    rank_codes = {row["stock_code"] for row in rankings}
    if positions_rows:
        position_codes = {position["code"] for position in positions_rows}
        position_map = {position["code"]: position for position in positions_rows}
    else:
        position_codes = set()
        position_map = {}

    rebalance_rows = []
    for row in rankings:
        action = "HOLD" if row["stock_code"] in position_codes else "BUY"
        rebalance_rows.append({
            "action": action,
            "stock_code": row["stock_code"],
            "stock_name": row.get("stock_name", ""),
            "rank": row.get("rank", 0),
        })
    for code in position_codes - rank_codes:
        position = position_map[code]
        rebalance_rows.append({
            "action": "SELL",
            "stock_code": code,
            "stock_name": position.get("name", ""),
            "rank": 999,
        })
    rebalance = pd.DataFrame(rebalance_rows)

    if positions_rows:
        positions = pd.DataFrame([
            {
                "stock_code": position["code"],
                "stock_name": position.get("name", ""),
                "shares": position["shares"],
            }
            for position in positions_rows
        ])
    else:
        positions = pd.DataFrame(columns=["stock_code", "stock_name", "shares"])
    positions["stock_code"] = positions["stock_code"].astype(str).str.zfill(6)

    all_codes = list(dict.fromkeys(list(rebalance["stock_code"]) + list(positions["stock_code"])))
    snapshot = fetch_tencent_snapshot(all_codes)
    prices = dict(zip(snapshot["stock_code"], snapshot["price"]))
    missing_prices = [code for code in all_codes if code not in prices]
    if missing_prices:
        raise PlanServiceError(
            500,
            {
                "code": "DATA_SOURCE_UNAVAILABLE",
                "message": f"缺少报价: {missing_prices[:5]}...",
            },
        )

    try:
        sheet, summary = size_target_state(pd.DataFrame(rankings), positions, cash, prices)
    except SizingError as exc:
        raise PlanServiceError(
            409,
            {
                "code": exc.code,
                "message": exc.message,
                **exc.details,
            },
        ) from exc
    except SystemExit as exc:
        raise PlanServiceError(
            400,
            {
                "code": "ORDER_SIZING_FAILED",
                "message": str(exc),
            },
        ) from exc
    plan_service.ensure_order_cash_nonnegative("stock", summary)
    _persist_strategy_orders("stock", plan_date, sheet)

    account = db.get_current_account_summary()
    account_cash = account["stock_available_cash"] if account else 0
    orders = sheet.to_dict("records")
    return {
        "orders": orders,
        "summary": plan_service.summarize_order_cash(account_cash, cash - account_cash, orders),
    }


def _persist_strategy_orders(strategy: str, plan_date: str, sheet: pd.DataFrame) -> None:
    db.init_db()
    meta = db.get_strategy_run_meta(strategy, plan_date)
    run_id = meta["id"] if meta else None
    if run_id is None:
        return
    if strategy == "cb":
        db.insert_cb_orders(run_id, sheet)
    elif strategy == "stock":
        db.insert_stock_orders(run_id, sheet)
