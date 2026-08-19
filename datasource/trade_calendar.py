"""交易时段判断，供各策略复用。"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CALENDAR_CACHE = ROOT / "data" / "cache" / "trade_calendar.csv"


def is_market_hours(
    now: datetime | None = None,
    block_start: time = time(9, 25),
    block_end: time = time(15, 10),
) -> bool:
    """Return True if current time is within the intraday blocked window (default 09:25–15:10)."""
    current = (now or datetime.now()).time()
    return block_start <= current < block_end


def enforce_snapshot_run_window(
    block_start: time = time(9, 25),
    block_end: time = time(15, 10),
    now: datetime | None = None,
) -> None:
    """Raise RuntimeError if the strategy is run during market hours.

    Strategies that use previous-close snapshot data must not run intraday.
    """
    if is_market_hours(now, block_start, block_end):
        raise RuntimeError(
            f"策略使用上一已完成交易日收盘数据，请在 {block_start:%H:%M} 前运行当日开盘建议，"
            f"或在 {block_end:%H:%M} 后运行下一交易日建议。"
        )


def resolve_effective_trading_date(
    *,
    now: datetime | None = None,
    trading_days: Iterable[date] | None = None,
    market_close: time = time(15, 10),
) -> date:
    """Freeze the last completed exchange trading date for one generation task."""
    current = now or datetime.now()
    days = sorted(set(trading_days or load_exchange_trading_days(as_of=current.date())))
    upper_bound = current.date()
    if current.time() < market_close:
        upper_bound -= timedelta(days=1)
    completed = [day for day in days if day <= upper_bound]
    if not completed:
        raise RuntimeError(f"交易日历不覆盖 {upper_bound.isoformat()} 之前的有效交易日")
    return completed[-1]


def load_exchange_trading_days(
    *,
    as_of: date | None = None,
    cache_path: Path | None = None,
    fetcher: Callable[[], Iterable[date]] | None = None,
) -> list[date]:
    """Load the exchange calendar lazily; importing or starting the app never fetches it."""
    target = as_of or date.today()
    path = cache_path or DEFAULT_CALENDAR_CACHE
    cached = _read_calendar(path)
    if _calendar_covers(cached, target):
        return cached

    provider = fetcher or _fetch_exchange_trading_days
    try:
        fetched = sorted(set(provider()))
    except Exception as exc:
        if _calendar_covers(cached, target):
            return cached
        if cached:
            raise RuntimeError(
                f"交易日历缓存不覆盖 {target.isoformat()}，且刷新失败：{exc}"
            ) from exc
        raise RuntimeError(f"无法获取交易日历：{exc}") from exc
    if not fetched:
        if _calendar_covers(cached, target):
            return cached
        if cached:
            raise RuntimeError(f"交易日历缓存不覆盖 {target.isoformat()}，且刷新结果为空")
        raise RuntimeError("交易日历为空，无法确定有效交易日")
    if not _calendar_covers(fetched, target):
        raise RuntimeError(f"交易日历刷新结果不覆盖 {target.isoformat()}")
    _write_calendar(path, fetched)
    return fetched


def _calendar_covers(trading_days: Iterable[date], target: date) -> bool:
    days = sorted(set(trading_days))
    if not days:
        return False
    required_horizon = target
    if target.weekday() == 5:
        required_horizon -= timedelta(days=1)
    elif target.weekday() == 6:
        required_horizon -= timedelta(days=2)
    return days[-1] >= required_horizon


def _fetch_exchange_trading_days() -> list[date]:
    import akshare as ak

    frame = ak.tool_trade_date_hist_sina()
    if "trade_date" not in frame.columns:
        raise RuntimeError("交易日历缺少 trade_date 字段")
    return [value.date() if hasattr(value, "date") else date.fromisoformat(str(value)[:10]) for value in frame["trade_date"]]


def _read_calendar(path: Path) -> list[date]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return sorted(
                date.fromisoformat(row["trade_date"])
                for row in csv.DictReader(handle)
                if row.get("trade_date")
            )
    except (OSError, ValueError, KeyError):
        return []


def _write_calendar(path: Path, trading_days: Iterable[date]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["trade_date"])
        writer.writeheader()
        writer.writerows({"trade_date": day.isoformat()} for day in trading_days)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
