from __future__ import annotations
from typing import Annotated, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from datasource import db

router = APIRouter(prefix="/api/account", tags=["account"])


NonnegativeFinite = Annotated[float, Field(ge=0, allow_inf_nan=False)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountContextIn(StrictRequest):
    snapshot_date: str
    temperature: FiniteFloat


class AccountValueSnapshotIn(StrictRequest):
    snapshot_date: str
    total: NonnegativeFinite
    cash: Optional[NonnegativeFinite] = None
    available_cash: Optional[NonnegativeFinite] = None
    frozen_cash: Optional[NonnegativeFinite] = 0


class PositionStateIn(StrictRequest):
    code: str
    name: str = ""
    shares: NonnegativeFinite


class AccountStateIn(StrictRequest):
    snapshot_date: str
    total: NonnegativeFinite
    cash: Optional[NonnegativeFinite] = None
    available_cash: Optional[NonnegativeFinite] = None
    frozen_cash: Optional[NonnegativeFinite] = 0
    positions: list[PositionStateIn]


ACCOUNT_IDS = set(db.ACCOUNT_VALUE_FIELDS)


def _resolve_cash_balance(body: AccountValueSnapshotIn | AccountStateIn) -> float | None:
    """Accept legacy cash balance or derive it from available + frozen cash."""
    frozen_cash = body.frozen_cash or 0.0
    if body.available_cash is not None:
        derived_cash = body.available_cash + frozen_cash
        if body.cash is not None and abs(body.cash - derived_cash) > 1e-9:
            raise ValueError("cash must equal available_cash plus frozen_cash")
        return derived_cash
    if body.cash is None and frozen_cash:
        raise ValueError("frozen_cash requires cash or available_cash")
    return body.cash


def public_account_summary(summary: dict | None) -> dict | None:
    """Expose account snapshots as account-level records, not DB calculation fields."""
    if summary is None:
        return None
    context = None
    if summary.get("snapshot_date") is not None or summary.get("temperature") is not None:
        context = {
            "snapshot_date": summary.get("snapshot_date"),
            "temperature": summary.get("temperature"),
        }
    return {
        "total_assets": summary.get("total_assets", 0) or 0,
        "context": context,
        "accounts": summary.get("accounts", []),
    }


@router.get("/latest")
def get_latest():
    return public_account_summary(db.get_current_account_summary())


@router.get("/summary")
def get_summary():
    return public_account_summary(db.get_current_account_summary())


@router.get("/history")
def get_history():
    return db.get_account_history()


@router.post("/context")
def post_context(body: AccountContextIn):
    try:
        db.insert_account_context(body.snapshot_date, body.temperature)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_account_summary(db.get_current_account_summary())


@router.post("/{account_id}/snapshot")
def post_account_snapshot(account_id: str, body: AccountValueSnapshotIn):
    if account_id not in ACCOUNT_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown account: {account_id}")
    try:
        cash = _resolve_cash_balance(body)
        db.insert_account_value_snapshot(
            account_id, body.snapshot_date, body.total, cash, body.frozen_cash
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_account_summary(db.get_current_account_summary())


@router.post("/{account_id}/state")
def post_account_state(account_id: str, body: AccountStateIn):
    if account_id not in {"stock", "cb"}:
        raise HTTPException(status_code=400, detail=f"Unknown account strategy: {account_id}")
    try:
        cash = _resolve_cash_balance(body)
        if cash is None:
            raise ValueError("cash or available_cash is required")
        saved = db.append_account_state_snapshot(
            account_id,
            body.snapshot_date,
            body.total,
            cash,
            [position.model_dump() for position in body.positions],
            body.frozen_cash,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = public_account_summary(db.get_current_account_summary())
    response["position_snapshot"] = saved["position_snapshot"]
    return response
