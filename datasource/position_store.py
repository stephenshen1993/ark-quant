"""Position snapshot persistence helpers."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from datasource.account_store import validate_iso_date, validate_nonnegative_finite
from datasource.strategy_store import validate_strategy


def normalize_security_code(value: object) -> str:
    code = str(value)
    if not code.isascii() or not code.isdigit() or len(code) > 6:
        raise ValueError("security code must contain at most six ASCII digits")
    return code.zfill(6)


def normalize_position_rows(rows: list[dict]) -> list[dict]:
    normalized = []
    seen_codes = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("positions must be objects")
        if "code" not in row or "shares" not in row:
            raise ValueError("each position requires code and shares")
        code = normalize_security_code(row["code"])
        if code in seen_codes:
            raise ValueError(f"duplicate security code: {code}")
        seen_codes.add(code)
        normalized.append({
            "code": code,
            "name": str(row.get("name") or ""),
            "shares": validate_nonnegative_finite(row["shares"], "shares"),
        })
    return normalized


def insert_position_snapshot(
    conn: sqlite3.Connection,
    strategy: str,
    position_date: str,
    rows: list[dict],
    *,
    legacy: bool = False,
) -> int:
    cur = conn.execute(
        """INSERT INTO position_snapshots
           (strategy,position_date,created_at,legacy) VALUES (?,?,?,?)""",
        (strategy, position_date, datetime.now().isoformat(), int(legacy)),
    )
    snapshot_id = cur.lastrowid
    conn.executemany(
        """INSERT INTO position_snapshot_items
           (snapshot_id,code,name,shares) VALUES (?,?,?,?)""",
        [(snapshot_id, row["code"], row["name"], row["shares"]) for row in rows],
    )
    return snapshot_id


def append_position_snapshot(
    conn: sqlite3.Connection,
    strategy: str,
    position_date: str,
    rows: list[dict],
) -> dict:
    validate_strategy(strategy)
    validate_iso_date(position_date, "position_date")
    normalized = normalize_position_rows(rows)
    conn.execute("SAVEPOINT append_position_snapshot")
    try:
        snapshot_id = insert_position_snapshot(conn, strategy, position_date, normalized)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT append_position_snapshot")
        conn.execute("RELEASE SAVEPOINT append_position_snapshot")
        raise
    conn.execute("RELEASE SAVEPOINT append_position_snapshot")
    return get_position_snapshot(conn, "s.id=?", (snapshot_id,))


def get_position_snapshot(
    conn: sqlite3.Connection,
    where: str,
    params: tuple,
    *,
    order_by: str = "s.id DESC",
) -> dict | None:
    row = conn.execute(
        f"""SELECT s.id, s.strategy, s.position_date, s.created_at, s.legacy
            FROM position_snapshots s WHERE {where}
            ORDER BY {order_by} LIMIT 1""",
        params,
    ).fetchone()
    if row is None:
        return None
    snapshot = dict(row)
    items = conn.execute(
        """SELECT code, name, shares FROM position_snapshot_items
           WHERE snapshot_id=? ORDER BY code""",
        (snapshot["id"],),
    ).fetchall()
    snapshot["items"] = [dict(item) for item in items]
    return snapshot


def get_position_snapshot_by_date(
    conn: sqlite3.Connection,
    strategy: str,
    date_str: str,
) -> dict | None:
    validate_strategy(strategy)
    validate_iso_date(date_str, "position_date")
    return get_position_snapshot(
        conn,
        "s.strategy=? AND s.position_date=?",
        (strategy, date_str),
    )


def get_latest_position_snapshot(conn: sqlite3.Connection, strategy: str) -> dict | None:
    validate_strategy(strategy)
    return get_position_snapshot(
        conn,
        "s.strategy=?",
        (strategy,),
        order_by="s.position_date DESC, s.id DESC",
    )


def get_position_snapshot_asof(
    conn: sqlite3.Connection,
    strategy: str,
    date_str: str,
) -> dict | None:
    validate_strategy(strategy)
    validate_iso_date(date_str, "position_date")
    return get_position_snapshot(
        conn,
        "s.strategy=? AND s.position_date<=?",
        (strategy, date_str),
        order_by="s.position_date DESC, s.id DESC",
    )


def get_position_snapshot_between(
    conn: sqlite3.Connection,
    strategy: str,
    start_date: str,
    end_date: str,
) -> dict | None:
    validate_strategy(strategy)
    validate_iso_date(start_date, "start_date")
    validate_iso_date(end_date, "end_date")
    return get_position_snapshot(
        conn,
        "s.strategy=? AND s.position_date>=? AND s.position_date<=?",
        (strategy, start_date, end_date),
        order_by="s.position_date DESC, s.id DESC",
    )


def get_position_snapshot_updated_between(
    conn: sqlite3.Connection,
    strategy: str,
    start_date: str,
    end_date: str,
) -> dict | None:
    validate_strategy(strategy)
    validate_iso_date(start_date, "start_date")
    validate_iso_date(end_date, "end_date")
    return get_position_snapshot(
        conn,
        "s.strategy=? AND date(s.created_at)>=date(?) AND date(s.created_at)<=date(?)",
        (strategy, start_date, end_date),
        order_by="s.created_at DESC, s.id DESC",
    )


def snapshot_items_for_compatibility(snapshot: dict | None) -> list[dict]:
    if snapshot is None:
        return []
    return [
        {**item, "position_date": snapshot["position_date"]}
        for item in snapshot["items"]
    ]


def get_latest_positions(conn: sqlite3.Connection, strategy: str) -> list[dict]:
    return snapshot_items_for_compatibility(get_latest_position_snapshot(conn, strategy))


def get_position_dates(conn: sqlite3.Connection, strategy: str) -> list[str]:
    validate_strategy(strategy)
    rows = conn.execute(
        """SELECT DISTINCT position_date FROM position_snapshots
           WHERE strategy=? ORDER BY position_date DESC""",
        (strategy,),
    ).fetchall()
    return [r["position_date"] for r in rows]


def get_positions_by_date(
    conn: sqlite3.Connection,
    strategy: str,
    date_str: str,
) -> list[dict]:
    return snapshot_items_for_compatibility(
        get_position_snapshot_by_date(conn, strategy, date_str)
    )


def get_positions_asof(
    conn: sqlite3.Connection,
    strategy: str,
    date_str: str,
) -> list[dict]:
    return snapshot_items_for_compatibility(
        get_position_snapshot_asof(conn, strategy, date_str)
    )


def get_positions_updated_between(
    conn: sqlite3.Connection,
    strategy: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    return snapshot_items_for_compatibility(
        get_position_snapshot_updated_between(conn, strategy, start_date, end_date)
    )
