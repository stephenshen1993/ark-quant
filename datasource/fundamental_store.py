"""Report-period keyed low-frequency fundamental facts."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Callable, Sequence

import pandas as pd

from datasource.market_data_bundle import PreparationMetadata, stable_fingerprint


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FUNDAMENTAL_STORE = ROOT / "data" / "cache" / "fundamentals.sqlite3"


@dataclass(frozen=True)
class FundamentalRequirements:
    symbol_field: str
    metrics: tuple[str, ...]
    caliber: str
    source: str
    source_version: str


@dataclass(frozen=True)
class FundamentalResult:
    frame: pd.DataFrame
    metadata: PreparationMetadata
    input_fingerprint: str


def expected_report_period(effective_date: date) -> str:
    """Return the latest mandatory mainland quarterly reporting period."""
    year = effective_date.year
    if effective_date.month <= 4:
        return f"{year - 1}-09-30"
    if effective_date.month <= 8:
        return f"{year}-03-31"
    if effective_date.month <= 10:
        return f"{year}-06-30"
    return f"{year}-09-30"


def prepare_fundamentals(
    requirements: FundamentalRequirements,
    symbols: Sequence[str],
    report_period: str,
    fetcher: Callable[[Sequence[str], str, Sequence[str]], pd.DataFrame],
    *,
    effective_date: date,
    store_path: Path,
) -> FundamentalResult:
    """Reuse complete report-period facts and fetch only missing symbol/metric pairs."""
    started = perf_counter()
    required_symbols = sorted({str(symbol) for symbol in symbols})
    if not required_symbols:
        raise RuntimeError("基本面准备缺少证券范围")
    if not requirements.metrics:
        raise RuntimeError("基本面准备缺少指标声明")

    conn = _connect(store_path)
    try:
        before = _load_values(conn, requirements, required_symbols, report_period)
        missing_before = _missing_pairs(before, requirements, required_symbols)
        reused = len(required_symbols) * len(requirements.metrics) - len(missing_before)
        refreshed = 0
        external_calls = 0
        if missing_before:
            missing_symbols = sorted({symbol for symbol, _ in missing_before})
            missing_metrics = sorted({metric for _, metric in missing_before})
            external_calls = len(missing_symbols)
            fetched = fetcher(missing_symbols, report_period, missing_metrics)
            facts = _normalize_facts(
                fetched,
                requirements,
                missing_symbols,
                report_period,
            )
            refreshed = _upsert_values(conn, requirements, facts, report_period)

        complete = _load_values(conn, requirements, required_symbols, report_period)
        missing_after = _missing_pairs(complete, requirements, required_symbols)
        if missing_after:
            sample = ", ".join(f"{symbol}/{metric}" for symbol, metric in missing_after[:10])
            raise RuntimeError(f"基本面关键字段缺失: {sample}")

        mode = "cache_hit"
        if missing_before:
            mode = "cold_build" if reused == 0 else "incremental"
        elapsed = int((perf_counter() - started) * 1000)
        metadata = PreparationMetadata(
            effective_date=effective_date.isoformat(),
            mode=mode,
            reused_records=reused,
            refreshed_records=refreshed,
            external_calls=external_calls,
            reused_fundamentals=reused,
            refreshed_fundamentals=refreshed,
            missing_fundamentals=len(missing_after),
            stage_timings_ms={"fundamentals": elapsed, "total": elapsed},
        )
        frame = _to_frame(complete, requirements, required_symbols, report_period)
        return FundamentalResult(
            frame=frame,
            metadata=metadata,
            input_fingerprint=stable_fingerprint(frame.to_dict("records")),
        )
    finally:
        conn.close()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamentals (
            source TEXT NOT NULL,
            source_version TEXT NOT NULL,
            caliber TEXT NOT NULL,
            symbol TEXT NOT NULL,
            report_period TEXT NOT NULL,
            metric TEXT NOT NULL,
            value_json TEXT NOT NULL,
            PRIMARY KEY (
                source, source_version, caliber, symbol, report_period, metric
            )
        )
        """
    )
    conn.commit()
    return conn


def _load_values(
    conn: sqlite3.Connection,
    requirements: FundamentalRequirements,
    symbols: Sequence[str],
    report_period: str,
) -> dict[tuple[str, str], object]:
    rows = conn.execute(
        """
        SELECT symbol, metric, value_json
        FROM fundamentals
        WHERE source=? AND source_version=? AND caliber=? AND report_period=?
        """,
        (
            requirements.source,
            requirements.source_version,
            requirements.caliber,
            report_period,
        ),
    ).fetchall()
    allowed_symbols = set(symbols)
    allowed_metrics = set(requirements.metrics)
    values: dict[tuple[str, str], object] = {}
    for row in rows:
        symbol = str(row["symbol"])
        metric = str(row["metric"])
        if symbol not in allowed_symbols or metric not in allowed_metrics:
            continue
        try:
            value = json.loads(row["value_json"])
        except json.JSONDecodeError:
            continue
        if _missing_value(value):
            continue
        values[(symbol, metric)] = value
    return values


def _missing_pairs(
    values: dict[tuple[str, str], object],
    requirements: FundamentalRequirements,
    symbols: Sequence[str],
) -> list[tuple[str, str]]:
    return [
        (symbol, metric)
        for symbol in symbols
        for metric in requirements.metrics
        if (symbol, metric) not in values
    ]


def _normalize_facts(
    frame: pd.DataFrame,
    requirements: FundamentalRequirements,
    symbols: Sequence[str],
    report_period: str,
) -> list[dict]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    required_columns = {requirements.symbol_field, "report_period", *requirements.metrics}
    missing = sorted(required_columns - set(frame.columns))
    if missing:
        raise RuntimeError(f"基本面响应缺少字段: {', '.join(missing)}")
    allowed_symbols = set(symbols)
    facts: list[dict] = []
    for raw in frame.to_dict("records"):
        symbol = str(raw[requirements.symbol_field])
        actual_period = str(raw["report_period"])[:10]
        if symbol not in allowed_symbols:
            continue
        if actual_period != report_period:
            raise RuntimeError(
                f"基本面报告期不一致: {symbol} 返回 {actual_period}，期望 {report_period}"
            )
        for metric in requirements.metrics:
            value = _json_value(raw.get(metric))
            if _missing_value(value):
                continue
            facts.append({"symbol": symbol, "metric": metric, "value": value})
    return facts


def _upsert_values(
    conn: sqlite3.Connection,
    requirements: FundamentalRequirements,
    facts: Sequence[dict],
    report_period: str,
) -> int:
    if not facts:
        return 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for fact in facts:
            conn.execute(
                """
                INSERT INTO fundamentals(
                    source, source_version, caliber, symbol, report_period, metric, value_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_version, caliber, symbol, report_period, metric)
                DO UPDATE SET value_json=excluded.value_json
                """,
                (
                    requirements.source,
                    requirements.source_version,
                    requirements.caliber,
                    fact["symbol"],
                    report_period,
                    fact["metric"],
                    json.dumps(fact["value"], ensure_ascii=False),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(facts)


def _to_frame(
    values: dict[tuple[str, str], object],
    requirements: FundamentalRequirements,
    symbols: Sequence[str],
    report_period: str,
) -> pd.DataFrame:
    rows = []
    for symbol in symbols:
        row = {
            requirements.symbol_field: symbol,
            "report_period": report_period,
        }
        row.update({metric: values[(symbol, metric)] for metric in requirements.metrics})
        rows.append(row)
    return pd.DataFrame(
        rows,
        columns=[requirements.symbol_field, "report_period", *requirements.metrics],
    )


def _missing_value(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _json_value(value: object) -> object:
    if _missing_value(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value
