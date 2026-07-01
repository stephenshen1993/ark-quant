from __future__ import annotations
from typing import Literal, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from datasource import db

router = APIRouter(prefix="/api/positions", tags=["positions"])


class PositionIn(BaseModel):
    code: str
    name: str = ""
    shares: int


@router.get("/{strategy}/dates")
def get_dates(strategy: Literal["cb", "stock"]):
    return db.get_position_dates(strategy)


@router.get("/{strategy}/quotes")
def get_quotes(strategy: Literal["cb", "stock"]):
    """最新持仓 + 实时报价，返回每只市值/占比与持仓总市值。"""
    positions = db.get_latest_positions(strategy)
    codes = [str(p["code"]).zfill(6) for p in positions]
    if not codes:
        return {"total_value": 0, "items": []}

    if strategy == "cb":
        from datasource.market import fetch_cb_quotes_tencent
        q = fetch_cb_quotes_tencent(codes)
        prices = {c: v["price"] for c, v in q.items()}
    else:
        from datasource.market import fetch_tencent_snapshot
        snap = fetch_tencent_snapshot(codes)
        prices = dict(zip(snap["stock_code"], snap["price"])) if not snap.empty else {}

    items = []
    total = 0.0
    for p in positions:
        code = str(p["code"]).zfill(6)
        price = prices.get(code)
        value = (price or 0) * p["shares"]
        total += value
        items.append({
            "code": code, "name": p["name"], "shares": p["shares"],
            "price": price, "value": value,
        })
    for it in items:
        it["weight"] = (it["value"] / total) if total else 0
    return {"total_value": total, "items": items}


@router.get("/{strategy}/quote")
def get_quote(strategy: Literal["cb", "stock"], code: str):
    """单标的实时名称+价格，用于录入时自动回填。"""
    code = str(code).zfill(6)
    if strategy == "cb":
        from datasource.market import fetch_cb_quotes_tencent
        q = fetch_cb_quotes_tencent([code]).get(code)
        if q:
            return {"code": code, "name": q["name"], "price": q["price"]}
    else:
        from datasource.market import fetch_tencent_snapshot
        snap = fetch_tencent_snapshot([code])
        if not snap.empty:
            r = snap.iloc[0]
            return {"code": code, "name": r["stock_name_q"], "price": float(r["price"])}
    return {"code": code, "name": None, "price": None}


@router.get("/{strategy}")
def get_positions(strategy: Literal["cb", "stock"], date: Optional[str] = None):
    if date:
        return db.get_positions_by_date(strategy, date)
    return db.get_latest_positions(strategy)


@router.post("/{strategy}")
def post_positions(strategy: Literal["cb", "stock"], body: list[PositionIn], position_date: str):
    db.insert_positions(strategy, position_date, [p.model_dump() for p in body])
    return {"ok": True}
