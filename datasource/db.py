"""
datasource/db.py — SQLite 持久化层

所有写入和查询都通过这个模块。测试通过 _TEST_CONN 注入内存库。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from datasource import account_store
from datasource import position_store
from datasource import strategy_store

DB_PATH = Path(
    os.environ.get(
        "ARK_QUANT_DB_PATH",
        Path(__file__).resolve().parents[1] / "data" / "ark_quant.db",
    )
)

_TEST_CONN: sqlite3.Connection | None = None  # 测试注入点

ACCOUNT_VALUE_FIELDS = account_store.ACCOUNT_VALUE_FIELDS
ACCOUNT_METADATA = account_store.ACCOUNT_METADATA


def get_connection() -> sqlite3.Connection:
    """Legacy getter for backward compatibility (used in test_db.py)."""
    if _TEST_CONN is not None:
        return _TEST_CONN
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _conn():
    """Context manager: closes connection after use in production; reuses _TEST_CONN in tests."""
    if _TEST_CONN is not None:
        yield _TEST_CONN
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.executescript("""
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS strategy_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy    TEXT    NOT NULL,
            data_date   TEXT    NOT NULL,
            trade_date  TEXT,
            created_at  TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'complete'))
        );

        CREATE TABLE IF NOT EXISTS cb_rankings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       INTEGER NOT NULL REFERENCES strategy_runs(id),
            rank         INTEGER NOT NULL,
            bond_code    TEXT    NOT NULL,
            bond_name    TEXT,
            cb_price     REAL,
            premium_rate REAL,
            double_low   REAL,
            score        REAL
        );

        CREATE TABLE IF NOT EXISTS stock_rankings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id     INTEGER NOT NULL REFERENCES strategy_runs(id),
            rank       INTEGER NOT NULL,
            stock_code TEXT    NOT NULL,
            stock_name TEXT,
            market_cap REAL,
            pe_ttm     REAL,
            roe_ex     REAL
        );

        CREATE TABLE IF NOT EXISTS cb_orders (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id    INTEGER NOT NULL REFERENCES strategy_runs(id),
            action    TEXT    NOT NULL,
            bond_code TEXT    NOT NULL,
            bond_name TEXT,
            price     REAL,
            shares    INTEGER,
            amount    REAL
        );

        CREATE TABLE IF NOT EXISTS stock_orders (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id     INTEGER NOT NULL REFERENCES strategy_runs(id),
            action     TEXT    NOT NULL,
            stock_code TEXT    NOT NULL,
            stock_name TEXT,
            price      REAL,
            shares     INTEGER,
            amount     REAL
        );

        CREATE TABLE IF NOT EXISTS account_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date   TEXT    NOT NULL,
            temperature     REAL,
            stock_total     REAL,
            stock_cash      REAL,
            bond_total      REAL,
            bond_cash       REAL,
            changqian_total REAL,
            cash_pool       REAL,
            overseas_total  REAL,
            changqian_updated_at TEXT,
            cash_pool_updated_at TEXT,
            overseas_updated_at  TEXT,
            created_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS account_value_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id    TEXT    NOT NULL,
            snapshot_date TEXT    NOT NULL,
            total         REAL    NOT NULL,
            cash          REAL,
            frozen_cash   REAL,
            created_at    TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS account_contexts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT    NOT NULL,
            temperature   REAL,
            check_type    TEXT    NOT NULL DEFAULT 'a_internal',
            new_contribution REAL NOT NULL DEFAULT 0,
            b_purchase_status TEXT NOT NULL DEFAULT 'unchecked',
            b_purchase_limit REAL NOT NULL DEFAULT 0,
            b_purchase_checked_at TEXT,
            b_purchase_source TEXT,
            created_at    TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cb_positions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            position_date TEXT    NOT NULL,
            bond_code     TEXT    NOT NULL,
            bond_name     TEXT,
            shares        INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stock_positions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            position_date TEXT    NOT NULL,
            stock_code    TEXT    NOT NULL,
            stock_name    TEXT,
            shares        INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS position_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy      TEXT    NOT NULL CHECK(strategy IN ('cb', 'stock')),
            position_date TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            legacy        INTEGER NOT NULL DEFAULT 0 CHECK(legacy IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS position_snapshot_items (
            snapshot_id INTEGER NOT NULL REFERENCES position_snapshots(id) ON DELETE CASCADE,
            code        TEXT    NOT NULL CHECK(length(code) = 6 AND code NOT GLOB '*[^0-9]*'),
            name        TEXT    NOT NULL DEFAULT '',
            shares      REAL    NOT NULL CHECK(shares >= 0),
            UNIQUE(snapshot_id, code)
        );

        CREATE INDEX IF NOT EXISTS idx_position_snapshots_strategy_date
        ON position_snapshots(strategy, position_date DESC, id DESC);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_position_snapshots_legacy_date
        ON position_snapshots(strategy, position_date) WHERE legacy = 1;

        CREATE INDEX IF NOT EXISTS idx_position_snapshot_items_snapshot
        ON position_snapshot_items(snapshot_id, code);

        CREATE TABLE IF NOT EXISTS raw_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT    NOT NULL,
            name          TEXT    NOT NULL,
            subdir        TEXT,
            data_json     TEXT    NOT NULL,
            created_at    TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS market_temperatures (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            temperature       REAL    NOT NULL,
            label             TEXT,
            source_updated_at TEXT    NOT NULL,
            source            TEXT    NOT NULL,
            fetched_at        TEXT    NOT NULL,
            UNIQUE(source, source_updated_at)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_market_temperatures_source_time
        ON market_temperatures(source, source_updated_at);

        CREATE TABLE IF NOT EXISTS market_temperature_refresh_state (
            source          TEXT PRIMARY KEY,
            last_attempt_at TEXT NOT NULL,
            last_error      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS generated_plans (
            plan_id    TEXT PRIMARY KEY,
            plan_date  TEXT NOT NULL,
            status     TEXT NOT NULL,
            plan_json  TEXT NOT NULL,
            error_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS plan_order_batches (
            plan_id      TEXT NOT NULL REFERENCES generated_plans(plan_id) ON DELETE CASCADE,
            strategy     TEXT NOT NULL CHECK(strategy IN ('cb', 'stock')),
            orders_json  TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            PRIMARY KEY(plan_id, strategy)
        );
        """)

        cols = {r["name"] for r in conn.execute("PRAGMA table_info(account_snapshots)").fetchall()}
        for col in ("changqian_updated_at", "cash_pool_updated_at", "overseas_updated_at"):
            if col not in cols:
                conn.execute(f"ALTER TABLE account_snapshots ADD COLUMN {col} TEXT")
        value_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(account_value_snapshots)").fetchall()
        }
        if "frozen_cash" not in value_cols:
            conn.execute("ALTER TABLE account_value_snapshots ADD COLUMN frozen_cash REAL")
            conn.execute("UPDATE account_value_snapshots SET frozen_cash=0 WHERE frozen_cash IS NULL")
        context_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(account_contexts)").fetchall()
        }
        for column, definition in (
            ("check_type", "TEXT NOT NULL DEFAULT 'a_internal'"),
            ("new_contribution", "REAL NOT NULL DEFAULT 0"),
            ("b_purchase_status", "TEXT NOT NULL DEFAULT 'unchecked'"),
            ("b_purchase_limit", "REAL NOT NULL DEFAULT 0"),
            ("b_purchase_checked_at", "TEXT"),
            ("b_purchase_source", "TEXT"),
        ):
            if column not in context_columns:
                conn.execute(f"ALTER TABLE account_contexts ADD COLUMN {column} {definition}")
        conn.execute(
            """UPDATE account_contexts
               SET b_purchase_status = CASE
                   WHEN b_purchase_limit > 0 THEN 'available'
                   WHEN b_purchase_checked_at IS NOT NULL OR b_purchase_source IS NOT NULL THEN 'unavailable'
                   ELSE 'unchecked'
               END
               WHERE b_purchase_status IS NULL OR b_purchase_status = 'unchecked'"""
        )
        generated_plan_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(generated_plans)").fetchall()
        }
        if "error_json" not in generated_plan_columns:
            conn.execute("ALTER TABLE generated_plans ADD COLUMN error_json TEXT")
        _migrate_strategy_run_status(conn)
        _migrate_legacy_account_snapshots(conn)
        _migrate_legacy_positions(conn)


def _migrate_strategy_run_status(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(strategy_runs)").fetchall()
    }
    if "status" in columns:
        return

    conn.execute(
        "ALTER TABLE strategy_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
    )
    conn.execute("""
        UPDATE strategy_runs
        SET status = CASE
            WHEN strategy = 'cb' AND EXISTS (
                SELECT 1 FROM cb_rankings r WHERE r.run_id = strategy_runs.id
            ) THEN 'complete'
            WHEN strategy = 'stock' AND EXISTS (
                SELECT 1 FROM stock_rankings r WHERE r.run_id = strategy_runs.id
            ) THEN 'complete'
            ELSE 'pending'
        END
    """)


def _migrate_legacy_account_snapshots(conn: sqlite3.Connection) -> None:
    """Move old wide account snapshots into the account-level snapshot model once."""
    legacy_count = conn.execute("SELECT COUNT(*) AS c FROM account_snapshots").fetchone()["c"]
    context_count = conn.execute("SELECT COUNT(*) AS c FROM account_contexts").fetchone()["c"]
    value_count = conn.execute("SELECT COUNT(*) AS c FROM account_value_snapshots").fetchone()["c"]
    if not legacy_count or (context_count and value_count):
        return

    rows = conn.execute("SELECT * FROM account_snapshots ORDER BY id").fetchall()
    for row in rows:
        item = dict(row)
        created_at = item.get("created_at") or datetime.now().isoformat()
        if not context_count:
            conn.execute(
                "INSERT INTO account_contexts (snapshot_date,temperature,created_at) VALUES (?,?,?)",
                (item["snapshot_date"], item.get("temperature"), created_at),
            )
        if not value_count:
            for account_id, (total_key, cash_key) in ACCOUNT_VALUE_FIELDS.items():
                conn.execute(
                    """INSERT INTO account_value_snapshots
                       (account_id,snapshot_date,total,cash,created_at)
                       VALUES (?,?,?,?,?)""",
                    (
                        account_id,
                        item["snapshot_date"],
                        item.get(total_key) or 0,
                        item.get(cash_key) if cash_key else None,
                        created_at,
                    ),
                )


def _migrate_legacy_positions(conn: sqlite3.Connection) -> None:
    """Import each legacy strategy/date once while retaining the old tables."""
    configs = {
        "cb": ("cb_positions", "bond_code", "bond_name"),
        "stock": ("stock_positions", "stock_code", "stock_name"),
    }
    for strategy, (table, code_col, name_col) in configs.items():
        dates = conn.execute(
            f"SELECT DISTINCT position_date FROM {table} ORDER BY position_date"
        ).fetchall()
        for row in dates:
            position_date = row["position_date"]
            _validate_iso_date(position_date, "position_date")
            existing = conn.execute(
                """SELECT id FROM position_snapshots
                   WHERE strategy=? AND position_date=? AND legacy=1""",
                (strategy, position_date),
            ).fetchone()
            if existing is not None:
                continue

            legacy_rows = conn.execute(
                f"""SELECT id, {code_col} AS code, {name_col} AS name, shares
                    FROM {table} WHERE position_date=? ORDER BY id""",
                (position_date,),
            ).fetchall()
            latest_by_code = {}
            for item in legacy_rows:
                code = _normalize_security_code(item["code"])
                latest_by_code[code] = {
                    "id": item["id"],
                    "code": code,
                    "name": item["name"] or "",
                    "shares": _validate_nonnegative_finite(item["shares"], "shares"),
                }

            cur = conn.execute(
                """INSERT OR IGNORE INTO position_snapshots
                   (strategy,position_date,created_at,legacy) VALUES (?,?,?,1)""",
                (strategy, position_date, datetime.now().isoformat()),
            )
            if cur.rowcount != 1:
                continue
            conn.executemany(
                """INSERT INTO position_snapshot_items
                   (snapshot_id,code,name,shares) VALUES (?,?,?,?)""",
                [
                    (
                        cur.lastrowid,
                        item["code"],
                        item["name"],
                        item["shares"],
                    )
                    for item in sorted(latest_by_code.values(), key=lambda value: value["id"])
                ],
            )


def _validate_iso_date(value: str, field: str = "snapshot_date") -> str:
    return account_store.validate_iso_date(value, field)


def _validate_iso_datetime(value: str, field: str) -> str:
    return account_store.validate_iso_datetime(value, field)


def _validate_nonnegative_finite(value: float, field: str) -> float:
    return account_store.validate_nonnegative_finite(value, field)


def _validate_strategy(strategy: str) -> str:
    if strategy not in {"cb", "stock"}:
        raise ValueError(f"Unknown strategy: {strategy}")
    return strategy


def _validate_account_id(account_id: str) -> str:
    return account_store.validate_account_id(account_id)


def _normalize_security_code(value: object) -> str:
    return position_store.normalize_security_code(value)


def _normalize_position_rows(rows: list[dict]) -> list[dict]:
    return position_store.normalize_position_rows(rows)


# ── Write ─────────────────────────────────────────────────────────────────────

def insert_strategy_run(strategy: str, data_date: date) -> int:
    with _conn() as conn:
        return strategy_store.insert_strategy_run(conn, strategy, data_date)


def insert_cb_rankings(run_id: int, df: pd.DataFrame) -> None:
    with _conn() as conn:
        strategy_store.insert_rankings(conn, "cb", run_id, df)


def insert_stock_rankings(run_id: int, df: pd.DataFrame) -> None:
    with _conn() as conn:
        strategy_store.insert_rankings(conn, "stock", run_id, df)


def create_complete_strategy_run(
    strategy: str,
    data_date: date,
    trade_date: date | None,
    rankings: pd.DataFrame,
) -> int:
    with _conn() as conn:
        return strategy_store.create_complete_strategy_run(
            conn, strategy, data_date, trade_date, rankings
        )


def insert_cb_orders(run_id: int, df: pd.DataFrame) -> None:
    with _conn() as conn:
        strategy_store.insert_cb_orders(conn, run_id, df)


def insert_stock_orders(run_id: int, df: pd.DataFrame) -> None:
    with _conn() as conn:
        strategy_store.insert_stock_orders(conn, run_id, df)


def insert_account_snapshot(
    snapshot_date: str, temperature: float,
    stock_total: float, stock_cash: float,
    bond_total: float, bond_cash: float,
    changqian_total: float, cash_pool: float, overseas_total: float,
    changqian_updated_at: str | None = None,
    cash_pool_updated_at: str | None = None,
    overseas_updated_at: str | None = None,
) -> int:
    with _conn() as conn:
        return account_store.insert_account_snapshot(
            conn,
            snapshot_date,
            temperature,
            stock_total,
            stock_cash,
            bond_total,
            bond_cash,
            changqian_total,
            cash_pool,
            overseas_total,
            changqian_updated_at,
            cash_pool_updated_at,
            overseas_updated_at,
        )


def insert_account_value_snapshot(
    account_id: str,
    snapshot_date: str,
    total: float,
    cash: float | None = None,
    frozen_cash: float | None = None,
) -> int:
    with _conn() as conn:
        return account_store.insert_account_value_snapshot(
            conn, account_id, snapshot_date, total, cash, frozen_cash
        )


def _insert_account_value_snapshot(
    conn: sqlite3.Connection,
    account_id: str,
    snapshot_date: str,
    total: float,
    cash: float | None,
    frozen_cash: float,
) -> int:
    return account_store.insert_account_value_snapshot_row(
        conn, account_id, snapshot_date, total, cash, frozen_cash
    )


def insert_account_context(
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
    with _conn() as conn:
        return account_store.insert_account_context(
            conn,
            snapshot_date,
            temperature,
            check_type=check_type,
            new_contribution=new_contribution,
            b_purchase_status=b_purchase_status,
            b_purchase_limit=b_purchase_limit,
            b_purchase_checked_at=b_purchase_checked_at,
            b_purchase_source=b_purchase_source,
        )


def insert_market_temperature(
    temperature: float,
    label: str | None,
    source_updated_at: str,
    source: str,
    fetched_at: str | None = None,
) -> dict:
    try:
        temperature = float(temperature)
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature must be numeric") from exc
    if not math.isfinite(temperature) or not 0 <= temperature <= 100:
        raise ValueError("temperature must be between 0 and 100")
    try:
        datetime.fromisoformat(source_updated_at)
        if fetched_at is not None:
            datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature timestamps must be valid ISO values") from exc
    fetched_at = fetched_at or (
        datetime.now(ZoneInfo("Asia/Shanghai"))
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )
    with _conn() as conn:
        conn.execute(
            """INSERT INTO market_temperatures
               (temperature,label,source_updated_at,source,fetched_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(source, source_updated_at) DO UPDATE SET
                   fetched_at=excluded.fetched_at
               WHERE market_temperatures.temperature=excluded.temperature
                 AND market_temperatures.label IS excluded.label""",
            (temperature, label, source_updated_at, source, fetched_at),
        )
        row = conn.execute(
            "SELECT * FROM market_temperatures WHERE source=? AND source_updated_at=?",
            (source, source_updated_at),
        ).fetchone()
        return dict(row)


def record_market_temperature_refresh_failure(
    source: str,
    last_attempt_at: str,
    last_error: str,
) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO market_temperature_refresh_state
               (source,last_attempt_at,last_error) VALUES (?,?,?)
               ON CONFLICT(source) DO UPDATE SET
                   last_attempt_at=excluded.last_attempt_at,
                   last_error=excluded.last_error""",
            (source, last_attempt_at, last_error),
        )


def clear_market_temperature_refresh_state(source: str) -> None:
    with _conn() as conn:
        conn.execute(
            "DELETE FROM market_temperature_refresh_state WHERE source=?",
            (source,),
        )


def _insert_position_snapshot(
    conn: sqlite3.Connection,
    strategy: str,
    position_date: str,
    rows: list[dict],
    *,
    legacy: bool = False,
) -> int:
    return position_store.insert_position_snapshot(
        conn, strategy, position_date, rows, legacy=legacy
    )


def append_position_snapshot(strategy: str, position_date: str, rows: list[dict]) -> dict:
    with _conn() as conn:
        return position_store.append_position_snapshot(conn, strategy, position_date, rows)


def append_account_state_snapshot(
    account_id: str,
    snapshot_date: str,
    total: float,
    cash: float,
    positions: list[dict],
    frozen_cash: float | None = None,
) -> dict:
    _validate_account_id(account_id)
    _validate_strategy(account_id)
    _validate_iso_date(snapshot_date)
    total = _validate_nonnegative_finite(total, "total")
    cash = _validate_nonnegative_finite(cash, "cash")
    if frozen_cash is None:
        frozen_cash = 0.0
    frozen_cash = _validate_nonnegative_finite(frozen_cash, "frozen_cash")
    if frozen_cash > cash:
        raise ValueError("frozen_cash must be less than or equal to cash")
    normalized = _normalize_position_rows(positions)

    with _conn() as conn:
        conn.execute("SAVEPOINT append_account_state_snapshot")
        try:
            # The position snapshot is the account's source of truth.  Persist it
            # first, then save the cash and total that were reconciled against it.
            position_snapshot_id = position_store.insert_position_snapshot(
                conn, account_id, snapshot_date, normalized
            )
            account_snapshot_id = _insert_account_value_snapshot(
                conn, account_id, snapshot_date, total, cash, frozen_cash
            )
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT append_account_state_snapshot")
            conn.execute("RELEASE SAVEPOINT append_account_state_snapshot")
            raise
        conn.execute("RELEASE SAVEPOINT append_account_state_snapshot")
        return {
            "account_snapshot_id": account_snapshot_id,
            "position_snapshot": position_store.get_position_snapshot(
                conn, "s.id=?", (position_snapshot_id,)
            ),
        }


def insert_positions(strategy: str, position_date: str, rows: list[dict]) -> None:
    """Compatibility writer; new storage is immutable and append-only."""
    append_position_snapshot(strategy, position_date, rows)


# ── Read ──────────────────────────────────────────────────────────────────────

def get_latest_run_id(strategy: str) -> int | None:
    with _conn() as conn:
        return strategy_store.get_latest_run_id(conn, strategy)


def get_latest_strategy_run(strategy: str) -> dict | None:
    with _conn() as conn:
        return strategy_store.get_latest_strategy_run(conn, strategy)


def get_strategy_run(run_id: int, strategy: str) -> dict | None:
    with _conn() as conn:
        return strategy_store.get_strategy_run(conn, run_id, strategy)


def get_current_account_summary() -> dict | None:
    with _conn() as conn:
        return account_store.get_current_account_summary(conn)


def get_account_summary(snapshot_date: str | None = None) -> dict | None:
    with _conn() as conn:
        return account_store.get_account_summary(conn, snapshot_date)


def _build_account_items(snap: dict) -> list[dict]:
    return account_store.build_account_items(snap)


def get_latest_account_snapshot() -> dict | None:
    """Compatibility alias for callers that still expect the old name."""
    return get_current_account_summary()


def get_latest_market_temperature(source: str | None = None) -> dict | None:
    with _conn() as conn:
        if source is None:
            row = conn.execute(
                "SELECT * FROM market_temperatures "
                "ORDER BY source_updated_at DESC, id DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM market_temperatures WHERE source=? "
                "ORDER BY source_updated_at DESC, id DESC LIMIT 1",
                (source,),
            ).fetchone()
        return dict(row) if row else None


def get_market_temperature_refresh_state(source: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM market_temperature_refresh_state WHERE source=?",
            (source,),
        ).fetchone()
        return dict(row) if row else None


def get_account_history() -> list[dict]:
    with _conn() as conn:
        return account_store.get_account_history(conn)


def _get_position_snapshot(
    conn: sqlite3.Connection,
    where: str,
    params: tuple,
    *,
    order_by: str = "s.id DESC",
) -> dict | None:
    return position_store.get_position_snapshot(
        conn, where, params, order_by=order_by
    )


def get_position_snapshot_by_date(strategy: str, date_str: str) -> dict | None:
    with _conn() as conn:
        return position_store.get_position_snapshot_by_date(conn, strategy, date_str)


def get_latest_position_snapshot(strategy: str) -> dict | None:
    with _conn() as conn:
        return position_store.get_latest_position_snapshot(conn, strategy)


def get_position_snapshot_asof(strategy: str, date_str: str) -> dict | None:
    with _conn() as conn:
        return position_store.get_position_snapshot_asof(conn, strategy, date_str)


def get_position_snapshot_between(
    strategy: str,
    start_date: str,
    end_date: str,
) -> dict | None:
    with _conn() as conn:
        return position_store.get_position_snapshot_between(
            conn, strategy, start_date, end_date
        )


def get_position_snapshot_updated_between(
    strategy: str,
    start_date: str,
    end_date: str,
) -> dict | None:
    with _conn() as conn:
        return position_store.get_position_snapshot_updated_between(
            conn, strategy, start_date, end_date
        )


def _snapshot_items_for_compatibility(snapshot: dict | None) -> list[dict]:
    return position_store.snapshot_items_for_compatibility(snapshot)


def get_latest_positions(strategy: str) -> list[dict]:
    with _conn() as conn:
        return position_store.get_latest_positions(conn, strategy)


def get_position_dates(strategy: str) -> list[str]:
    with _conn() as conn:
        return position_store.get_position_dates(conn, strategy)


def get_positions_by_date(strategy: str, date_str: str) -> list[dict]:
    with _conn() as conn:
        return position_store.get_positions_by_date(conn, strategy, date_str)


def get_positions_asof(strategy: str, date_str: str) -> list[dict]:
    with _conn() as conn:
        return position_store.get_positions_asof(conn, strategy, date_str)


def get_positions_updated_between(strategy: str, start_date: str, end_date: str) -> list[dict]:
    with _conn() as conn:
        return position_store.get_positions_updated_between(
            conn, strategy, start_date, end_date
        )


def get_ranking_dates(strategy: str) -> list[str]:
    with _conn() as conn:
        return strategy_store.get_ranking_dates(conn, strategy)


def get_strategy_run_meta(strategy: str, data_date: str) -> dict | None:
    with _conn() as conn:
        return strategy_store.get_strategy_run_meta(conn, strategy, data_date)


def get_rankings_by_run_id(strategy: str, run_id: int) -> list[dict]:
    with _conn() as conn:
        return strategy_store.get_rankings_by_run_id(conn, strategy, run_id)


def get_rankings(strategy: str, data_date: str) -> list[dict]:
    with _conn() as conn:
        return strategy_store.get_rankings(conn, strategy, data_date)


def get_orders(strategy: str, data_date: str) -> list[dict]:
    with _conn() as conn:
        return strategy_store.get_orders(conn, strategy, data_date)


def get_latest_rankings(strategy: str) -> list[dict]:
    with _conn() as conn:
        return strategy_store.get_latest_rankings(conn, strategy)


def get_latest_orders(strategy: str) -> list[dict]:
    with _conn() as conn:
        return strategy_store.get_latest_orders(conn, strategy)


def clear_latest_orders() -> None:
    with _conn() as conn:
        strategy_store.clear_latest_orders(conn)


def insert_generated_plan(plan_id: str, plan_date: str, status: str, plan: dict) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO generated_plans
               (plan_id, plan_date, status, plan_json, error_json, created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                plan_id,
                plan_date,
                status,
                _to_json(plan),
                None,
                datetime.now().isoformat(),
            ),
        )


def try_insert_running_generated_plan(plan_id: str, plan_date: str, plan: dict) -> bool:
    """Atomically reserve a plan date for one in-progress generation."""
    with _conn() as conn:
        cursor = conn.execute(
            """INSERT INTO generated_plans
               (plan_id, plan_date, status, plan_json, error_json, created_at)
               SELECT ?, ?, 'running', ?, NULL, ?
               WHERE NOT EXISTS (
                   SELECT 1 FROM generated_plans
                   WHERE plan_date=? AND status='running'
               )""",
            (
                plan_id,
                plan_date,
                _to_json(plan),
                datetime.now().isoformat(),
                plan_date,
            ),
        )
        return cursor.rowcount == 1


def update_generated_plan(
    plan_id: str,
    *,
    status: str,
    plan: dict | None = None,
    error: dict | None = None,
) -> None:
    with _conn() as conn:
        existing = conn.execute(
            "SELECT plan_json FROM generated_plans WHERE plan_id=?",
            (plan_id,),
        ).fetchone()
        if existing is None:
            raise ValueError(f"Unknown generated plan: {plan_id}")
        plan_json = _to_json(plan) if plan is not None else existing["plan_json"]
        conn.execute(
            """UPDATE generated_plans
               SET status=?, plan_json=?, error_json=?
               WHERE plan_id=?""",
            (status, plan_json, _to_json(error) if error else None, plan_id),
        )


def mark_generated_plans_stale(reason: dict | None = None) -> None:
    error = reason or {
        "code": "PLAN_INPUTS_CHANGED",
        "message": "账户事实已更新，既有计划需要重新生成。",
    }
    with _conn() as conn:
        conn.execute(
            "UPDATE generated_plans SET status='stale', error_json=? WHERE status='complete'",
            (_to_json(error),),
        )


def get_generated_plan(plan_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM generated_plans WHERE plan_id=?",
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        return {
            "plan_id": item["plan_id"],
            "plan_date": item["plan_date"],
            "status": item["status"],
            "plan": json.loads(item["plan_json"]),
            "error": json.loads(item["error_json"]) if item.get("error_json") else None,
            "created_at": item["created_at"],
        }


def get_latest_generated_plan(plan_date: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            """SELECT plan_id, plan_date, status, error_json, created_at
               FROM generated_plans
               WHERE plan_date=?
               ORDER BY created_at DESC, plan_id DESC
               LIMIT 1""",
            (plan_date,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        return {
            "plan_id": item["plan_id"],
            "plan_date": item["plan_date"],
            "status": item["status"],
            "error": json.loads(item["error_json"]) if item.get("error_json") else None,
            "created_at": item["created_at"],
        }


def get_running_generated_plan(plan_date: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            """SELECT plan_id, plan_date, status, created_at
               FROM generated_plans
               WHERE plan_date=? AND status='running'
               ORDER BY created_at DESC, plan_id DESC
               LIMIT 1""",
            (plan_date,),
        ).fetchone()
        return dict(row) if row is not None else None


def insert_plan_order_batch(plan_id: str, strategy: str, orders: list[dict], summary: dict) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO plan_order_batches
               (plan_id, strategy, orders_json, summary_json, created_at)
               VALUES (?,?,?,?,?)""",
            (
                plan_id,
                strategy,
                _to_json(orders),
                _to_json(summary),
                datetime.now().isoformat(),
            ),
        )


def get_plan_order_batch(plan_id: str, strategy: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM plan_order_batches WHERE plan_id=? AND strategy=?",
            (plan_id, strategy),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        return {
            "plan_id": item["plan_id"],
            "strategy": item["strategy"],
            "orders": json.loads(item["orders_json"]),
            "summary": json.loads(item["summary_json"]),
            "created_at": item["created_at"],
        }


def _to_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


def _json_default(value):
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


# ── 原始数据快照（本地优先缓存） ──────────────────────────────────────────────

def get_raw_snapshot(snapshot_date: str, name: str, subdir: str | None = None) -> str | None:
    """Return JSON data for a cached snapshot, or None."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT data_json FROM raw_snapshots WHERE snapshot_date=? AND name=? AND subdir IS ?",
            (snapshot_date, name, subdir),
        ).fetchone()
        return row["data_json"] if row else None


def save_raw_snapshot(snapshot_date: str, name: str, data_json: str, subdir: str | None = None) -> None:
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM raw_snapshots WHERE snapshot_date=? AND name=? AND subdir IS ?",
            (snapshot_date, name, subdir),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE raw_snapshots SET data_json=?, created_at=? WHERE id=?",
                (data_json, datetime.now().isoformat(), existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO raw_snapshots (snapshot_date, name, subdir, data_json, created_at) VALUES (?,?,?,?,?)",
                (snapshot_date, name, subdir, data_json, datetime.now().isoformat()),
            )
