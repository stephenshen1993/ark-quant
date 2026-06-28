from __future__ import annotations
from typing import Literal
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


@router.get("/{strategy}")
def get_positions(strategy: Literal["cb", "stock"], date: Optional[str] = None):
    if date:
        return db.get_positions_by_date(strategy, date)
    return db.get_latest_positions(strategy)


@router.post("/{strategy}")
def post_positions(strategy: Literal["cb", "stock"], body: list[PositionIn], position_date: str):
    db.insert_positions(strategy, position_date, [p.model_dump() for p in body])
    return {"ok": True}
