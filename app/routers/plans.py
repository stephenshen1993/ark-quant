from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datasource import db

router = APIRouter(prefix="/api/plan", tags=["plan"])


@router.get("")
def get_plan(temperature: float = None):
    account = db.get_latest_account_snapshot()
    if temperature is not None and account:
        account = dict(account)
        account["temperature"] = temperature

    cb_orders = db.get_latest_orders("cb")
    cb_dates = db.get_ranking_dates("cb")
    cb_data_date = cb_dates[0] if cb_dates else None
    cb_trade_date = _trade_date_for("cb", cb_data_date)
    cb_rankings = db.get_latest_rankings("cb") if not cb_orders else []

    stock_orders = db.get_latest_orders("stock")
    stock_dates = db.get_ranking_dates("stock")
    stock_data_date = stock_dates[0] if stock_dates else None
    stock_trade_date = _trade_date_for("stock", stock_data_date)
    stock_rankings = db.get_latest_rankings("stock") if not stock_orders else []

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
        sells = sum(o.get("amount", 0) for o in orders if o.get("delta_shares", 0) < 0)
        buys  = sum(o.get("amount", 0) for o in orders if o.get("delta_shares", 0) > 0)
        book = round(base + sells - buys, 2)
        return {
            "book_balance": book,
            "transfer_delta": round(delta, 2),
            "cash_left": round(book + delta, 2),
        }

    return {
        "generated_at": datetime.now().isoformat(),
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


class SizeOrdersRequest(BaseModel):
    cash: float


@router.post("/{strategy}/size-orders")
def size_orders(strategy: str, req: SizeOrdersRequest):
    """根据最新榜单 + 持仓 + 现金，生成具体买卖张数/股数。"""
    if strategy == "cb":
        return _size_cb_orders(req.cash)
    if strategy == "stock":
        return _size_stock_orders(req.cash)
    raise HTTPException(400, {"code": "INVALID_STRATEGY", "message": f"未知策略: {strategy}"})


def _size_cb_orders(cash: float) -> dict:
    import pandas as pd
    from datasource.market import fetch_cb_prices_tencent
    from strategies.cb_rotation.size_orders import size_rebalance

    rankings = db.get_latest_rankings("cb")
    if not rankings:
        raise HTTPException(400, {"code": "NO_RANKINGS", "message": "没有转债榜单，请先运行策略。"})

    positions_rows = db.get_latest_positions("cb")
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

    sheet, summary = size_rebalance(target, positions, cash, prices, min_trade_value=1000)

    db.init_db()
    run_id = db.get_latest_run_id("cb")
    if run_id is not None:
        db.insert_cb_orders(run_id, sheet)

    account = db.get_latest_account_snapshot()
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

    rankings = db.get_latest_rankings("stock")
    if not rankings:
        raise HTTPException(400, {"code": "NO_RANKINGS", "message": "没有股票榜单，请先运行策略。"})

    positions_rows = db.get_latest_positions("stock")
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
    run_id = db.get_latest_run_id("stock")
    if run_id is not None:
        db.insert_stock_orders(run_id, sheet)

    account = db.get_latest_account_snapshot()
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
