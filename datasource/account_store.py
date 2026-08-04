"""Account context and account-value snapshot persistence helpers."""
from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime

from investment_model import ACCOUNT_DEFINITIONS, account_definition

ACCOUNT_VALUE_FIELDS = {
    "stock": ("stock_total", "stock_cash"),
    "cb": ("bond_total", "bond_cash"),
    "changqian": ("changqian_total", None),
    "cash": ("cash_pool", None),
    "overseas": ("overseas_total", None),
}

ACCOUNT_METADATA = ACCOUNT_DEFINITIONS


def validate_iso_date(value: str, field: str = "snapshot_date") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be an ISO date")
    return value


def validate_iso_datetime(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO datetime")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO datetime") from exc
    return value


def validate_nonnegative_finite(value: float, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a nonnegative finite number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a nonnegative finite number")
    return number


def validate_account_id(account_id: str) -> str:
    if account_id not in ACCOUNT_VALUE_FIELDS:
        raise ValueError(f"Unknown account: {account_id}")
    return account_id


def insert_account_snapshot(
    conn: sqlite3.Connection,
    snapshot_date: str,
    temperature: float,
    stock_total: float,
    stock_cash: float,
    bond_total: float,
    bond_cash: float,
    changqian_total: float,
    cash_pool: float,
    overseas_total: float,
    changqian_updated_at: str | None = None,
    cash_pool_updated_at: str | None = None,
    overseas_updated_at: str | None = None,
) -> int:
    del changqian_updated_at, cash_pool_updated_at, overseas_updated_at
    validate_iso_date(snapshot_date)
    values = (
        (stock_total, "stock_total"),
        (stock_cash, "stock_cash"),
        (bond_total, "bond_total"),
        (bond_cash, "bond_cash"),
        (changqian_total, "changqian_total"),
        (cash_pool, "cash_pool"),
        (overseas_total, "overseas_total"),
    )
    for value, field in values:
        validate_nonnegative_finite(value, field)
    insert_account_context(conn, snapshot_date, temperature)
    ids = [
        insert_account_value_snapshot(conn, "stock", snapshot_date, stock_total, stock_cash),
        insert_account_value_snapshot(conn, "cb", snapshot_date, bond_total, bond_cash),
        insert_account_value_snapshot(conn, "changqian", snapshot_date, changqian_total),
        insert_account_value_snapshot(conn, "cash", snapshot_date, cash_pool),
        insert_account_value_snapshot(conn, "overseas", snapshot_date, overseas_total),
    ]
    return ids[-1]


def insert_account_value_snapshot(
    conn: sqlite3.Connection,
    account_id: str,
    snapshot_date: str,
    total: float,
    cash: float | None = None,
    frozen_cash: float | None = None,
) -> int:
    validate_account_id(account_id)
    validate_iso_date(snapshot_date)
    total = validate_nonnegative_finite(total, "total")
    if cash is not None:
        cash = validate_nonnegative_finite(cash, "cash")
    if frozen_cash is not None:
        frozen_cash = validate_nonnegative_finite(frozen_cash, "frozen_cash")
    if frozen_cash is None:
        frozen_cash = 0.0
    if cash is None and frozen_cash:
        raise ValueError("frozen_cash requires cash")
    if cash is not None and frozen_cash > cash:
        raise ValueError("frozen_cash must be less than or equal to cash")
    return insert_account_value_snapshot_row(
        conn, account_id, snapshot_date, total, cash, frozen_cash
    )


def insert_account_value_snapshot_row(
    conn: sqlite3.Connection,
    account_id: str,
    snapshot_date: str,
    total: float,
    cash: float | None,
    frozen_cash: float,
) -> int:
    cur = conn.execute(
        """INSERT INTO account_value_snapshots
           (account_id,snapshot_date,total,cash,frozen_cash,created_at)
           VALUES (?,?,?,?,?,?)""",
        (account_id, snapshot_date, total, cash, frozen_cash, datetime.now().isoformat()),
    )
    return cur.lastrowid


def insert_account_context(
    conn: sqlite3.Connection,
    snapshot_date: str,
    temperature: float,
    *,
    check_type: str = "a_internal",
    new_contribution: float = 0,
    b_purchase_status: str = "unchecked",
    b_purchase_limit: float = 0,
    b_purchase_checked_at: str | None = None,
    b_purchase_source: str | None = None,
) -> int:
    validate_iso_date(snapshot_date)
    if check_type not in {"monthly_contribution", "quarterly", "a_internal", "b_recovery", "ad_hoc"}:
        raise ValueError("invalid check_type")
    if b_purchase_status not in {"unchecked", "unavailable", "available"}:
        raise ValueError("invalid b_purchase_status")
    new_contribution = validate_nonnegative_finite(new_contribution, "new_contribution")
    b_purchase_limit = validate_nonnegative_finite(b_purchase_limit, "b_purchase_limit")
    if b_purchase_status != "available":
        b_purchase_limit = 0
    elif b_purchase_limit <= 0:
        raise ValueError("available b_purchase_status requires b_purchase_limit")
    if b_purchase_checked_at is not None:
        validate_iso_datetime(b_purchase_checked_at, "b_purchase_checked_at")
    if b_purchase_source is not None and not isinstance(b_purchase_source, str):
        raise ValueError("b_purchase_source must be text")
    cur = conn.execute(
        """INSERT INTO account_contexts
           (snapshot_date,temperature,check_type,new_contribution,b_purchase_status,b_purchase_limit,
            b_purchase_checked_at,b_purchase_source,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            snapshot_date,
            temperature,
            check_type,
            new_contribution,
            b_purchase_status,
            b_purchase_limit,
            b_purchase_checked_at,
            b_purchase_source,
            datetime.now().isoformat(),
        ),
    )
    return cur.lastrowid


def get_current_account_summary(conn: sqlite3.Connection) -> dict | None:
    context = conn.execute(
        "SELECT * FROM account_contexts ORDER BY id DESC LIMIT 1"
    ).fetchone()
    values = conn.execute(
        """
        SELECT v.*
        FROM account_value_snapshots v
        JOIN (
            SELECT account_id, MAX(id) AS id
            FROM account_value_snapshots
            GROUP BY account_id
        ) latest ON latest.id = v.id
        """
    ).fetchall()
    if not context and not values:
        return None
    base = {}
    if context:
        ctx = dict(context)
        base["id"] = ctx["id"]
        base["snapshot_date"] = ctx["snapshot_date"]
        base["temperature"] = ctx["temperature"]
        base["check_type"] = ctx["check_type"]
        base["new_contribution"] = ctx["new_contribution"]
        base["b_purchase_status"] = ctx["b_purchase_status"]
        base["b_purchase_limit"] = ctx["b_purchase_limit"]
        base["b_purchase_checked_at"] = ctx["b_purchase_checked_at"]
        base["b_purchase_source"] = ctx["b_purchase_source"]
        base["created_at"] = ctx["created_at"]
    elif values:
        first = dict(values[0])
        base["snapshot_date"] = first["snapshot_date"]
        base["created_at"] = first["created_at"]
    snap = {
        "id": base.get("id"),
        "snapshot_date": base.get("snapshot_date"),
        "temperature": base.get("temperature"),
        "check_type": base.get("check_type", "a_internal"),
        "new_contribution": base.get("new_contribution", 0) or 0,
        "b_purchase_status": base.get("b_purchase_status", "unchecked") or "unchecked",
        "b_purchase_limit": base.get("b_purchase_limit", 0) or 0,
        "b_purchase_checked_at": base.get("b_purchase_checked_at"),
        "b_purchase_source": base.get("b_purchase_source"),
        "stock_total": base.get("stock_total", 0) or 0,
        "stock_cash": base.get("stock_cash", 0) or 0,
        "stock_frozen_cash": 0,
        "stock_available_cash": base.get("stock_cash", 0) or 0,
        "bond_total": base.get("bond_total", 0) or 0,
        "bond_cash": base.get("bond_cash", 0) or 0,
        "bond_frozen_cash": 0,
        "bond_available_cash": base.get("bond_cash", 0) or 0,
        "changqian_total": base.get("changqian_total", 0) or 0,
        "cash_pool": base.get("cash_pool", 0) or 0,
        "overseas_total": base.get("overseas_total", 0) or 0,
        "created_at": base.get("created_at"),
    }
    updated_at: dict[str, str] = {}
    snapshot_dates: dict[str, str] = {}
    for value in values:
        item = dict(value)
        account_id = item["account_id"]
        updated_at[account_id] = item["created_at"]
        snapshot_dates[account_id] = item["snapshot_date"]
        if account_id == "stock":
            snap["stock_total"] = item["total"]
            snap["stock_cash"] = item["cash"] or 0
            snap["stock_frozen_cash"] = item["frozen_cash"] or 0
            snap["stock_available_cash"] = snap["stock_cash"] - snap["stock_frozen_cash"]
        elif account_id == "cb":
            snap["bond_total"] = item["total"]
            snap["bond_cash"] = item["cash"] or 0
            snap["bond_frozen_cash"] = item["frozen_cash"] or 0
            snap["bond_available_cash"] = snap["bond_cash"] - snap["bond_frozen_cash"]
        elif account_id == "changqian":
            snap["changqian_total"] = item["total"]
        elif account_id == "cash":
            snap["cash_pool"] = item["total"]
        elif account_id == "overseas":
            snap["overseas_total"] = item["total"]
    snap["account_updated_at"] = updated_at
    snap["account_snapshot_dates"] = snapshot_dates
    snap["accounts"] = build_account_items(snap)
    snap["total_assets"] = (
        snap["stock_total"]
        + snap["bond_total"]
        + snap["changqian_total"]
        + snap["cash_pool"]
        + snap["overseas_total"]
    )
    return snap


def build_account_items(snap: dict) -> list[dict]:
    items = []
    for account_id in ("stock", "cb", "changqian", "overseas", "cash"):
        total_key, cash_key = ACCOUNT_VALUE_FIELDS[account_id]
        meta = account_definition(account_id)
        frozen_key = cash_key.replace("_cash", "_frozen_cash") if cash_key else None
        available_key = cash_key.replace("_cash", "_available_cash") if cash_key else None
        items.append({
            "id": account_id,
            "label": meta["label"],
            "sub": meta["sub"],
            "total": snap.get(total_key, 0) or 0,
            "cash": (snap.get(cash_key, 0) or 0) if cash_key else None,
            "frozen_cash": (
                snap.get(frozen_key, 0) if frozen_key else None
            ),
            "available_cash": (
                snap.get(available_key, snap.get(cash_key, 0)) if available_key else None
            ),
            "snapshot_date": snap.get("account_snapshot_dates", {}).get(account_id),
            "updated_at": snap.get("account_updated_at", {}).get(account_id),
            "asset_classes": meta["asset_classes"],
            "country_exposure": meta["country_exposure"],
            "strategy_ids": meta["strategy_ids"],
            "strategy_names": meta["strategy_names"],
            "participates_in_domestic_rebalance": meta[
                "participates_in_domestic_rebalance"
            ],
        })
    return items


def get_account_history(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT snapshot_date, account_id, total
        FROM account_value_snapshots
        ORDER BY snapshot_date, id
        """
    ).fetchall()
    grouped: dict[str, dict] = {}
    for row in rows:
        item = dict(row)
        target = grouped.setdefault(item["snapshot_date"], {
            "snapshot_date": item["snapshot_date"],
            "stock_total": 0,
            "bond_total": 0,
            "changqian_total": 0,
            "cash_pool": 0,
            "overseas_total": 0,
        })
        if item["account_id"] == "stock":
            target["stock_total"] = item["total"]
        elif item["account_id"] == "cb":
            target["bond_total"] = item["total"]
        elif item["account_id"] == "changqian":
            target["changqian_total"] = item["total"]
        elif item["account_id"] == "cash":
            target["cash_pool"] = item["total"]
        elif item["account_id"] == "overseas":
            target["overseas_total"] = item["total"]
    return list(grouped.values())
