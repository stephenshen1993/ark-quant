"""
datasource/db.py — SQLite 持久化层

所有写入和查询都通过这个模块。测试通过 _TEST_CONN 注入内存库。
"""
from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from investment_model import ACCOUNT_DEFINITIONS, account_definition

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "ark_quant.db"

_TEST_CONN: sqlite3.Connection | None = None  # 测试注入点

ACCOUNT_VALUE_FIELDS = {
    "stock": ("stock_total", "stock_cash"),
    "cb": ("bond_total", "bond_cash"),
    "changqian": ("changqian_total", None),
    "cash": ("cash_pool", None),
    "overseas": ("overseas_total", None),
}

# 这里仅保存历史 SQLite 字段的兼容映射。账户语义统一由 investment_model.py 定义。
ACCOUNT_METADATA = ACCOUNT_DEFINITIONS


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
            ("b_purchase_limit", "REAL NOT NULL DEFAULT 0"),
            ("b_purchase_checked_at", "TEXT"),
            ("b_purchase_source", "TEXT"),
        ):
            if column not in context_columns:
                conn.execute(f"ALTER TABLE account_contexts ADD COLUMN {column} {definition}")
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


def _next_weekday(d: date) -> date:
    """返回 d 之后的下一个工作日（跳过周六、周日）。"""
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _validate_iso_date(value: str, field: str = "snapshot_date") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be an ISO date")
    return value


def _validate_iso_datetime(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO datetime")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO datetime") from exc
    return value


def _validate_nonnegative_finite(value: float, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a nonnegative finite number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a nonnegative finite number")
    return number


def _validate_strategy(strategy: str) -> str:
    if strategy not in {"cb", "stock"}:
        raise ValueError(f"Unknown strategy: {strategy}")
    return strategy


def _validate_account_id(account_id: str) -> str:
    if account_id not in ACCOUNT_VALUE_FIELDS:
        raise ValueError(f"Unknown account: {account_id}")
    return account_id


def _normalize_security_code(value: object) -> str:
    code = str(value)
    if not code.isascii() or not code.isdigit() or len(code) > 6:
        raise ValueError("security code must contain at most six ASCII digits")
    return code.zfill(6)


def _normalize_position_rows(rows: list[dict]) -> list[dict]:
    normalized = []
    seen_codes = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("positions must be objects")
        if "code" not in row or "shares" not in row:
            raise ValueError("each position requires code and shares")
        code = _normalize_security_code(row["code"])
        if code in seen_codes:
            raise ValueError(f"duplicate security code: {code}")
        seen_codes.add(code)
        normalized.append({
            "code": code,
            "name": str(row.get("name") or ""),
            "shares": _validate_nonnegative_finite(row["shares"], "shares"),
        })
    return normalized


# ── Write ─────────────────────────────────────────────────────────────────────

def insert_strategy_run(strategy: str, data_date: date) -> int:
    _validate_strategy(strategy)
    trade_date = _next_weekday(data_date)
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO strategy_runs
               (strategy, data_date, trade_date, created_at, status)
               VALUES (?,?,?,?, 'pending')""",
            (strategy, data_date.isoformat(), trade_date.isoformat(), datetime.now().isoformat()),
        )
        return cur.lastrowid


def _cb_ranking_rows(run_id: int, df: pd.DataFrame) -> list[tuple]:
    return [
        (run_id, i, str(getattr(r, "bond_code", "")).zfill(6),
         getattr(r, "bond_name", ""), getattr(r, "cb_price", None),
         getattr(r, "premium_rate", None), getattr(r, "double_low", None),
         getattr(r, "score", None))
        for i, r in enumerate(df.itertuples(index=False), start=1)
    ]


def _stock_ranking_rows(run_id: int, df: pd.DataFrame) -> list[tuple]:
    return [
        (run_id, int(getattr(r, "rank", i)),
         str(getattr(r, "stock_code", "")).zfill(6),
         getattr(r, "stock_name_q", getattr(r, "stock_name", "")),
         getattr(r, "total_mv_yuan", None) / 1e8 if getattr(r, "total_mv_yuan", None) is not None else None,
         getattr(r, "pe_ttm", None), getattr(r, "roe_pct", None))
        for i, r in enumerate(df.itertuples(index=False), start=1)
    ]


def _insert_ranking_rows(
    conn: sqlite3.Connection,
    strategy: str,
    run_id: int,
    df: pd.DataFrame,
) -> int:
    run = conn.execute(
        "SELECT strategy FROM strategy_runs WHERE id=?", (run_id,)
    ).fetchone()
    if run is None or run["strategy"] != strategy:
        raise ValueError(f"Strategy run {run_id} does not belong to {strategy}")

    if strategy == "cb":
        rows = _cb_ranking_rows(run_id, df)
        conn.executemany(
            "INSERT INTO cb_rankings (run_id,rank,bond_code,bond_name,cb_price,premium_rate,double_low,score) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
    else:
        rows = _stock_ranking_rows(run_id, df)
        conn.executemany(
            "INSERT INTO stock_rankings (run_id,rank,stock_code,stock_name,market_cap,pe_ttm,roe_ex) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def _insert_rankings_and_complete(
    conn: sqlite3.Connection,
    strategy: str,
    run_id: int,
    df: pd.DataFrame,
) -> None:
    inserted = _insert_ranking_rows(conn, strategy, run_id, df)
    if inserted:
        conn.execute(
            "UPDATE strategy_runs SET status='complete' WHERE id=? AND strategy=?",
            (run_id, strategy),
        )


def _insert_rankings_compat(strategy: str, run_id: int, df: pd.DataFrame) -> None:
    with _conn() as conn:
        conn.execute("SAVEPOINT insert_rankings_compat")
        try:
            _insert_rankings_and_complete(conn, strategy, run_id, df)
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT insert_rankings_compat")
            conn.execute("RELEASE SAVEPOINT insert_rankings_compat")
            raise
        conn.execute("RELEASE SAVEPOINT insert_rankings_compat")


def insert_cb_rankings(run_id: int, df: pd.DataFrame) -> None:
    _insert_rankings_compat("cb", run_id, df)


def insert_stock_rankings(run_id: int, df: pd.DataFrame) -> None:
    _insert_rankings_compat("stock", run_id, df)


def create_complete_strategy_run(
    strategy: str,
    data_date: date,
    trade_date: date | None,
    rankings: pd.DataFrame,
) -> int:
    _validate_strategy(strategy)
    if rankings.empty:
        raise ValueError("A complete strategy run requires at least one ranking")
    resolved_trade_date = trade_date or _next_weekday(data_date)

    with _conn() as conn:
        conn.execute("SAVEPOINT create_complete_strategy_run")
        try:
            cur = conn.execute(
                """INSERT INTO strategy_runs
                   (strategy, data_date, trade_date, created_at, status)
                   VALUES (?,?,?,?, 'pending')""",
                (
                    strategy,
                    data_date.isoformat(),
                    resolved_trade_date.isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            run_id = cur.lastrowid
            _insert_rankings_and_complete(conn, strategy, run_id, rankings)
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT create_complete_strategy_run")
            conn.execute("RELEASE SAVEPOINT create_complete_strategy_run")
            raise
        conn.execute("RELEASE SAVEPOINT create_complete_strategy_run")
        return run_id


def insert_cb_orders(run_id: int, df: pd.DataFrame) -> None:
    rows = [
        (run_id, r.action, str(r.bond_code).zfill(6),
         getattr(r, "bond_name", ""), r.price, int(r.delta_shares), r.amount)
        for r in df.itertuples(index=False)
    ]
    with _conn() as conn:
        conn.execute("DELETE FROM cb_orders WHERE run_id=?", (run_id,))
        conn.executemany(
            "INSERT INTO cb_orders (run_id,action,bond_code,bond_name,price,shares,amount) VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def insert_stock_orders(run_id: int, df: pd.DataFrame) -> None:
    rows = [
        (run_id, r.action, str(r.stock_code).zfill(6),
         getattr(r, "stock_name", ""), r.price, int(r.delta_shares), r.amount)
        for r in df.itertuples(index=False)
    ]
    with _conn() as conn:
        conn.execute("DELETE FROM stock_orders WHERE run_id=?", (run_id,))
        conn.executemany(
            "INSERT INTO stock_orders (run_id,action,stock_code,stock_name,price,shares,amount) VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def insert_account_snapshot(
    snapshot_date: str, temperature: float,
    stock_total: float, stock_cash: float,
    bond_total: float, bond_cash: float,
    changqian_total: float, cash_pool: float, overseas_total: float,
    changqian_updated_at: str | None = None,
    cash_pool_updated_at: str | None = None,
    overseas_updated_at: str | None = None,
) -> int:
    _validate_iso_date(snapshot_date)
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
        _validate_nonnegative_finite(value, field)
    insert_account_context(snapshot_date, temperature)
    ids = [
        insert_account_value_snapshot("stock", snapshot_date, stock_total, stock_cash),
        insert_account_value_snapshot("cb", snapshot_date, bond_total, bond_cash),
        insert_account_value_snapshot("changqian", snapshot_date, changqian_total),
        insert_account_value_snapshot("cash", snapshot_date, cash_pool),
        insert_account_value_snapshot("overseas", snapshot_date, overseas_total),
    ]
    return ids[-1]


def insert_account_value_snapshot(
    account_id: str,
    snapshot_date: str,
    total: float,
    cash: float | None = None,
    frozen_cash: float | None = None,
) -> int:
    _validate_account_id(account_id)
    _validate_iso_date(snapshot_date)
    total = _validate_nonnegative_finite(total, "total")
    if cash is not None:
        cash = _validate_nonnegative_finite(cash, "cash")
    if frozen_cash is not None:
        frozen_cash = _validate_nonnegative_finite(frozen_cash, "frozen_cash")
    if frozen_cash is None:
        frozen_cash = 0.0
    if cash is None and frozen_cash:
        raise ValueError("frozen_cash requires cash")
    if cash is not None and frozen_cash > cash:
        raise ValueError("frozen_cash must be less than or equal to cash")
    with _conn() as conn:
        return _insert_account_value_snapshot(
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
    cur = conn.execute(
        """INSERT INTO account_value_snapshots
           (account_id,snapshot_date,total,cash,frozen_cash,created_at)
           VALUES (?,?,?,?,?,?)""",
        (account_id, snapshot_date, total, cash, frozen_cash, datetime.now().isoformat()),
    )
    return cur.lastrowid


def insert_account_context(
    snapshot_date: str,
    temperature: float,
    *,
    check_type: str = "a_internal",
    new_contribution: float = 0,
    b_purchase_limit: float = 0,
    b_purchase_checked_at: str | None = None,
    b_purchase_source: str | None = None,
) -> int:
    _validate_iso_date(snapshot_date)
    if check_type not in {"monthly_contribution", "quarterly", "a_internal", "b_recovery", "ad_hoc"}:
        raise ValueError("invalid check_type")
    new_contribution = _validate_nonnegative_finite(new_contribution, "new_contribution")
    b_purchase_limit = _validate_nonnegative_finite(b_purchase_limit, "b_purchase_limit")
    if b_purchase_checked_at is not None:
        _validate_iso_datetime(b_purchase_checked_at, "b_purchase_checked_at")
    if b_purchase_source is not None and not isinstance(b_purchase_source, str):
        raise ValueError("b_purchase_source must be text")
    if b_purchase_limit >= 1000 and (not b_purchase_checked_at or not b_purchase_source):
        raise ValueError("b_purchase limit requires checked_at and source")
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO account_contexts
               (snapshot_date,temperature,check_type,new_contribution,b_purchase_limit,
                b_purchase_checked_at,b_purchase_source,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                snapshot_date, temperature, check_type, new_contribution, b_purchase_limit,
                b_purchase_checked_at, b_purchase_source, datetime.now().isoformat(),
            ),
        )
        return cur.lastrowid


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


def append_position_snapshot(strategy: str, position_date: str, rows: list[dict]) -> dict:
    _validate_strategy(strategy)
    _validate_iso_date(position_date, "position_date")
    normalized = _normalize_position_rows(rows)
    with _conn() as conn:
        conn.execute("SAVEPOINT append_position_snapshot")
        try:
            snapshot_id = _insert_position_snapshot(conn, strategy, position_date, normalized)
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT append_position_snapshot")
            conn.execute("RELEASE SAVEPOINT append_position_snapshot")
            raise
        conn.execute("RELEASE SAVEPOINT append_position_snapshot")
        return _get_position_snapshot(conn, "s.id=?", (snapshot_id,))


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
            position_snapshot_id = _insert_position_snapshot(
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
            "position_snapshot": _get_position_snapshot(
                conn, "s.id=?", (position_snapshot_id,)
            ),
        }


def insert_positions(strategy: str, position_date: str, rows: list[dict]) -> None:
    """Compatibility writer; new storage is immutable and append-only."""
    append_position_snapshot(strategy, position_date, rows)


# ── Read ──────────────────────────────────────────────────────────────────────

def _ranking_table(strategy: str) -> str:
    _validate_strategy(strategy)
    return "cb_rankings" if strategy == "cb" else "stock_rankings"


def get_latest_run_id(strategy: str) -> int | None:
    table = _ranking_table(strategy)
    with _conn() as conn:
        row = conn.execute(
            f"""SELECT sr.id FROM strategy_runs sr
                WHERE sr.strategy=? AND sr.status='complete'
                  AND EXISTS (SELECT 1 FROM {table} r WHERE r.run_id=sr.id)
                ORDER BY sr.id DESC LIMIT 1""",
            (strategy,)
        ).fetchone()
        return row["id"] if row else None


def get_latest_strategy_run(strategy: str) -> dict | None:
    table = _ranking_table(strategy)
    with _conn() as conn:
        row = conn.execute(
            f"""SELECT sr.id, sr.strategy, sr.data_date, sr.trade_date,
                       sr.created_at, sr.status
                FROM strategy_runs sr
                WHERE sr.strategy=? AND sr.status='complete'
                  AND EXISTS (SELECT 1 FROM {table} r WHERE r.run_id=sr.id)
                ORDER BY sr.id DESC LIMIT 1""",
            (strategy,),
        ).fetchone()
        return dict(row) if row else None


def get_strategy_run(run_id: int, strategy: str) -> dict | None:
    table = _ranking_table(strategy)
    with _conn() as conn:
        row = conn.execute(
            f"""SELECT sr.id, sr.strategy, sr.data_date, sr.trade_date,
                       sr.created_at, sr.status
                FROM strategy_runs sr
                WHERE sr.id=? AND sr.strategy=? AND sr.status='complete'
                  AND EXISTS (SELECT 1 FROM {table} r WHERE r.run_id=sr.id)""",
            (run_id, strategy),
        ).fetchone()
        return dict(row) if row else None


def get_current_account_summary() -> dict | None:
    with _conn() as conn:
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
        snap["accounts"] = _build_account_items(snap)
        snap["total_assets"] = (
            snap["stock_total"] + snap["bond_total"] + snap["changqian_total"]
            + snap["cash_pool"] + snap["overseas_total"]
        )
        return snap


def _build_account_items(snap: dict) -> list[dict]:
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


def _get_position_snapshot(
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


def get_position_snapshot_by_date(strategy: str, date_str: str) -> dict | None:
    _validate_strategy(strategy)
    _validate_iso_date(date_str, "position_date")
    with _conn() as conn:
        return _get_position_snapshot(
            conn,
            "s.strategy=? AND s.position_date=?",
            (strategy, date_str),
        )


def get_latest_position_snapshot(strategy: str) -> dict | None:
    _validate_strategy(strategy)
    with _conn() as conn:
        return _get_position_snapshot(
            conn,
            "s.strategy=?",
            (strategy,),
            order_by="s.position_date DESC, s.id DESC",
        )


def get_position_snapshot_asof(strategy: str, date_str: str) -> dict | None:
    _validate_strategy(strategy)
    _validate_iso_date(date_str, "position_date")
    with _conn() as conn:
        return _get_position_snapshot(
            conn,
            "s.strategy=? AND s.position_date<=?",
            (strategy, date_str),
            order_by="s.position_date DESC, s.id DESC",
        )


def get_position_snapshot_between(
    strategy: str,
    start_date: str,
    end_date: str,
) -> dict | None:
    _validate_strategy(strategy)
    _validate_iso_date(start_date, "start_date")
    _validate_iso_date(end_date, "end_date")
    with _conn() as conn:
        return _get_position_snapshot(
            conn,
            "s.strategy=? AND s.position_date>=? AND s.position_date<=?",
            (strategy, start_date, end_date),
            order_by="s.position_date DESC, s.id DESC",
        )


def get_position_snapshot_updated_between(
    strategy: str,
    start_date: str,
    end_date: str,
) -> dict | None:
    _validate_strategy(strategy)
    _validate_iso_date(start_date, "start_date")
    _validate_iso_date(end_date, "end_date")
    with _conn() as conn:
        return _get_position_snapshot(
            conn,
            "s.strategy=? AND date(s.created_at)>=date(?) AND date(s.created_at)<=date(?)",
            (strategy, start_date, end_date),
            order_by="s.created_at DESC, s.id DESC",
        )


def _snapshot_items_for_compatibility(snapshot: dict | None) -> list[dict]:
    if snapshot is None:
        return []
    return [
        {**item, "position_date": snapshot["position_date"]}
        for item in snapshot["items"]
    ]


def get_latest_positions(strategy: str) -> list[dict]:
    return _snapshot_items_for_compatibility(get_latest_position_snapshot(strategy))


def get_position_dates(strategy: str) -> list[str]:
    _validate_strategy(strategy)
    with _conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT position_date FROM position_snapshots
               WHERE strategy=? ORDER BY position_date DESC""",
            (strategy,),
        ).fetchall()
        return [r["position_date"] for r in rows]


def get_positions_by_date(strategy: str, date_str: str) -> list[dict]:
    return _snapshot_items_for_compatibility(
        get_position_snapshot_by_date(strategy, date_str)
    )


def get_positions_asof(strategy: str, date_str: str) -> list[dict]:
    return _snapshot_items_for_compatibility(
        get_position_snapshot_asof(strategy, date_str)
    )


def get_positions_updated_between(strategy: str, start_date: str, end_date: str) -> list[dict]:
    return _snapshot_items_for_compatibility(
        get_position_snapshot_updated_between(strategy, start_date, end_date)
    )


def get_ranking_dates(strategy: str) -> list[str]:
    table = _ranking_table(strategy)
    with _conn() as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT sr.data_date FROM strategy_runs sr
                WHERE sr.strategy=? AND sr.status='complete'
                  AND EXISTS (SELECT 1 FROM {table} r WHERE r.run_id=sr.id)
                ORDER BY sr.data_date DESC""",
            (strategy,)
        ).fetchall()
        return [r["data_date"] for r in rows]


def get_strategy_run_meta(strategy: str, data_date: str) -> dict | None:
    table = _ranking_table(strategy)
    with _conn() as conn:
        row = conn.execute(
            f"""SELECT sr.id, sr.strategy, sr.data_date, sr.trade_date,
                       sr.created_at, sr.status
                FROM strategy_runs sr
                WHERE sr.strategy=? AND sr.data_date=? AND sr.status='complete'
                  AND EXISTS (SELECT 1 FROM {table} r WHERE r.run_id=sr.id)
                ORDER BY sr.id DESC LIMIT 1""",
            (strategy, data_date),
        ).fetchone()
        return dict(row) if row else None


def _get_rankings_for_run(
    conn: sqlite3.Connection,
    strategy: str,
    run_id: int,
) -> list[dict]:
    table = _ranking_table(strategy)
    run = conn.execute(
        f"""SELECT sr.id, sr.trade_date FROM strategy_runs sr
            WHERE sr.id=? AND sr.strategy=? AND sr.status='complete'
              AND EXISTS (SELECT 1 FROM {table} r WHERE r.run_id=sr.id)""",
        (run_id, strategy),
    ).fetchone()
    if not run:
        return []
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE run_id=? ORDER BY rank", (run_id,)
    ).fetchall()
    result = [dict(row) for row in rows]
    for row in result:
        row["trade_date"] = run["trade_date"]
    return result


def get_rankings_by_run_id(strategy: str, run_id: int) -> list[dict]:
    with _conn() as conn:
        return _get_rankings_for_run(conn, strategy, run_id)


def get_rankings(strategy: str, data_date: str) -> list[dict]:
    table = _ranking_table(strategy)
    with _conn() as conn:
        run = conn.execute(
            f"""SELECT sr.id FROM strategy_runs sr
                WHERE sr.strategy=? AND sr.data_date=? AND sr.status='complete'
                  AND EXISTS (SELECT 1 FROM {table} r WHERE r.run_id=sr.id)
                ORDER BY sr.id DESC LIMIT 1""",
            (strategy, data_date),
        ).fetchone()
        if not run:
            return []
        return _get_rankings_for_run(conn, strategy, run["id"])


def get_orders(strategy: str, data_date: str) -> list[dict]:
    table = "cb_orders" if strategy == "cb" else "stock_orders"
    ranking_table = _ranking_table(strategy)
    with _conn() as conn:
        run = conn.execute(
            f"""SELECT sr.id FROM strategy_runs sr
                WHERE sr.strategy=? AND sr.data_date=? AND sr.status='complete'
                  AND EXISTS (SELECT 1 FROM {ranking_table} r WHERE r.run_id=sr.id)
                ORDER BY sr.id DESC LIMIT 1""",
            (strategy, data_date),
        ).fetchone()
        if not run:
            return []
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE run_id=? ORDER BY id", (run["id"],)
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_rankings(strategy: str) -> list[dict]:
    dates = get_ranking_dates(strategy)
    return get_rankings(strategy, dates[0]) if dates else []


def get_latest_orders(strategy: str) -> list[dict]:
    dates = get_ranking_dates(strategy)
    return get_orders(strategy, dates[0]) if dates else []


def clear_latest_orders() -> None:
    """Invalidate the current derived orders after any plan input changes."""
    with _conn() as conn:
        for strategy, order_table, ranking_table in (
            ("cb", "cb_orders", "cb_rankings"),
            ("stock", "stock_orders", "stock_rankings"),
        ):
            conn.execute(
                f"""DELETE FROM {order_table}
                    WHERE run_id=(
                        SELECT sr.id FROM strategy_runs sr
                        WHERE sr.strategy=? AND sr.status='complete'
                          AND EXISTS (
                              SELECT 1 FROM {ranking_table} r WHERE r.run_id=sr.id
                          )
                        ORDER BY sr.id DESC LIMIT 1
                    )""",
                (strategy,),
            )


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
