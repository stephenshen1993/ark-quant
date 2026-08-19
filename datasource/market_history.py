"""Incremental raw market history with batch-first gap filling."""

from __future__ import annotations

import json
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Callable, Iterable, Sequence

import pandas as pd

from datasource.market_data_bundle import (
    DataRequirements,
    PreparationMetadata,
    stable_fingerprint,
)
from datasource.derived_store import invalidate_corporate_action_dependencies


@dataclass(frozen=True)
class MarketHistoryResult:
    frame: pd.DataFrame
    metadata: PreparationMetadata
    input_fingerprint: str


@dataclass(frozen=True)
class CorporateActionInvalidation:
    changed_symbols: tuple[str, ...]
    changed_dates: tuple[str, ...]
    invalidated_factors: int
    invalidated_rankings: int


def prepare_market_history(
    requirements: DataRequirements,
    effective_date: date,
    symbols: Sequence[str],
    trading_days: Iterable[date],
    batch_fetcher: Callable[[Sequence[date]], pd.DataFrame],
    *,
    fallback_fetcher: Callable[[str, date, date], pd.DataFrame] | None = None,
    store_path: Path,
    fallback_workers: int = 4,
) -> MarketHistoryResult:
    """Fill only proven trading-day gaps, using full-market batches before per-symbol fallback."""
    started = perf_counter()
    required_dates = _required_dates(
        effective_date,
        trading_days,
        requirements.lookback_trading_days,
    )
    required_symbols = sorted({str(symbol) for symbol in symbols})
    if not required_symbols:
        raise RuntimeError("历史行情准备缺少证券范围")
    if not requirements.market_fields:
        raise RuntimeError("历史行情准备缺少必需字段声明")

    conn = _connect(store_path)
    try:
        before = _load_rows(conn, requirements, required_symbols, required_dates)
        missing_before = _missing_pairs(
            before,
            requirements,
            required_symbols,
            required_dates,
        )
        missing_dates = sorted({day for _, day in missing_before})
        reused_records = len(required_symbols) * len(required_dates) - len(missing_before)
        refreshed_keys: set[tuple[str, date]] = set()
        batch_requests = 0
        fallback_symbols = 0
        external_calls = 0

        if missing_dates:
            batch_requests = 1
            external_calls += 1
            batch = batch_fetcher(missing_dates)
            batch_rows = _normalize_rows(
                batch,
                requirements,
                required_symbols,
                missing_dates,
            )
            refreshed_keys.update(_upsert_rows(conn, requirements, batch_rows))

        after_batch = _load_rows(conn, requirements, required_symbols, required_dates)
        remaining = _missing_pairs(
            after_batch,
            requirements,
            required_symbols,
            required_dates,
        )
        missing_by_symbol: dict[str, list[date]] = {}
        for symbol, day in remaining:
            missing_by_symbol.setdefault(symbol, []).append(day)

        if missing_by_symbol and fallback_fetcher is not None:
            fallback_symbols = len(missing_by_symbol)
            external_calls += fallback_symbols

            def _fetch_symbol(item: tuple[str, list[date]]) -> pd.DataFrame:
                symbol, days = item
                return fallback_fetcher(symbol, min(days), max(days))

            items = sorted(missing_by_symbol.items())
            with ThreadPoolExecutor(
                max_workers=max(1, min(fallback_workers, len(items)))
            ) as executor:
                for (symbol, days), frame in zip(items, executor.map(_fetch_symbol, items)):
                    normalized = _normalize_rows(
                        frame,
                        requirements,
                        [symbol],
                        days,
                    )
                    refreshed_keys.update(_upsert_rows(conn, requirements, normalized))

        complete = _load_rows(conn, requirements, required_symbols, required_dates)
        missing_after = _missing_pairs(
            complete,
            requirements,
            required_symbols,
            required_dates,
        )
        if missing_after:
            sample = ", ".join(
                f"{symbol}@{day.isoformat()}" for symbol, day in missing_after[:10]
            )
            raise RuntimeError(f"历史行情关键缺口未补齐: {sample}")

        mode = "cache_hit"
        if missing_before:
            mode = "cold_build" if reused_records == 0 else "incremental"
        elapsed = int((perf_counter() - started) * 1000)
        metadata = PreparationMetadata(
            effective_date=effective_date.isoformat(),
            mode=mode,
            reused_records=reused_records,
            refreshed_records=len(refreshed_keys),
            external_calls=external_calls,
            batch_requests=batch_requests,
            fallback_symbols=fallback_symbols,
            missing_trading_days=tuple(day.isoformat() for day in missing_dates),
            stage_timings_ms={"total": elapsed},
        )
        frame = _to_frame(complete, requirements, required_symbols, required_dates)
        fingerprint = stable_fingerprint(frame.to_dict("records"))
        return MarketHistoryResult(frame=frame, metadata=metadata, input_fingerprint=fingerprint)
    finally:
        conn.close()


def reconcile_corporate_actions(
    requirements: DataRequirements,
    actions: pd.DataFrame,
    *,
    store_path: Path,
    derived_store_path: Path,
) -> CorporateActionInvalidation:
    """Update adjustment facts without rewriting raw prices and invalidate exact dependencies."""
    required = {requirements.symbol_field, "trade_date", "adjustment_factor"}
    missing = sorted(required - set(actions.columns))
    if missing:
        raise RuntimeError(f"公司行为数据缺少字段: {', '.join(missing)}")
    conn = _connect(store_path)
    affected: list[tuple[str, date]] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for action in actions.to_dict("records"):
                symbol = str(action[requirements.symbol_field])
                action_date = _as_date(action["trade_date"])
                row = conn.execute(
                    """
                    SELECT data_json FROM market_history
                    WHERE source=? AND source_version=? AND symbol=? AND trade_date=?
                    """,
                    (
                        requirements.source,
                        requirements.source_version,
                        symbol,
                        action_date.isoformat(),
                    ),
                ).fetchone()
                if row is None:
                    continue
                try:
                    payload = json.loads(row["data_json"])
                except json.JSONDecodeError:
                    continue
                new_factor = _json_value(action["adjustment_factor"])
                if payload.get("adjustment_factor") == new_factor:
                    continue
                payload["adjustment_factor"] = new_factor
                conn.execute(
                    """
                    UPDATE market_history SET data_json=?
                    WHERE source=? AND source_version=? AND symbol=? AND trade_date=?
                    """,
                    (
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        requirements.source,
                        requirements.source_version,
                        symbol,
                        action_date.isoformat(),
                    ),
                )
                affected.append((symbol, action_date))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    factor_count, ranking_count = invalidate_corporate_action_dependencies(
        strategy=requirements.strategy,
        affected=affected,
        store_path=derived_store_path,
    )
    return CorporateActionInvalidation(
        changed_symbols=tuple(sorted({symbol for symbol, _ in affected})),
        changed_dates=tuple(sorted({action_date.isoformat() for _, action_date in affected})),
        invalidated_factors=factor_count,
        invalidated_rankings=ranking_count,
    )


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_history (
            source TEXT NOT NULL,
            source_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            data_json TEXT NOT NULL,
            PRIMARY KEY (source, source_version, symbol, trade_date)
        )
        """
    )
    conn.commit()
    return conn


def _required_dates(
    effective_date: date,
    trading_days: Iterable[date],
    lookback: int,
) -> list[date]:
    available = sorted({day for day in trading_days if day <= effective_date})
    if lookback <= 0:
        raise RuntimeError("历史行情回看窗口必须大于零")
    if len(available) < lookback:
        raise RuntimeError(
            f"交易日历仅覆盖 {len(available)} 日，少于所需 {lookback} 日"
        )
    return available[-lookback:]


def _load_rows(
    conn: sqlite3.Connection,
    requirements: DataRequirements,
    symbols: Sequence[str],
    trading_days: Sequence[date],
) -> dict[tuple[str, date], dict]:
    start = min(trading_days).isoformat()
    end = max(trading_days).isoformat()
    rows = conn.execute(
        """
        SELECT symbol, trade_date, data_json
        FROM market_history
        WHERE source=? AND source_version=? AND trade_date BETWEEN ? AND ?
        """,
        (requirements.source, requirements.source_version, start, end),
    ).fetchall()
    allowed_symbols = set(symbols)
    allowed_dates = set(trading_days)
    loaded: dict[tuple[str, date], dict] = {}
    for row in rows:
        symbol = str(row["symbol"])
        trade_day = date.fromisoformat(row["trade_date"])
        if symbol not in allowed_symbols or trade_day not in allowed_dates:
            continue
        try:
            payload = json.loads(row["data_json"])
        except json.JSONDecodeError:
            continue
        loaded[(symbol, trade_day)] = payload
    return loaded


def _missing_pairs(
    rows: dict[tuple[str, date], dict],
    requirements: DataRequirements,
    symbols: Sequence[str],
    trading_days: Sequence[date],
) -> list[tuple[str, date]]:
    missing: list[tuple[str, date]] = []
    for symbol in symbols:
        for day in trading_days:
            payload = rows.get((symbol, day))
            if payload is None or any(
                _missing_value(payload.get(field)) for field in requirements.market_fields
            ):
                missing.append((symbol, day))
    return missing


def _normalize_rows(
    frame: pd.DataFrame,
    requirements: DataRequirements,
    symbols: Sequence[str],
    trading_days: Sequence[date],
) -> list[dict]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    required_columns = {
        requirements.symbol_field,
        "trade_date",
        *requirements.market_fields,
    }
    missing = sorted(required_columns - set(frame.columns))
    if missing:
        raise RuntimeError(f"批量历史行情缺少字段: {', '.join(missing)}")
    allowed_symbols = set(symbols)
    allowed_dates = set(trading_days)
    normalized: dict[tuple[str, date], dict] = {}
    for raw in frame.to_dict("records"):
        symbol = str(raw[requirements.symbol_field])
        trade_day = _as_date(raw["trade_date"])
        if symbol not in allowed_symbols or trade_day not in allowed_dates:
            continue
        payload = {
            requirements.symbol_field: symbol,
            "trade_date": trade_day.isoformat(),
            **{field: _json_value(raw.get(field)) for field in requirements.market_fields},
        }
        normalized[(symbol, trade_day)] = payload
    return list(normalized.values())


def _upsert_rows(
    conn: sqlite3.Connection,
    requirements: DataRequirements,
    rows: Sequence[dict],
) -> set[tuple[str, date]]:
    keys: set[tuple[str, date]] = set()
    if not rows:
        return keys
    conn.execute("BEGIN IMMEDIATE")
    try:
        for row in rows:
            symbol = str(row[requirements.symbol_field])
            trade_day = date.fromisoformat(str(row["trade_date"]))
            conn.execute(
                """
                INSERT INTO market_history(source, source_version, symbol, trade_date, data_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, source_version, symbol, trade_date)
                DO UPDATE SET data_json=excluded.data_json
                """,
                (
                    requirements.source,
                    requirements.source_version,
                    symbol,
                    trade_day.isoformat(),
                    json.dumps(row, ensure_ascii=False, sort_keys=True),
                ),
            )
            keys.add((symbol, trade_day))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return keys


def _to_frame(
    rows: dict[tuple[str, date], dict],
    requirements: DataRequirements,
    symbols: Sequence[str],
    trading_days: Sequence[date],
) -> pd.DataFrame:
    ordered = [rows[(symbol, day)] for day in trading_days for symbol in symbols]
    columns = [requirements.symbol_field, "trade_date", *requirements.market_fields]
    return pd.DataFrame(ordered, columns=columns)


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _missing_value(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _json_value(value: object) -> object:
    if _missing_value(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value
