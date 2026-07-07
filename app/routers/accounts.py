from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datasource import db

router = APIRouter(prefix="/api/account", tags=["account"])


class AccountContextIn(BaseModel):
    snapshot_date: str
    temperature: float


class AccountValueSnapshotIn(BaseModel):
    snapshot_date: str
    total: float
    cash: Optional[float] = None


ACCOUNT_IDS = set(db.ACCOUNT_VALUE_FIELDS)


@router.get("/latest")
def get_latest():
    return db.get_current_account_summary()


@router.get("/summary")
def get_summary():
    return db.get_current_account_summary()


@router.get("/history")
def get_history():
    return db.get_account_history()


@router.post("/context")
def post_context(body: AccountContextIn):
    db.insert_account_context(body.snapshot_date, body.temperature)
    return db.get_current_account_summary()


@router.post("/{account_id}/snapshot")
def post_account_snapshot(account_id: str, body: AccountValueSnapshotIn):
    if account_id not in ACCOUNT_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown account: {account_id}")
    db.insert_account_value_snapshot(account_id, body.snapshot_date, body.total, body.cash)
    return db.get_current_account_summary()
