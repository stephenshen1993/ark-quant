"""Versioned local cache for derived factors and strategy rankings."""

from __future__ import annotations

import io
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Callable

import pandas as pd

from datasource.market_data_bundle import PreparationMetadata, stable_fingerprint


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DERIVED_STORE = ROOT / "data" / "cache" / "derived.sqlite3"


@dataclass(frozen=True)
class DerivedFactorResult:
    frame: pd.DataFrame
    metadata: PreparationMetadata
    input_fingerprint: str


@dataclass(frozen=True)
class StrategyRankingResult:
    frame: pd.DataFrame
    metadata: PreparationMetadata
    input_fingerprint: str


def prepare_derived_factors(
    inputs: pd.DataFrame,
    *,
    symbol_field: str,
    effective_date: date,
    factor_name: str,
    algorithm_version: str,
    input_fingerprint: str,
    compute: Callable[[pd.DataFrame], pd.DataFrame],
    store_path: Path = DEFAULT_DERIVED_STORE,
) -> DerivedFactorResult:
    """Compute only factor identities absent for the exact input and algorithm version."""
    started = perf_counter()
    if symbol_field not in inputs.columns:
        raise RuntimeError(f"因子输入缺少证券字段 {symbol_field}")
    symbols = sorted({str(value) for value in inputs[symbol_field].dropna()})
    conn = _connect(store_path)
    try:
        values = _load_factor_values(
            conn,
            symbols,
            effective_date,
            factor_name,
            algorithm_version,
            input_fingerprint,
        )
        missing = [symbol for symbol in symbols if symbol not in values]
        if missing:
            subset = inputs[inputs[symbol_field].astype(str).isin(missing)].copy()
            computed = compute(subset)
            fresh = _normalize_factor_values(computed, symbol_field, factor_name, missing)
            _upsert_factor_values(
                conn,
                fresh,
                effective_date,
                factor_name,
                algorithm_version,
                input_fingerprint,
            )
            values.update(fresh)
        unresolved = [symbol for symbol in symbols if symbol not in values]
        if unresolved:
            raise RuntimeError(f"派生因子 {factor_name} 缺少证券: {', '.join(unresolved[:10])}")
        mode = "cache_hit" if not missing else ("cold_build" if len(missing) == len(symbols) else "incremental")
        elapsed = int((perf_counter() - started) * 1000)
        frame = pd.DataFrame(
            [{symbol_field: symbol, factor_name: values[symbol]} for symbol in symbols]
        )
        metadata = PreparationMetadata(
            effective_date=effective_date.isoformat(),
            mode=mode,
            reused_records=len(symbols) - len(missing),
            refreshed_records=len(missing),
            stage_timings_ms={"derived_factors": elapsed, "total": elapsed},
        )
        return DerivedFactorResult(
            frame=frame,
            metadata=metadata,
            input_fingerprint=stable_fingerprint(frame.to_dict("records")),
        )
    finally:
        conn.close()


def prepare_strategy_ranking(
    *,
    strategy: str,
    effective_date: date,
    strategy_version: str,
    config_fingerprint: str,
    input_fingerprint: str,
    compute: Callable[[], pd.DataFrame],
    store_path: Path = DEFAULT_DERIVED_STORE,
) -> StrategyRankingResult:
    """Reuse an exact strategy/config/input ranking or recompute it entirely locally."""
    started = perf_counter()
    conn = _connect(store_path)
    try:
        row = conn.execute(
            """
            SELECT data_json FROM strategy_rankings
            WHERE strategy=? AND data_date=? AND strategy_version=?
              AND config_fingerprint=? AND input_fingerprint=? AND status='complete'
            """,
            (
                strategy,
                effective_date.isoformat(),
                strategy_version,
                config_fingerprint,
                input_fingerprint,
            ),
        ).fetchone()
        if row is not None:
            try:
                frame = pd.read_json(io.StringIO(row["data_json"]), orient="table")
            except ValueError:
                frame = pd.DataFrame()
            if not frame.empty:
                elapsed = int((perf_counter() - started) * 1000)
                metadata = PreparationMetadata(
                    effective_date=effective_date.isoformat(),
                    mode="cache_hit",
                    reused_records=len(frame),
                    stage_timings_ms={"ranking": elapsed, "total": elapsed},
                )
                return StrategyRankingResult(
                    frame=frame,
                    metadata=metadata,
                    input_fingerprint=input_fingerprint,
                )

        frame = compute()
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise RuntimeError(f"{strategy} 策略没有生成有效榜单")
        data_json = frame.to_json(orient="table", date_format="iso", force_ascii=False)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                INSERT INTO strategy_rankings(
                    strategy, data_date, strategy_version, config_fingerprint,
                    input_fingerprint, status, data_json
                ) VALUES (?, ?, ?, ?, ?, 'complete', ?)
                ON CONFLICT(
                    strategy, data_date, strategy_version, config_fingerprint, input_fingerprint
                ) DO UPDATE SET status='complete', data_json=excluded.data_json
                """,
                (
                    strategy,
                    effective_date.isoformat(),
                    strategy_version,
                    config_fingerprint,
                    input_fingerprint,
                    data_json,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        elapsed = int((perf_counter() - started) * 1000)
        metadata = PreparationMetadata(
            effective_date=effective_date.isoformat(),
            mode="cold_build",
            refreshed_records=len(frame),
            stage_timings_ms={"ranking": elapsed, "total": elapsed},
        )
        return StrategyRankingResult(
            frame=frame,
            metadata=metadata,
            input_fingerprint=input_fingerprint,
        )
    finally:
        conn.close()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS derived_factors (
            symbol TEXT NOT NULL,
            data_date TEXT NOT NULL,
            factor_name TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            value_json TEXT NOT NULL,
            PRIMARY KEY (
                symbol, data_date, factor_name, algorithm_version, input_fingerprint
            )
        );
        CREATE TABLE IF NOT EXISTS strategy_rankings (
            strategy TEXT NOT NULL,
            data_date TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            config_fingerprint TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            data_json TEXT NOT NULL,
            PRIMARY KEY (
                strategy, data_date, strategy_version, config_fingerprint, input_fingerprint
            )
        );
        """
    )
    conn.commit()
    return conn


def _load_factor_values(
    conn: sqlite3.Connection,
    symbols: list[str],
    effective_date: date,
    factor_name: str,
    algorithm_version: str,
    input_fingerprint: str,
) -> dict[str, object]:
    rows = conn.execute(
        """
        SELECT symbol, value_json FROM derived_factors
        WHERE data_date=? AND factor_name=? AND algorithm_version=? AND input_fingerprint=?
        """,
        (
            effective_date.isoformat(),
            factor_name,
            algorithm_version,
            input_fingerprint,
        ),
    ).fetchall()
    allowed = set(symbols)
    values: dict[str, object] = {}
    for row in rows:
        symbol = str(row["symbol"])
        if symbol not in allowed:
            continue
        try:
            value = json.loads(row["value_json"])
        except json.JSONDecodeError:
            continue
        if not _missing(value):
            values[symbol] = value
    return values


def _normalize_factor_values(
    frame: pd.DataFrame,
    symbol_field: str,
    factor_name: str,
    expected_symbols: list[str],
) -> dict[str, object]:
    missing_columns = {symbol_field, factor_name} - set(frame.columns)
    if missing_columns:
        raise RuntimeError(f"因子结果缺少字段: {', '.join(sorted(missing_columns))}")
    allowed = set(expected_symbols)
    values: dict[str, object] = {}
    for row in frame.to_dict("records"):
        symbol = str(row[symbol_field])
        value = row.get(factor_name)
        if symbol in allowed and not _missing(value):
            values[symbol] = value.item() if hasattr(value, "item") else value
    return values


def _upsert_factor_values(
    conn: sqlite3.Connection,
    values: dict[str, object],
    effective_date: date,
    factor_name: str,
    algorithm_version: str,
    input_fingerprint: str,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        for symbol, value in values.items():
            conn.execute(
                """
                INSERT INTO derived_factors(
                    symbol, data_date, factor_name, algorithm_version,
                    input_fingerprint, value_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, data_date, factor_name, algorithm_version, input_fingerprint)
                DO UPDATE SET value_json=excluded.value_json
                """,
                (
                    symbol,
                    effective_date.isoformat(),
                    factor_name,
                    algorithm_version,
                    input_fingerprint,
                    json.dumps(value, ensure_ascii=False),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))
