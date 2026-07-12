from __future__ import annotations
from datetime import date, datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datasource import db
from datasource.youzhiyouxing import TemperatureFetchError, get_or_fetch_market_temperature

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

    date_errors = _validate_plan_inputs(plan_date, account)
    if date_errors:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PLAN_INPUT_DATE_MISMATCH",
                "message": "交易计划输入日期不一致，请按下方逐项更新到同一个计划日。",
                "plan_date": plan_date,
                "market_temperature": market_temperature.to_dict(),
                "errors": date_errors,
            },
        )

    cb_orders = db.get_orders("cb", plan_date)
    cb_data_date = plan_date
    cb_trade_date = _trade_date_for("cb", cb_data_date)
    cb_rankings = db.get_rankings("cb", plan_date) if not cb_orders else []

    stock_orders = db.get_orders("stock", plan_date)
    stock_data_date = plan_date
    stock_trade_date = _trade_date_for("stock", stock_data_date)
    stock_rankings = db.get_rankings("stock", plan_date) if not stock_orders else []

    transfer_steps = []
    if account:
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from rebalance import calc_targets, build_transfer_plan
            domestic = (
                account["stock_total"] + account["bond_total"]
                + account["changqian_total"] + account["cash_pool"]
            )
            targets = calc_targets(domestic, account["temperature"])
            deltas = {
                "stock":     targets["stock"]     - account["stock_total"],
                "bond":      targets["bond"]      - account["bond_total"],
                "changqian": targets["changqian"] - account["changqian_total"],
                "cash_pool": targets["cash_pool"] - account["cash_pool"],
            }
            transfer_steps = build_transfer_plan(deltas)
        except Exception:
            transfer_steps = []

    def _order_summary(orders, cash_key: str) -> dict | None:
        if not orders or not account:
            return None
        delta = deltas.get(cash_key, 0) if transfer_steps else 0
        base = account.get(f"{cash_key}_cash", 0)
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
        "transfer_steps": transfer_steps,
        "transfer_deltas": deltas if transfer_steps else {},
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
    errors = []
    account_input_end = _account_input_window_end(plan_date)
    if not account:
        errors.append({"input": "account", "date": None, "expected": plan_date, "message": "缺少账户快照"})
    else:
        account_updates = account.get("account_updated_at") or {}
        legacy_account_dates = account.get("account_snapshot_dates") or {}
        required_accounts = {
            "stock": "股票账户",
            "cb": "转债账户",
            "changqian": "长钱账户",
            "cash": "现金账户",
            "overseas": "海外长钱",
        }
        for account_id, label in required_accounts.items():
            actual = account_updates.get(account_id)
            legacy_date = legacy_account_dates.get(account_id)
            if (
                not _is_account_update_acceptable(actual, plan_date, account_input_end)
                and legacy_date != plan_date
            ):
                errors.append({
                    "input": f"account.{account_id}",
                    "date": actual,
                    "expected": f"{plan_date} 至 {account_input_end}",
                    "message": f"{label}更新时间不在计划输入窗口内",
                })

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


def _is_account_update_acceptable(updated_at: str | None, plan_date: str, window_end: str) -> bool:
    if updated_at is None:
        return False
    try:
        actual_date = datetime.fromisoformat(updated_at).date()
        start = date.fromisoformat(plan_date)
        end = date.fromisoformat(window_end)
    except ValueError:
        return False
    return start <= actual_date <= end


class SizeOrdersRequest(BaseModel):
    cash: float


@router.post("/{strategy}/size-orders")
def size_orders(strategy: str, req: SizeOrdersRequest):
    """根据计划日期榜单 + 持仓 + 现金，生成具体买卖张数/股数。"""
    if strategy == "cb":
        _ensure_plan_inputs_consistent()
        return _size_cb_orders(req.cash)
    if strategy == "stock":
        _ensure_plan_inputs_consistent()
        return _size_stock_orders(req.cash)
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
    positions_rows = _positions_for_plan("cb", plan_date, input_end)
    if not positions_rows:
        raise HTTPException(400, {"code": "NO_POSITIONS", "message": f"没有 {plan_date} 至 {input_end} 输入窗口内的转债持仓，请先在账户页保存。"})
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

    sheet, summary = size_rebalance(target, positions, cash, prices)

    db.init_db()
    meta = db.get_strategy_run_meta("cb", plan_date)
    run_id = meta["id"] if meta else None
    if run_id is not None:
        db.insert_cb_orders(run_id, sheet)

    account = db.get_current_account_summary()
    acct_cash = account["bond_cash"] if account else 0
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
    positions_rows = _positions_for_plan("stock", plan_date, input_end)
    if not positions_rows:
        raise HTTPException(400, {"code": "NO_POSITIONS", "message": f"没有 {plan_date} 至 {input_end} 输入窗口内的股票持仓，请先在账户页保存。"})
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

    sheet, summary = size_rebalance(reb, positions, cash, prices, min_trade_value=1000)

    db.init_db()
    meta = db.get_strategy_run_meta("stock", plan_date)
    run_id = meta["id"] if meta else None
    if run_id is not None:
        db.insert_stock_orders(run_id, sheet)

    account = db.get_current_account_summary()
    acct_cash = account["stock_cash"] if account else 0
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


def _positions_for_plan(strategy: str, plan_date: str, input_end: str) -> list[dict]:
    rows = db.get_positions_updated_between(strategy, plan_date, input_end)
    if rows:
        return rows
    return db.get_positions_by_date(strategy, plan_date)


def _ensure_plan_inputs_consistent() -> str:
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
    return plan_date
