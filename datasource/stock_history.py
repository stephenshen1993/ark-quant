"""Provider-specific adapters for underlying-stock price history."""

from __future__ import annotations

import logging
from datetime import date
from time import sleep

import pandas as pd

from datasource.market import stock_symbol_with_exchange
from datasource.market_history import MarketFetchResult


_RETRY_DELAYS_SECONDS = (0.5, 1.0)
_PROVIDER_LABELS = {
    "sina": "新浪",
    "tencent": "腾讯",
}


class StockHistoryProviderError(RuntimeError):
    """The selected provider could not return a valid price series."""


def fetch_stock_history_with_adjustment(
    ak,
    provider: str,
    stock_code: str,
    start: date,
    end: date,
) -> MarketFetchResult:
    """Fetch raw and hfq closes from one provider without cross-provider mixing."""
    normalized_provider = str(provider).strip().lower()
    if normalized_provider not in _PROVIDER_LABELS:
        raise ValueError(f"不支持的正股历史行情源: {provider}")
    adapter = _fetch_sina_history if normalized_provider == "sina" else _fetch_tencent_history

    external_calls = 0

    def _fetch(adjust: str) -> pd.DataFrame:
        nonlocal external_calls
        last_error: Exception | None = None
        for attempt in range(1, len(_RETRY_DELAYS_SECONDS) + 2):
            try:
                external_calls += 1
                return adapter(
                    ak,
                    stock_code,
                    start,
                    end,
                    adjust,
                )
            except Exception as exc:
                last_error = exc
                if attempt <= len(_RETRY_DELAYS_SECONDS):
                    logging.warning(
                        "%s %s历史行情%s第 %d 次失败，准备重试: %s",
                        stock_code,
                        _PROVIDER_LABELS[normalized_provider],
                        _adjustment_label(adjust),
                        attempt,
                        exc,
                    )
                    sleep(_RETRY_DELAYS_SECONDS[attempt - 1])

        assert last_error is not None
        raise StockHistoryProviderError(
            f"{stock_code} {_PROVIDER_LABELS[normalized_provider]}历史行情"
            f"{_adjustment_label(adjust)}连续 {len(_RETRY_DELAYS_SECONDS) + 1} 次失败: "
            f"{last_error}"
        ) from last_error

    raw = _fetch("")
    adjusted = _fetch("hfq")[["stock_code", "trade_date", "raw_close"]].rename(
        columns={"raw_close": "adjusted_close"}
    )
    result = raw.merge(adjusted, on=["stock_code", "trade_date"], how="left")
    result["adjustment_factor"] = result["adjusted_close"] / result["raw_close"]
    return MarketFetchResult(
        frame=result.drop(columns=["adjusted_close"]),
        external_calls=external_calls,
    )


def _fetch_sina_history(
    ak,
    stock_code: str,
    start: date,
    end: date,
    adjust: str,
) -> pd.DataFrame:
    symbol = stock_symbol_with_exchange(stock_code)
    frame = ak.stock_zh_a_daily(
        symbol=symbol,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust=adjust,
    )
    return _adapt_price_frame(
        frame,
        stock_code,
        provider_label="新浪",
        date_candidates=("date", "index"),
    )


def _fetch_tencent_history(
    ak,
    stock_code: str,
    start: date,
    end: date,
    adjust: str,
) -> pd.DataFrame:
    symbol = stock_symbol_with_exchange(stock_code)
    frame = ak.stock_zh_a_hist_tx(
        symbol=symbol,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust=adjust,
    )
    return _adapt_price_frame(
        frame,
        stock_code,
        provider_label="腾讯",
        date_candidates=("date",),
    )


def _adapt_price_frame(
    frame: pd.DataFrame,
    stock_code: str,
    *,
    provider_label: str,
    date_candidates: tuple[str, ...],
) -> pd.DataFrame:
    columns = ["stock_code", "trade_date", "raw_close"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    normalized = frame.reset_index().copy()
    date_col = next((name for name in date_candidates if name in normalized.columns), None)
    close_col = "close" if "close" in normalized.columns else None
    if date_col is None or close_col is None:
        raise RuntimeError(f"{provider_label}历史行情缺少日期或收盘价")
    result = pd.DataFrame({
        "stock_code": stock_code,
        "trade_date": pd.to_datetime(normalized[date_col], errors="coerce").dt.date,
        "raw_close": pd.to_numeric(normalized[close_col], errors="coerce"),
    }).dropna(subset=["trade_date", "raw_close"])
    if result.empty:
        raise RuntimeError(f"{provider_label}历史行情没有有效日期或收盘价")
    return result.drop_duplicates(subset=["stock_code", "trade_date"], keep="last")


def _adjustment_label(adjust: str) -> str:
    return "后复权" if adjust == "hfq" else "不复权"
