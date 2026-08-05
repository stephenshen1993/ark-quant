"""Strategy run, ranking, and derived-order persistence helpers."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd


def next_weekday(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def validate_strategy(strategy: str) -> str:
    if strategy not in {"cb", "stock"}:
        raise ValueError(f"Unknown strategy: {strategy}")
    return strategy


def ranking_table(strategy: str) -> str:
    validate_strategy(strategy)
    return "cb_rankings" if strategy == "cb" else "stock_rankings"


def insert_strategy_run(conn: sqlite3.Connection, strategy: str, data_date: date) -> int:
    validate_strategy(strategy)
    trade_date = next_weekday(data_date)
    cur = conn.execute(
        """INSERT INTO strategy_runs
           (strategy, data_date, trade_date, created_at, status)
           VALUES (?,?,?,?, 'pending')""",
        (strategy, data_date.isoformat(), trade_date.isoformat(), datetime.now().isoformat()),
    )
    return cur.lastrowid


def _cb_ranking_rows(run_id: int, df: pd.DataFrame) -> list[tuple]:
    return [
        (
            run_id,
            i,
            str(getattr(r, "bond_code", "")).zfill(6),
            getattr(r, "bond_name", ""),
            getattr(r, "cb_price", None),
            getattr(r, "premium_rate", None),
            getattr(r, "double_low", None),
            getattr(r, "score", None),
        )
        for i, r in enumerate(df.itertuples(index=False), start=1)
    ]


def _stock_ranking_rows(run_id: int, df: pd.DataFrame) -> list[tuple]:
    return [
        (
            run_id,
            int(getattr(r, "rank", i)),
            str(getattr(r, "stock_code", "")).zfill(6),
            getattr(r, "stock_name_q", getattr(r, "stock_name", "")),
            (
                getattr(r, "total_mv_yuan", None) / 1e8
                if getattr(r, "total_mv_yuan", None) is not None
                else None
            ),
            getattr(r, "pe_ttm", None),
            getattr(r, "roe_pct", None),
        )
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
            """INSERT INTO cb_rankings
               (run_id,rank,bond_code,bond_name,cb_price,premium_rate,double_low,score)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )
    else:
        rows = _stock_ranking_rows(run_id, df)
        conn.executemany(
            """INSERT INTO stock_rankings
               (run_id,rank,stock_code,stock_name,market_cap,pe_ttm,roe_ex)
               VALUES (?,?,?,?,?,?,?)""",
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


def insert_rankings(conn: sqlite3.Connection, strategy: str, run_id: int, df: pd.DataFrame) -> None:
    conn.execute("SAVEPOINT insert_rankings")
    try:
        _insert_rankings_and_complete(conn, strategy, run_id, df)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT insert_rankings")
        conn.execute("RELEASE SAVEPOINT insert_rankings")
        raise
    conn.execute("RELEASE SAVEPOINT insert_rankings")


def create_complete_strategy_run(
    conn: sqlite3.Connection,
    strategy: str,
    data_date: date,
    trade_date: date | None,
    rankings: pd.DataFrame,
) -> int:
    validate_strategy(strategy)
    if rankings.empty:
        raise ValueError("A complete strategy run requires at least one ranking")
    resolved_trade_date = trade_date or next_weekday(data_date)

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


def insert_cb_orders(conn: sqlite3.Connection, run_id: int, df: pd.DataFrame) -> None:
    rows = [
        (
            run_id,
            r.action,
            str(r.bond_code).zfill(6),
            getattr(r, "bond_name", ""),
            r.price,
            int(r.delta_shares),
            r.amount,
        )
        for r in df.itertuples(index=False)
    ]
    conn.execute("DELETE FROM cb_orders WHERE run_id=?", (run_id,))
    conn.executemany(
        """INSERT INTO cb_orders
           (run_id,action,bond_code,bond_name,price,shares,amount)
           VALUES (?,?,?,?,?,?,?)""",
        rows,
    )


def insert_stock_orders(conn: sqlite3.Connection, run_id: int, df: pd.DataFrame) -> None:
    rows = [
        (
            run_id,
            r.action,
            str(r.stock_code).zfill(6),
            getattr(r, "stock_name", ""),
            r.price,
            int(r.delta_shares),
            r.amount,
        )
        for r in df.itertuples(index=False)
    ]
    conn.execute("DELETE FROM stock_orders WHERE run_id=?", (run_id,))
    conn.executemany(
        """INSERT INTO stock_orders
           (run_id,action,stock_code,stock_name,price,shares,amount)
           VALUES (?,?,?,?,?,?,?)""",
        rows,
    )


def get_latest_run_id(conn: sqlite3.Connection, strategy: str) -> int | None:
    table = ranking_table(strategy)
    row = conn.execute(
        f"""SELECT sr.id FROM strategy_runs sr
            WHERE sr.strategy=? AND sr.status='complete'
              AND EXISTS (SELECT 1 FROM {table} r WHERE r.run_id=sr.id)
            ORDER BY sr.id DESC LIMIT 1""",
        (strategy,),
    ).fetchone()
    return row["id"] if row else None


def get_latest_strategy_run(conn: sqlite3.Connection, strategy: str) -> dict | None:
    table = ranking_table(strategy)
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


def get_strategy_run(conn: sqlite3.Connection, run_id: int, strategy: str) -> dict | None:
    table = ranking_table(strategy)
    row = conn.execute(
        f"""SELECT sr.id, sr.strategy, sr.data_date, sr.trade_date,
                   sr.created_at, sr.status
            FROM strategy_runs sr
            WHERE sr.id=? AND sr.strategy=? AND sr.status='complete'
              AND EXISTS (SELECT 1 FROM {table} r WHERE r.run_id=sr.id)""",
        (run_id, strategy),
    ).fetchone()
    return dict(row) if row else None


def get_ranking_dates(conn: sqlite3.Connection, strategy: str) -> list[str]:
    table = ranking_table(strategy)
    rows = conn.execute(
        f"""SELECT DISTINCT sr.data_date FROM strategy_runs sr
            WHERE sr.strategy=? AND sr.status='complete'
              AND EXISTS (SELECT 1 FROM {table} r WHERE r.run_id=sr.id)
            ORDER BY sr.data_date DESC""",
        (strategy,),
    ).fetchall()
    return [r["data_date"] for r in rows]


def get_strategy_run_meta(
    conn: sqlite3.Connection,
    strategy: str,
    data_date: str,
) -> dict | None:
    table = ranking_table(strategy)
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
    table = ranking_table(strategy)
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


def get_rankings_by_run_id(
    conn: sqlite3.Connection,
    strategy: str,
    run_id: int,
) -> list[dict]:
    return _get_rankings_for_run(conn, strategy, run_id)


def get_rankings(conn: sqlite3.Connection, strategy: str, data_date: str) -> list[dict]:
    table = ranking_table(strategy)
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


def get_orders(conn: sqlite3.Connection, strategy: str, data_date: str) -> list[dict]:
    table = "cb_orders" if strategy == "cb" else "stock_orders"
    ranking_table_name = ranking_table(strategy)
    run = conn.execute(
        f"""SELECT sr.id FROM strategy_runs sr
            WHERE sr.strategy=? AND sr.data_date=? AND sr.status='complete'
              AND EXISTS (SELECT 1 FROM {ranking_table_name} r WHERE r.run_id=sr.id)
            ORDER BY sr.id DESC LIMIT 1""",
        (strategy, data_date),
    ).fetchone()
    if not run:
        return []
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE run_id=? ORDER BY id", (run["id"],)
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_rankings(conn: sqlite3.Connection, strategy: str) -> list[dict]:
    dates = get_ranking_dates(conn, strategy)
    return get_rankings(conn, strategy, dates[0]) if dates else []


def get_latest_orders(conn: sqlite3.Connection, strategy: str) -> list[dict]:
    dates = get_ranking_dates(conn, strategy)
    return get_orders(conn, strategy, dates[0]) if dates else []


def clear_latest_orders(conn: sqlite3.Connection) -> None:
    """Invalidate the current derived orders after any plan input changes."""
    for strategy, order_table, ranking_table_name in (
        ("cb", "cb_orders", "cb_rankings"),
        ("stock", "stock_orders", "stock_rankings"),
    ):
        conn.execute(
            f"""DELETE FROM {order_table}
                WHERE run_id=(
                    SELECT sr.id FROM strategy_runs sr
                    WHERE sr.strategy=? AND sr.status='complete'
                      AND EXISTS (
                          SELECT 1 FROM {ranking_table_name} r WHERE r.run_id=sr.id
                      )
                    ORDER BY sr.id DESC LIMIT 1
                )""",
            (strategy,),
        )
