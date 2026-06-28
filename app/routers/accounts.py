from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from datasource import db

router = APIRouter(prefix="/api/account", tags=["account"])


class AccountSnapshotIn(BaseModel):
    snapshot_date: str
    temperature: float
    stock_total: float
    stock_cash: float
    bond_total: float
    bond_cash: float
    changqian_total: float
    cash_pool: float
    overseas_total: float


@router.get("/latest")
def get_latest():
    return db.get_latest_account_snapshot()


@router.get("/history")
def get_history():
    return db.get_account_history()


@router.post("/snapshot")
def post_snapshot(body: AccountSnapshotIn):
    db.insert_account_snapshot(**body.model_dump())
    return {"ok": True}
