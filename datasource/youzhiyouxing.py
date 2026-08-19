"""Fetch the Youzhiyouxing whole-market temperature."""
from __future__ import annotations

import fcntl
import math
import re
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DATA_URL = "https://youzhiyouxing.cn/data"
SHANGHAI = ZoneInfo("Asia/Shanghai")
CACHE_TTL = timedelta(hours=1)
OFFICIAL_SNAPSHOT_FALLBACK_TTL = timedelta(days=3)
FAILURE_RETRY_TTL = timedelta(minutes=5)
_REFRESH_LOCK = threading.Lock()


class TemperatureFetchError(RuntimeError):
    """Raised when the whole-market temperature cannot be fetched or parsed."""


@dataclass(frozen=True)
class MarketTemperature:
    temperature: float
    label: str | None
    updated_at: str
    source: str = DATA_URL
    fetched_at: str | None = None
    id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def get_or_fetch_market_temperature(
    *,
    refresh: bool = False,
    timeout: float = 10.0,
) -> MarketTemperature:
    """Return the latest saved temperature; fetch the official source only when needed."""
    with _REFRESH_LOCK:
        return _get_or_fetch_market_temperature(refresh=refresh, timeout=timeout)


def _get_or_fetch_market_temperature(*, refresh: bool, timeout: float) -> MarketTemperature:
    from datasource import db

    db.init_db()
    if not refresh:
        cached = _get_valid_cached_temperature(db)
        if cached and _is_fresh(cached.fetched_at):
            return cached

    lock = (
        nullcontext()
        if db._TEST_CONN is not None
        else _process_refresh_lock(db.DB_PATH)
    )
    with lock:
        if not refresh:
            cached = _get_valid_cached_temperature(db)
            if cached and _is_fresh(cached.fetched_at):
                return cached
            failed_attempt = db.get_market_temperature_refresh_state(DATA_URL)
            if failed_attempt and _is_recent(
                failed_attempt["last_attempt_at"], FAILURE_RETRY_TTL
            ):
                if _can_use_official_snapshot(cached):
                    return cached
                raise TemperatureFetchError(failed_attempt["last_error"])
        return _fetch_and_save_temperature(
            db,
            timeout,
            fallback=cached if not refresh else None,
        )


def _fetch_and_save_temperature(
    db,
    timeout: float,
    *,
    fallback: MarketTemperature | None = None,
) -> MarketTemperature:
    attempted_at = _storage_timestamp(_now_shanghai())
    try:
        fresh = _canonicalize_market_temperature(fetch_market_temperature(timeout=timeout))
    except TemperatureFetchError as exc:
        db.record_market_temperature_refresh_failure(DATA_URL, attempted_at, str(exc))
        if _can_use_official_snapshot(fallback):
            return fallback
        raise

    saved = db.insert_market_temperature(
        temperature=fresh.temperature,
        label=fresh.label,
        source_updated_at=fresh.updated_at,
        source=fresh.source,
        fetched_at=attempted_at,
    )
    if (
        float(saved["temperature"]) != fresh.temperature
        or saved.get("label") != fresh.label
    ):
        raise TemperatureFetchError(
            "market temperature source conflict: same source timestamp has changed data"
        )
    result = _canonicalize_market_temperature(_from_db_row(saved))
    db.clear_market_temperature_refresh_state(DATA_URL)
    return result


def _get_valid_cached_temperature(db) -> MarketTemperature | None:
    row = db.get_latest_market_temperature(source=DATA_URL)
    if not row:
        return None
    return _canonicalize_market_temperature(_from_db_row(row))


@contextmanager
def _process_refresh_lock(database_path: Path):
    lock_path = Path(f"{database_path}.temperature.lock")
    with lock_path.open("a") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def fetch_market_temperature(timeout: float = 10.0) -> MarketTemperature:
    req = Request(
        DATA_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise TemperatureFetchError(f"无法访问有知有行温度计: {exc}") from exc
    return parse_market_temperature(html)


def parse_market_temperature(html: str) -> MarketTemperature:
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    compact = re.sub(r"\s+", " ", text)

    updated_match = re.search(
        r"温度更新时间[:：]\s*(\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2})",
        compact,
    )
    if not updated_match:
        raise TemperatureFetchError("有知有行页面缺少温度更新时间")

    temp_match = re.search(r"全市场温度.*?([-+]?\d+(?:\.\d+)?)\s*°", compact)
    if not temp_match:
        raise TemperatureFetchError("有知有行页面缺少全市场温度")

    label_match = re.search(r"全市场温度.*?[-+]?\d+(?:\.\d+)?\s*°\s*([^<\s]+)", compact)
    updated_at = _parse_cn_datetime(updated_match.group(1))
    result = MarketTemperature(
        temperature=float(temp_match.group(1)),
        label=label_match.group(1) if label_match else None,
        updated_at=updated_at,
    )
    return _canonicalize_market_temperature(result)


def _parse_cn_datetime(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip())
    try:
        dt = datetime.strptime(normalized, "%Y年%m月%d日 %H:%M").replace(tzinfo=SHANGHAI)
    except ValueError as exc:
        raise TemperatureFetchError(f"有知有行温度更新时间无效: {value}") from exc
    return dt.replace(tzinfo=None).isoformat(timespec="minutes")


def _canonicalize_market_temperature(value: MarketTemperature) -> MarketTemperature:
    if value.source != DATA_URL:
        raise TemperatureFetchError("有知有行温度来源无效")
    try:
        temperature = float(value.temperature)
    except (TypeError, ValueError) as exc:
        raise TemperatureFetchError("有知有行全市场温度无效") from exc
    if not math.isfinite(temperature) or not 0 <= temperature <= 100:
        raise TemperatureFetchError("有知有行全市场温度必须在 0..100 范围内")
    try:
        parsed = datetime.fromisoformat(value.updated_at)
    except (TypeError, ValueError) as exc:
        raise TemperatureFetchError("有知有行温度更新时间无效") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    else:
        parsed = parsed.astimezone(SHANGHAI)
    updated_at = parsed.replace(
        tzinfo=None,
        second=0,
        microsecond=0,
    ).isoformat(timespec="minutes")
    return replace(value, temperature=temperature, updated_at=updated_at)


def _now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def _storage_timestamp(value: datetime) -> str:
    return value.astimezone(SHANGHAI).replace(tzinfo=None).isoformat(timespec="seconds")


def _is_fresh(fetched_at: str | None) -> bool:
    return _is_recent(fetched_at, CACHE_TTL)


def _can_use_official_snapshot(cached: MarketTemperature | None) -> bool:
    return bool(cached and _is_recent(cached.fetched_at, OFFICIAL_SNAPSHOT_FALLBACK_TTL))


def _is_recent(timestamp: str | None, ttl: timedelta) -> bool:
    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    else:
        parsed = parsed.astimezone(SHANGHAI)
    age = _now_shanghai() - parsed
    return timedelta(0) <= age < ttl


def _from_db_row(row: dict) -> MarketTemperature:
    return MarketTemperature(
        temperature=row["temperature"],
        label=row.get("label"),
        updated_at=row["source_updated_at"],
        source=row["source"],
        fetched_at=row.get("fetched_at"),
        id=row.get("id"),
    )
