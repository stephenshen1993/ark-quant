"""
datasource/db.py — SQLite 持久化层

所有写入和查询都通过这个模块。测试通过 _TEST_CONN 注入内存库。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "ark_quant.db"

_TEST_CONN: sqlite3.Connection | None = None  # 测试注入点


def get_connection() -> sqlite3.Connection:
    """Legacy getter for backward compatibility (used in test_db.py)."""
    if _TEST_CONN is not None:
        return _TEST_CONN
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _conn():
    """Context manager: closes connection after use in production; reuses _TEST_CONN in tests."""
    if _TEST_CONN is not None:
        yield _TEST_CONN
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
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
            created_at  TEXT    NOT NULL
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
            created_at      TEXT    NOT NULL
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
        """)


def _next_weekday(d: date) -> date:
    """返回 d 之后的下一个工作日（跳过周六、周日）。"""
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# ── Write ─────────────────────────────────────────────────────────────────────

def insert_strategy_run(strategy: str, data_date: date) -> int:
    trade_date = _next_weekday(data_date)
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO strategy_runs (strategy, data_date, trade_date, created_at) VALUES (?,?,?,?)",
            (strategy, data_date.isoformat(), trade_date.isoformat(), datetime.now().isoformat()),
        )
        return cur.lastrowid


def insert_cb_rankings(run_id: int, df: pd.DataFrame) -> None:
    rows = [
        (run_id, i, str(getattr(r, "bond_code", "")).zfill(6),
         getattr(r, "bond_name", ""), getattr(r, "cb_price", None),
         getattr(r, "premium_rate", None), getattr(r, "double_low", None),
         getattr(r, "score", None))
        for i, r in enumerate(df.itertuples(index=False), start=1)
    ]
    with _conn() as conn:
        conn.executemany(
            "INSERT INTO cb_rankings (run_id,rank,bond_code,bond_name,cb_price,premium_rate,double_low,score) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )


def insert_stock_rankings(run_id: int, df: pd.DataFrame) -> None:
    rows = [
        (run_id, int(getattr(r, "rank", i)),
         str(getattr(r, "stock_code", "")).zfill(6),
         getattr(r, "stock_name_q", getattr(r, "stock_name", "")),
         getattr(r, "total_mv_yuan", None) / 1e8 if getattr(r, "total_mv_yuan", None) is not None else None,
         getattr(r, "pe_ttm", None), getattr(r, "roe_pct", None))
        for i, r in enumerate(df.itertuples(index=False), start=1)
    ]
    with _conn() as conn:
        conn.executemany(
            "INSERT INTO stock_rankings (run_id,rank,stock_code,stock_name,market_cap,pe_ttm,roe_ex) VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def insert_cb_orders(run_id: int, df: pd.DataFrame) -> None:
    rows = [
        (run_id, r.action, str(r.bond_code).zfill(6),
         getattr(r, "bond_name", ""), r.price, int(r.delta_shares), r.amount)
        for r in df.itertuples(index=False)
    ]
    with _conn() as conn:
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
        conn.executemany(
            "INSERT INTO stock_orders (run_id,action,stock_code,stock_name,price,shares,amount) VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def insert_account_snapshot(
    snapshot_date: str, temperature: float,
    stock_total: float, stock_cash: float,
    bond_total: float, bond_cash: float,
    changqian_total: float, cash_pool: float, overseas_total: float,
) -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO account_snapshots
               (snapshot_date,temperature,stock_total,stock_cash,bond_total,bond_cash,
                changqian_total,cash_pool,overseas_total,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (snapshot_date, temperature, stock_total, stock_cash, bond_total, bond_cash,
             changqian_total, cash_pool, overseas_total, datetime.now().isoformat()),
        )
        return cur.lastrowid


def insert_positions(strategy: str, position_date: str, rows: list[dict]) -> None:
    if strategy == "cb":
        table, code_col, name_col = "cb_positions", "bond_code", "bond_name"
    else:
        table, code_col, name_col = "stock_positions", "stock_code", "stock_name"
    with _conn() as conn:
        conn.execute(f"DELETE FROM {table} WHERE position_date=?", (position_date,))
        conn.executemany(
            f"INSERT INTO {table} (position_date,{code_col},{name_col},shares) VALUES (?,?,?,?)",
            [(position_date, r["code"], r.get("name", ""), r["shares"]) for r in rows],
        )


# ── Read ──────────────────────────────────────────────────────────────────────

def get_latest_run_id(strategy: str) -> int | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM strategy_runs WHERE strategy=? ORDER BY id DESC LIMIT 1",
            (strategy,)
        ).fetchone()
        return row["id"] if row else None


def get_latest_account_snapshot() -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_account_history() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT snapshot_date, stock_total, bond_total, changqian_total, cash_pool, overseas_total "
            "FROM account_snapshots ORDER BY snapshot_date"
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_positions(strategy: str) -> list[dict]:
    if strategy == "cb":
        table, code_col, name_col = "cb_positions", "bond_code", "bond_name"
    else:
        table, code_col, name_col = "stock_positions", "stock_code", "stock_name"
    with _conn() as conn:
        row = conn.execute(f"SELECT MAX(position_date) as d FROM {table}").fetchone()
        if not row or not row["d"]:
            return []
        rows = conn.execute(
            f"SELECT {code_col} as code, {name_col} as name, shares, position_date "
            f"FROM {table} WHERE position_date=? ORDER BY {code_col}",
            (row["d"],)
        ).fetchall()
        return [dict(r) for r in rows]


def get_position_dates(strategy: str) -> list[str]:
    table = "cb_positions" if strategy == "cb" else "stock_positions"
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT position_date FROM {table} ORDER BY position_date DESC"
        ).fetchall()
        return [r["position_date"] for r in rows]


def get_positions_by_date(strategy: str, date_str: str) -> list[dict]:
    if strategy == "cb":
        table, code_col, name_col = "cb_positions", "bond_code", "bond_name"
    else:
        table, code_col, name_col = "stock_positions", "stock_code", "stock_name"
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {code_col} as code, {name_col} as name, shares "
            f"FROM {table} WHERE position_date=? ORDER BY {code_col}",
            (date_str,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_ranking_dates(strategy: str) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT data_date FROM strategy_runs WHERE strategy=? ORDER BY data_date DESC",
            (strategy,)
        ).fetchall()
        return [r["data_date"] for r in rows]


def get_rankings(strategy: str, data_date: str) -> list[dict]:
    table = "cb_rankings" if strategy == "cb" else "stock_rankings"
    with _conn() as conn:
        run = conn.execute(
            "SELECT id, trade_date FROM strategy_runs WHERE strategy=? AND data_date=? ORDER BY id DESC LIMIT 1",
            (strategy, data_date),
        ).fetchone()
        if not run:
            return []
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE run_id=? ORDER BY rank", (run["id"],)
        ).fetchall()
        result = [dict(r) for r in rows]
        for r in result:
            r["trade_date"] = run["trade_date"]
        return result


def get_orders(strategy: str, data_date: str) -> list[dict]:
    table = "cb_orders" if strategy == "cb" else "stock_orders"
    with _conn() as conn:
        run = conn.execute(
            "SELECT id FROM strategy_runs WHERE strategy=? AND data_date=? ORDER BY id DESC LIMIT 1",
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
