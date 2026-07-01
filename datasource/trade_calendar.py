"""交易时段判断，供各策略复用。"""

from __future__ import annotations

from datetime import datetime, time


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
