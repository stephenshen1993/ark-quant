from __future__ import annotations
from typing import Annotated, Literal, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from datasource import db

router = APIRouter(prefix="/api/positions", tags=["positions"])


NonnegativeFinite = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PositionIn(StrictRequest):
    code: str
    name: str = ""
    shares: NonnegativeFinite


def _validate_snapshot_query(date: Optional[str], asof: bool) -> None:
    if date == "":
        raise HTTPException(status_code=400, detail="date must not be empty")
    if asof and date is None:
        raise HTTPException(status_code=400, detail="asof requires date")


@router.get("/{strategy}/dates")
def get_dates(strategy: Literal["cb", "stock"]):
    return db.get_position_dates(strategy)


@router.get("/{strategy}/quotes")
def get_quotes(strategy: Literal["cb", "stock"], date: Optional[str] = None, asof: bool = False):
    """指定日期持仓 + 实时报价，返回每只市值/占比与持仓总市值。"""
    _validate_snapshot_query(date, asof)
    try:
        positions = (
            db.get_positions_asof(strategy, date) if date is not None and asof
            else db.get_positions_by_date(strategy, date) if date is not None
            else db.get_latest_positions(strategy)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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


@router.get("/{strategy}/snapshot")
def get_position_snapshot(
    strategy: Literal["cb", "stock"],
    date: Optional[str] = None,
    asof: bool = False,
):
    _validate_snapshot_query(date, asof)
    try:
        if date is not None and asof:
            return db.get_position_snapshot_asof(strategy, date)
        if date is not None:
            return db.get_position_snapshot_by_date(strategy, date)
        return db.get_latest_position_snapshot(strategy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{strategy}")
def get_positions(strategy: Literal["cb", "stock"], date: Optional[str] = None, asof: bool = False):
    _validate_snapshot_query(date, asof)
    try:
        if date is not None:
            if asof:
                return db.get_positions_asof(strategy, date)
            return db.get_positions_by_date(strategy, date)
        return db.get_latest_positions(strategy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{strategy}")
def post_positions(strategy: Literal["cb", "stock"], body: list[PositionIn], position_date: str):
    try:
        snapshot = db.append_position_snapshot(
            strategy, position_date, [p.model_dump() for p in body]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "position_snapshot": snapshot}
