from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter
from datasource import db

router = APIRouter(prefix="/api/plan", tags=["plan"])


@router.get("")
def get_plan():
    missing = []
    account = db.get_latest_account_snapshot()
    if not account:
        missing.append("account")

    cb_orders = db.get_latest_orders("cb")
    cb_dates = db.get_ranking_dates("cb")
    if not cb_orders:
        missing.append("cb_orders")

    stock_orders = db.get_latest_orders("stock")
    stock_dates = db.get_ranking_dates("stock")
    if not stock_orders:
        missing.append("stock_orders")

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

    return {
        "generated_at": datetime.now().isoformat(),
        "missing": missing,
        "account": account,
        "transfer_steps": transfer_steps,
        "cb": {
            "data_date": cb_dates[0] if cb_dates else None,
            "orders": cb_orders,
        },
        "stock": {
            "data_date": stock_dates[0] if stock_dates else None,
            "orders": stock_orders,
        },
    }
