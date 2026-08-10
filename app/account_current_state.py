"""Current account data as an account-scoped update model.

The public interface deliberately hides dated snapshots.  Each account exposes
one editable current state, an opaque version for optimistic concurrency, and a
separate derived valuation.  Historical rows remain an internal audit trail.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

from datasource import account_store, db, position_store, strategy_store
from investment_model import ACCOUNT_DEFINITIONS, account_definition

SECURITIES_ACCOUNTS = {"stock", "cb"}
ACCOUNT_ORDER = ("stock", "cb", "cash", "changqian", "overseas")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class CurrentAccountError(ValueError):
    pass


class AccountVersionConflict(CurrentAccountError):
    pass


def list_current_accounts() -> list[dict]:
    with db._conn() as conn:
        accounts = [_current_account(conn, account_id) for account_id in ACCOUNT_ORDER]
    return [_refresh_current_valuation(account) for account in accounts]


def get_current_account(account_id: str) -> dict:
    _validate_account(account_id)
    with db._conn() as conn:
        account = _current_account(conn, account_id)
    return _refresh_current_valuation(account)


def build_current_account_summary() -> dict | None:
    """Build the single account summary used by both the UI and plan generation."""
    with db._conn() as conn:
        summary = account_store.get_current_account_summary(conn)
        accounts = [_current_account(conn, account_id) for account_id in ACCOUNT_ORDER]
    accounts = [_refresh_current_valuation(account) for account in accounts]
    if summary is None and all(account["record_state"] == "missing" for account in accounts):
        return None

    result = dict(summary or {})
    updated_at = {}
    snapshot_dates = {}
    totals = []
    for account in accounts:
        account_id = account["account_id"]
        total_key, cash_key = account_store.ACCOUNT_VALUE_FIELDS[account_id]
        total = (
            account["valuation"].get("total")
            if account["record_state"] == "recorded"
            else None
        )
        result[total_key] = total
        totals.append(total)
        if cash_key:
            raw = account["raw_data"]
            available_cash = raw.get("available_cash")
            frozen_cash = raw.get("frozen_cash")
            cash = (
                None
                if available_cash is None or frozen_cash is None
                else available_cash + frozen_cash
            )
            result[cash_key] = cash
            result[cash_key.replace("_cash", "_available_cash")] = available_cash
            result[cash_key.replace("_cash", "_frozen_cash")] = frozen_cash
        if account["as_of"] is not None:
            snapshot_dates[account_id] = account["as_of"]
        if account["updated_at"] is not None:
            updated_at[account_id] = account["updated_at"]

    result["account_snapshot_dates"] = snapshot_dates
    result["account_updated_at"] = updated_at
    result["accounts"] = account_store.build_account_items(result)
    result["total_assets"] = None if any(total is None for total in totals) else sum(totals)
    _invalidate_plans_with_different_valuation(result)
    return result


def has_explicit_current_state(account_id: str) -> bool:
    """Return whether the account has crossed into the current-state write model."""
    _validate_account(account_id)
    with db._conn() as conn:
        row = conn.execute(
            """SELECT 1 FROM account_state_versions
               WHERE account_id=? AND operation!='backfill' LIMIT 1""",
            (account_id,),
        ).fetchone()
        return row is not None


def update_current_account(
    account_id: str,
    *,
    expected_version: str | None,
    amount: float | None = None,
    available_cash: float | None = None,
    frozen_cash: float | None = None,
    positions: list[dict] | None = None,
) -> dict:
    """Save one account from raw user facts and invalidate plans only on change."""
    _validate_account(account_id)
    raw = _normalize_raw_data(
        account_id,
        amount=amount,
        available_cash=available_cash,
        frozen_cash=frozen_cash,
        positions=positions,
    )
    valuation = _derive_valuation(account_id, raw)

    with db._conn() as conn:
        with _write_transaction(conn):
            current = _current_account(conn, account_id)
            _check_version(current, expected_version)
            changed = current["record_state"] == "missing" or current["raw_data"] != raw
            if not changed and not _valuation_needs_refresh(current):
                valuation = current["valuation"]
            plan_input_changed = changed or _plan_valuation_changed(
                current["valuation"], valuation
            )
            _insert_version(
                conn,
                account_id,
                raw,
                valuation,
                operation="update" if changed else "confirm",
            )
            if plan_input_changed:
                _invalidate_derived_plans(conn)
        return _current_account(conn, account_id)


def confirm_current_account(account_id: str, *, expected_version: str) -> dict:
    """Confirm current raw data and invalidate plans only if effective inputs changed."""
    _validate_account(account_id)
    with db._conn() as conn:
        current = _current_account(conn, account_id)
        _check_version(current, expected_version)
        if current["record_state"] == "missing":
            raise CurrentAccountError("账户尚无可确认的数据")
    valuation = current["valuation"]
    if _valuation_needs_refresh(current):
        valuation = _derive_valuation(account_id, current["raw_data"])
    plan_input_changed = _plan_valuation_changed(current["valuation"], valuation)

    with db._conn() as conn:
        with _write_transaction(conn):
            current = _current_account(conn, account_id)
            _check_version(current, expected_version)
            _insert_version(
                conn,
                account_id,
                current["raw_data"],
                valuation,
                operation="confirm",
            )
            if plan_input_changed:
                _invalidate_derived_plans(conn)
        return _current_account(conn, account_id)


def backfill_account(
    account_id: str,
    *,
    as_of: str,
    amount: float | None = None,
    available_cash: float | None = None,
    frozen_cash: float | None = None,
    positions: list[dict] | None = None,
) -> dict:
    """Internal audit import; an older date can never replace a newer current state."""
    _validate_account(account_id)
    account_store.validate_iso_date(as_of, "as_of")
    if as_of > date.today().isoformat():
        raise CurrentAccountError("回填日期不能晚于今天")
    raw = _normalize_raw_data(
        account_id,
        amount=amount,
        available_cash=available_cash,
        frozen_cash=frozen_cash,
        positions=positions,
    )
    valuation = _derive_valuation(account_id, raw)
    with db._conn() as conn:
        with _write_transaction(conn):
            current = _current_account(conn, account_id)
            if current["as_of"] is not None and as_of >= current["as_of"]:
                raise CurrentAccountError("回填日期必须早于该账户当前数据日期")
            _insert_version(
                conn,
                account_id,
                raw,
                valuation,
                operation="backfill",
                as_of=as_of,
            )
        return _current_account(conn, account_id)


def _validate_account(account_id: str) -> None:
    if account_id not in ACCOUNT_DEFINITIONS:
        raise CurrentAccountError(f"未知账户：{account_id}")


def _normalize_raw_data(
    account_id: str,
    *,
    amount: float | None,
    available_cash: float | None,
    frozen_cash: float | None,
    positions: list[dict] | None,
) -> dict:
    if account_id not in SECURITIES_ACCOUNTS:
        if amount is None:
            raise CurrentAccountError("资金余额不能为空")
        return {"amount": account_store.validate_nonnegative_finite(amount, "amount")}

    if available_cash is None or frozen_cash is None:
        raise CurrentAccountError("可用资金和冻结资金不能为空")
    if positions is None:
        raise CurrentAccountError("持仓列表不能为空；空仓请明确提交空列表")
    available = account_store.validate_nonnegative_finite(available_cash, "available_cash")
    frozen = account_store.validate_nonnegative_finite(frozen_cash, "frozen_cash")
    normalized = position_store.normalize_position_rows(
        [
            {
                "code": item.get("code"),
                "name": "",
                "shares": item.get("quantity"),
            }
            for item in (positions or [])
        ]
    )
    return {
        "available_cash": available,
        "frozen_cash": frozen,
        "positions": [
            {"code": item["code"], "quantity": item["shares"]}
            for item in normalized
            if item["shares"] > 0
        ],
    }


def _derive_valuation(account_id: str, raw: dict) -> dict:
    if account_id not in SECURITIES_ACCOUNTS:
        return {
            "status": "available",
            "total": raw["amount"],
            "missing_codes": [],
            "items": [],
        }

    positions = raw["positions"]
    if not positions:
        return {
            "status": "available",
            "total": raw["available_cash"] + raw["frozen_cash"],
            "missing_codes": [],
            "items": [],
        }

    quotes = _fetch_quotes(account_id, [item["code"] for item in positions])
    items = []
    missing_codes = []
    holdings_total = 0.0
    for position in positions:
        quote = quotes.get(position["code"], {})
        price = _positive_finite_or_none(quote.get("price"))
        market_value = None if price is None else round(price * position["quantity"], 2)
        if market_value is None:
            missing_codes.append(position["code"])
        else:
            holdings_total += market_value
        items.append(
            {
                "code": position["code"],
                "name": str(quote.get("name") or ""),
                "quantity": position["quantity"],
                "price": price,
                "market_value": market_value,
            }
        )

    total = None
    if not missing_codes:
        total = round(raw["available_cash"] + raw["frozen_cash"] + holdings_total, 2)
    return {
        "status": "unavailable" if missing_codes else "available",
        "total": total,
        "missing_codes": missing_codes,
        "items": items,
    }


def _valuation_needs_refresh(account: dict) -> bool:
    return (
        account["account_id"] in SECURITIES_ACCOUNTS
        and account["record_state"] == "recorded"
        and bool(account["raw_data"]["positions"])
    )


def _plan_valuation_changed(previous: dict, current: dict) -> bool:
    """Compare only derived values that are consumed by plan calculations."""
    return previous.get("total") != current.get("total")


def _refresh_current_valuation(account: dict) -> dict:
    """Fill derived display values without changing raw data or its version."""
    if not _valuation_needs_refresh(account):
        return account
    valuation = _derive_valuation(account["account_id"], account["raw_data"])
    existing_names = {
        item["code"]: item.get("name") or ""
        for item in account["valuation"].get("items", [])
    }
    valuation = {
        **valuation,
        "items": [
            {
                **item,
                "name": item.get("name") or existing_names.get(item["code"], ""),
            }
            for item in valuation["items"]
        ],
    }
    return {
        **account,
        "valuation": valuation,
        "readiness": (
            "ready" if valuation["status"] == "available" else "waiting_for_valuation"
        ),
    }


def _fetch_quotes(account_id: str, codes: list[str]) -> dict[str, dict]:
    try:
        if account_id == "cb":
            from datasource.market import fetch_cb_quotes_tencent

            return fetch_cb_quotes_tencent(codes)

        from datasource.market import fetch_tencent_snapshot

        snapshot = fetch_tencent_snapshot(codes)
        if snapshot.empty:
            return {}
        return {
            str(row["stock_code"]).zfill(6): {
                "name": row.get("stock_name_q") or "",
                "price": row.get("price"),
            }
            for _, row in snapshot.iterrows()
        }
    except Exception:
        return {}


def _positive_finite_or_none(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _current_account(conn: sqlite3.Connection, account_id: str) -> dict:
    version_row = conn.execute(
        """SELECT * FROM account_state_versions
           WHERE account_id=? AND operation!='backfill'
           ORDER BY as_of DESC, id DESC LIMIT 1""",
        (account_id,),
    ).fetchone()
    if version_row is not None:
        row = dict(version_row)
        return _public_state(
            account_id,
            raw=json.loads(row["raw_json"]),
            valuation=json.loads(row["valuation_json"]) if row["valuation_json"] else None,
            as_of=row["as_of"],
            updated_at=row["created_at"],
            version=_opaque_version("current", row["id"], account_id, row["created_at"]),
            operation=row["operation"],
        )

    account_row = conn.execute(
        """SELECT * FROM account_value_snapshots
           WHERE account_id=? ORDER BY snapshot_date DESC, id DESC LIMIT 1""",
        (account_id,),
    ).fetchone()
    latest_position = (
        position_store.get_latest_position_snapshot(conn, account_id)
        if account_id in SECURITIES_ACCOUNTS
        else None
    )
    latest_date = max(
        [
            item
            for item in (
                dict(account_row).get("snapshot_date") if account_row is not None else None,
                (latest_position or {}).get("position_date"),
            )
            if item
        ],
        default=None,
    )
    if latest_date is None:
        return _missing_state(account_id)

    if account_row is None or account_row["snapshot_date"] != latest_date:
        account_row = conn.execute(
            """SELECT * FROM account_value_snapshots
               WHERE account_id=? AND snapshot_date=? ORDER BY id DESC LIMIT 1""",
            (account_id, latest_date),
        ).fetchone()
    position = (
        position_store.get_position_snapshot_by_date(conn, account_id, latest_date)
        if account_id in SECURITIES_ACCOUNTS
        else None
    )

    value = dict(account_row) if account_row is not None else {}
    if account_id in SECURITIES_ACCOUNTS:
        cash = value.get("cash")
        frozen = value.get("frozen_cash") or 0
        raw = {
            "available_cash": None if cash is None else cash - frozen,
            "frozen_cash": None if cash is None else frozen,
            "positions": [
                {"code": item["code"], "quantity": item["shares"]}
                for item in ((position or {}).get("items") or [])
                if item["shares"] > 0
            ],
        }
        valuation = {
            "status": "available" if value.get("total") is not None else "unavailable",
            "total": value.get("total"),
            "missing_codes": [],
            "items": [
                {
                    "code": item["code"],
                    "name": item.get("name") or "",
                    "quantity": item["shares"],
                    "price": None,
                    "market_value": None,
                }
                for item in ((position or {}).get("items") or [])
                if item["shares"] > 0
            ],
        }
    else:
        raw = {"amount": value.get("total")}
        valuation = {
            "status": "available" if value.get("total") is not None else "unavailable",
            "total": value.get("total"),
            "missing_codes": [],
            "items": [],
        }

    timestamps = [item for item in (value.get("created_at"), (position or {}).get("created_at")) if item]
    complete_record = account_row is not None and (
        value.get("total") is not None
        if account_id not in SECURITIES_ACCOUNTS
        else position is not None and value.get("cash") is not None
    )
    return _public_state(
        account_id,
        raw=raw,
        valuation=valuation,
        as_of=latest_date,
        updated_at=max(timestamps) if timestamps else None,
        version=_opaque_version(
            "legacy",
            value.get("id"),
            (position or {}).get("id"),
            account_id,
        ),
        operation="migrated",
        recorded=complete_record,
        holding_recorded=position is not None,
    )


def _missing_state(account_id: str) -> dict:
    raw = (
        {"available_cash": None, "frozen_cash": None, "positions": []}
        if account_id in SECURITIES_ACCOUNTS
        else {"amount": None}
    )
    return _public_state(
        account_id,
        raw=raw,
        valuation={"status": "unavailable", "total": None, "missing_codes": [], "items": []},
        as_of=None,
        updated_at=None,
        version=None,
        operation=None,
    )


def _public_state(
    account_id: str,
    *,
    raw: dict,
    valuation: dict | None,
    as_of: str | None,
    updated_at: str | None,
    version: str | None,
    operation: str | None,
    recorded: bool | None = None,
    holding_recorded: bool | None = None,
) -> dict:
    definition = account_definition(account_id)
    recorded = version is not None if recorded is None else recorded
    valuation = valuation or {
        "status": "unavailable",
        "total": None,
        "missing_codes": [],
        "items": [],
    }
    if account_id in SECURITIES_ACCOUNTS:
        holding_recorded = recorded if holding_recorded is None else holding_recorded
        if not holding_recorded:
            holding_state = "missing"
        elif raw["positions"]:
            holding_state = "recorded"
        else:
            holding_state = "confirmed_empty"
    else:
        holding_state = None
    if not recorded:
        readiness = "needs_update"
    elif valuation["status"] != "available":
        readiness = "waiting_for_valuation"
    else:
        readiness = "ready"
    return {
        "account_id": account_id,
        "label": definition["label"],
        "sub": definition["sub"],
        "kind": "securities" if account_id in SECURITIES_ACCOUNTS else "balance",
        "record_state": "recorded" if recorded else "missing",
        "holding_state": holding_state,
        "as_of": as_of,
        "updated_at": updated_at,
        "version": version,
        "operation": operation,
        "raw_data": raw,
        "valuation": valuation,
        "readiness": readiness,
    }


def _check_version(current: dict, expected_version: str | None) -> None:
    if current["version"] != expected_version:
        raise AccountVersionConflict("账户数据已在别处更新，请刷新后合并你的修改")


def _insert_version(
    conn: sqlite3.Connection,
    account_id: str,
    raw: dict,
    valuation: dict,
    *,
    operation: str,
    as_of: str | None = None,
) -> int:
    now = datetime.now(LOCAL_TZ)
    as_of = as_of or now.date().isoformat()
    created_at = now.isoformat(timespec="seconds")
    position_snapshot_id = None
    account_snapshot_id = None
    if operation != "backfill":
        cash = None
        frozen_cash = 0.0
        if account_id in SECURITIES_ACCOUNTS:
            names = {
                item["code"]: item.get("name") or ""
                for item in valuation.get("items", [])
            }
            position_snapshot_id = position_store.insert_position_snapshot(
                conn,
                account_id,
                as_of,
                [
                    {
                        "code": item["code"],
                        "name": names.get(item["code"], ""),
                        "shares": item["quantity"],
                    }
                    for item in raw["positions"]
                ],
            )
            cash = raw["available_cash"] + raw["frozen_cash"]
            frozen_cash = raw["frozen_cash"]
            total = valuation.get("total")
        else:
            total = raw["amount"]
        account_snapshot_id = account_store.insert_account_value_snapshot_row(
            conn,
            account_id,
            as_of,
            total,
            cash,
            frozen_cash,
        )
    cursor = conn.execute(
        """INSERT INTO account_state_versions
           (account_id,as_of,raw_json,valuation_json,account_snapshot_id,
            position_snapshot_id,operation,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            account_id,
            as_of,
            _canonical_json(raw),
            _canonical_json(valuation),
            account_snapshot_id,
            position_snapshot_id,
            operation,
            created_at,
        ),
    )
    return cursor.lastrowid


def _invalidate_derived_plans(conn: sqlite3.Connection) -> None:
    strategy_store.clear_latest_orders(conn)
    reason = {
        "code": "PLAN_INPUTS_CHANGED",
        "message": "账户数据已更新，既有计划需要重新生成。",
    }
    conn.execute(
        """UPDATE generated_plans SET status='stale', error_json=?
           WHERE status IN ('running', 'complete')""",
        (_canonical_json(reason),),
    )


def _invalidate_plans_with_different_valuation(summary: dict) -> None:
    """Stale plans whose captured securities totals differ from current quotes."""
    total_fields = ("stock_total", "bond_total")
    with db._conn() as conn:
        with _write_transaction(conn):
            rows = conn.execute(
                """SELECT plan_id, plan_json FROM generated_plans
                   WHERE status IN ('running', 'complete')"""
            ).fetchall()
            stale_plan_ids = []
            for row in rows:
                try:
                    plan = json.loads(row["plan_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                captured = plan.get("account")
                if not isinstance(captured, dict):
                    continue
                if any(captured.get(field) != summary.get(field) for field in total_fields):
                    stale_plan_ids.append(row["plan_id"])
            if not stale_plan_ids:
                return
            strategy_store.clear_latest_orders(conn)
            reason = _canonical_json({
                "code": "PLAN_INPUTS_CHANGED",
                "message": "账户估值已更新，既有计划需要重新生成。",
            })
            conn.executemany(
                "UPDATE generated_plans SET status='stale', error_json=? WHERE plan_id=?",
                [(reason, plan_id) for plan_id in stale_plan_ids],
            )


@contextmanager
def _write_transaction(conn: sqlite3.Connection):
    if conn.in_transaction:
        name = "account_current_state"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
            conn.execute(f"RELEASE SAVEPOINT {name}")
            raise
        conn.execute(f"RELEASE SAVEPOINT {name}")
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _opaque_version(*parts) -> str:
    payload = ":".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
