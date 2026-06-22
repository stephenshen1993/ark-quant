from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

try:
    import numpy as np
    import pandas as pd
except ImportError as exc:
    raise SystemExit("缺少基础依赖。请先运行: python3 -m pip install -r requirements.txt") from exc

from datasource.market import (
    bond_symbol_with_exchange,
    fetch_stock_market_caps_tencent,
    normalize_stock_code,
    stock_symbol_with_exchange,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "cb_rotation.json"
DEFAULT_POSITIONS = ROOT / "portfolios" / "current_cb_positions.csv"
OUTPUT_DIR = ROOT / "outputs"
LOG_DIR = ROOT / "logs"
CACHE_DIR = ROOT / "data" / "cache"
RAW_DIR = ROOT / "data" / "raw"


@dataclass(frozen=True)
class RunArtifacts:
    candidates_csv: Path
    candidates_xlsx: Path
    rebalance_csv: Path
    report_md: Path


def setup_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"cb_rotation_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )
    return log_file


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def require_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "缺少依赖 akshare。请先运行: python3 -m pip install -r requirements.txt"
        ) from exc
    return ak


def cache_path(name: str, stamp: str | None = None) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = stamp or datetime.now().strftime("%Y%m%d")
    return CACHE_DIR / f"{name}_{suffix}.csv"


def latest_cache(name: str, max_age_days: int) -> Path | None:
    if not CACHE_DIR.exists():
        return None
    files = sorted(CACHE_DIR.glob(f"{name}_*.csv"), reverse=True)
    cutoff = date.today() - timedelta(days=max_age_days)
    for path in files:
        stamps = re.findall(r"(?<!\d)(20\d{6})(?!\d)", path.stem)
        cache_date = (
            datetime.strptime(stamps[-1], "%Y%m%d").date()
            if stamps
            else datetime.fromtimestamp(path.stat().st_mtime).date()
        )
        if cache_date >= cutoff:
            return path
    return None


def require_fresh_dates(df: pd.DataFrame, column: str, max_age_days: int, stage: str) -> None:
    if column not in df.columns:
        raise RuntimeError(f"{stage} 缺少数据日期字段 {column}。本次停止运行。")
    values = pd.to_datetime(df[column], errors="coerce")
    if values.isna().any():
        raise RuntimeError(f"{stage} 存在无法识别的数据日期。 本次停止运行。")
    cutoff = pd.Timestamp(date.today() - timedelta(days=max_age_days))
    stale = values < cutoff
    if stale.any():
        oldest = values.min().date()
        raise RuntimeError(f"{stage} 包含过期数据，最早日期为 {oldest}。本次停止运行。")


def require_single_trade_date(df: pd.DataFrame, column: str, stage: str) -> date:
    values = pd.to_datetime(df[column], errors="coerce")
    dates = sorted({value.date() for value in values.dropna()})
    if len(dates) != 1:
        raise RuntimeError(f"{stage} 必须使用同一个已完成交易日，当前包含: {dates}。本次停止运行。")
    return dates[0]


def enforce_snapshot_run_window(config: dict, now: datetime | None = None) -> None:
    data_config = config.get("data", {})
    if data_config.get("selection_data_mode", "previous_close") != "previous_close":
        raise RuntimeError("当前仅支持 previous_close 收盘口径。")
    if not data_config.get("block_intraday_runs", True):
        return
    current = (now or datetime.now()).time()
    market_open = datetime.strptime(data_config.get("intraday_block_start", "09:25"), "%H:%M").time()
    after_close = datetime.strptime(data_config.get("intraday_block_end", "15:10"), "%H:%M").time()
    if market_open <= current < after_close:
        raise RuntimeError(
            f"策略使用上一已完成交易日收盘数据，请在 {market_open:%H:%M} 前运行当日开盘建议，"
            f"或在 {after_close:%H:%M} 后运行下一交易日建议。"
        )


def merge_latest_rows(existing: pd.DataFrame, new: pd.DataFrame, key: str) -> pd.DataFrame:
    if existing.empty:
        return new.copy()
    if new.empty:
        return existing.copy()
    return pd.concat([existing, new], ignore_index=True).drop_duplicates(subset=[key], keep="last")


def fetch_with_cache(name: str, fetcher, config: dict) -> pd.DataFrame:
    try:
        df = fetcher()
        if isinstance(df, pd.DataFrame) and not df.empty:
            path = cache_path(name)
            df.to_csv(path, index=False, encoding="utf-8-sig")
            logging.info("Saved data cache: %s", path)
            return df
    except Exception as exc:
        logging.warning("Fetch failed for %s: %s", name, exc)

    data_config = config.get("data", {})
    if not data_config.get("use_cache_on_failure", True):
        raise RuntimeError(f"数据源 {name} 获取失败，且未启用缓存回退。")
    cached = latest_cache(name, int(data_config.get("max_cache_age_days", 7)))
    if cached is None:
        raise RuntimeError(f"数据源 {name} 获取失败，且没有可用缓存。")
    logging.warning("Using cached data for %s: %s", name, cached)
    return pd.read_csv(cached, dtype=str)


def first_existing_col(df: pd.DataFrame, names: Iterable[str]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for name in names:
        key = name.strip().lower()
        if key in lowered:
            return lowered[key]
    return None


def as_number(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    multiplier = pd.Series(1.0, index=series.index)
    multiplier = multiplier.mask(text.str.contains("万", na=False), 10_000.0)
    multiplier = multiplier.mask(text.str.contains("亿", na=False), 100_000_000.0)
    cleaned = (
        text.str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("亿元", "", regex=False)
        .str.replace("亿", "", regex=False)
        .str.replace("万元", "", regex=False)
        .str.replace("万", "", regex=False)
    )
    cleaned = cleaned.mask(text.isin(["", "-", "--", "nan", "None", "<NA>"]))
    return pd.to_numeric(cleaned, errors="coerce") * multiplier


def as_100m_units(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    cleaned = (
        text.str.replace(",", "", regex=False)
        .str.replace("亿元", "", regex=False)
        .str.replace("亿", "", regex=False)
        .str.replace("万元", "", regex=False)
        .str.replace("万", "", regex=False)
    )
    cleaned = cleaned.mask(text.isin(["", "-", "--", "nan", "None", "<NA>"]))
    value = pd.to_numeric(cleaned, errors="coerce")
    value = value.mask(text.str.contains("万", na=False), value / 10_000)
    return value


def as_yuan(series: pd.Series, column_name: str | None = None) -> pd.Series:
    value = as_number(series)
    name = column_name or ""
    if "万元" in name:
        return pd.to_numeric(series, errors="coerce") * 10_000
    if "亿元" in name:
        return pd.to_numeric(series, errors="coerce") * 100_000_000
    return value


def as_turnover_yuan(series: pd.Series, column_name: str | None = None) -> pd.Series:
    value = as_yuan(series, column_name)
    name = column_name or ""
    if name == "成交额" and value.max(skipna=True) < 10_000_000:
        return value * 10_000
    return value


def get_col(df: pd.DataFrame, candidates: list[str], target: str, required: bool = False) -> pd.Series:
    col = first_existing_col(df, candidates)
    if col is None:
        message = f"缺少字段 {target}，候选列: {candidates}"
        if required:
            raise ValueError(message)
        logging.warning("%s；该字段相关过滤会跳过。", message)
        return pd.Series(pd.NA, index=df.index, name=target)
    return df[col]


def assert_required_fields(df: pd.DataFrame, fields: Iterable[str], stage: str, require_all_rows: bool = False) -> None:
    missing = [field for field in fields if field not in df.columns or not df[field].notna().any()]
    if missing:
        raise RuntimeError(f"{stage} 缺少原策略必需字段: {missing}。本次停止运行，避免静默放宽策略。")
    if require_all_rows:
        incomplete = [field for field in fields if df[field].isna().any()]
        if incomplete:
            counts = {field: int(df[field].isna().sum()) for field in incomplete}
            raise RuntimeError(
                f"{stage} 存在未覆盖全部候选的原策略必需字段: {counts}。"
                "本次停止运行，避免用缺失值参与排序。"
            )


def fetch_cb_universe(ak, config: dict) -> pd.DataFrame:
    errors: list[str] = []
    for func_name in ("bond_zh_cov", "bond_cb_jsl"):
        if not hasattr(ak, func_name):
            continue
        try:
            logging.info("Fetching convertible bond universe with ak.%s()", func_name)
            raw = fetch_with_cache(func_name, getattr(ak, func_name), config)
            if isinstance(raw, pd.DataFrame) and not raw.empty:
                raw.attrs["source_func"] = func_name
                return raw
        except Exception as exc:  # AKShare endpoints can change or throttle.
            errors.append(f"{func_name}: {exc}")
            logging.warning("Failed to fetch with %s: %s", func_name, exc)
    raise RuntimeError("无法获取可转债全市场数据: " + " | ".join(errors))


def enrich_cb_with_redeem_data(ak, cb: pd.DataFrame, config: dict) -> pd.DataFrame:
    try:
        logging.info("Fetching redeem risk data with ak.bond_cb_redeem_jsl()")
        raw = fetch_with_cache("bond_cb_redeem_jsl", ak.bond_cb_redeem_jsl, config)
    except Exception as exc:
        logging.warning("Failed to fetch redeem data: %s", exc)
        return cb
    if raw.empty:
        return cb

    redeem = pd.DataFrame(index=raw.index)
    redeem["bond_code"] = get_col(raw, ["代码", "债券代码"], "bond_code", True).astype(str).str.zfill(6)
    redeem["remaining_size_100m_redeem"] = as_100m_units(get_col(raw, ["剩余规模"], "remaining_size_100m"))
    redeem["maturity_date_redeem"] = pd.to_datetime(
        get_col(raw, ["到期日", "到期时间", "到期日期"], "maturity_date"),
        errors="coerce",
    ).dt.date
    redeem["call_status_redeem"] = (
        get_col(raw, ["强赎状态"], "call_status")
        .replace({"<NA>": pd.NA, "nan": pd.NA})
        .fillna("无强赎提示")
        .astype(str)
    )
    redeem["active_reference"] = True
    redeem = redeem.drop_duplicates(subset=["bond_code"])

    merged = cb.merge(redeem, on="bond_code", how="left")
    merged["remaining_size_100m"] = merged["remaining_size_100m_redeem"].combine_first(
        merged["remaining_size_100m"]
    )
    merged["maturity_date"] = merged["maturity_date"].combine_first(merged["maturity_date_redeem"])
    merged["call_status"] = merged["call_status"].replace({"<NA>": pd.NA, "nan": pd.NA}).combine_first(
        merged["call_status_redeem"]
    )
    return merged.drop(columns=["remaining_size_100m_redeem", "maturity_date_redeem", "call_status_redeem"])


def normalize_cb_data(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(index=raw.index)
    df["bond_code"] = get_col(raw, ["债券代码", "转债代码", "代码", "bond_id"], "bond_code", True).astype(str).str.zfill(6)
    df["bond_name"] = get_col(raw, ["债券简称", "转债名称", "名称", "bond_nm"], "bond_name", True).astype(str)
    df["stock_code"] = (
        get_col(raw, ["正股代码", "stock_id", "stock_code"], "stock_code")
        .astype(str)
        .str.extract(r"(\d{6})", expand=False)
        .str.zfill(6)
    )
    df["stock_name"] = get_col(raw, ["正股简称", "正股名称", "stock_nm", "stock_name"], "stock_name").astype(str)
    df["cb_price"] = as_number(get_col(raw, ["债现价", "现价", "最新价", "转债最新价", "price"], "cb_price", True))
    df["premium_rate"] = as_number(get_col(raw, ["转股溢价率", "溢价率", "premium_rt"], "premium_rate", True))
    remaining_col = first_existing_col(raw, ["剩余规模", "债券余额", "余额", "remain_size"])
    if remaining_col is None:
        logging.warning("缺少字段 remaining_size_100m；该字段相关过滤会跳过。")
        df["remaining_size_100m"] = pd.NA
    else:
        df["remaining_size_100m"] = as_100m_units(raw[remaining_col])
    turnover_col = first_existing_col(raw, ["成交额", "成交额(元)", "成交额(万元)", "amount"])
    if turnover_col is None:
        logging.warning("缺少字段 turnover_yuan；该字段相关过滤会跳过。")
        df["turnover_yuan"] = pd.NA
    else:
        df["turnover_yuan"] = as_turnover_yuan(raw[turnover_col], str(turnover_col))
    df["maturity_date"] = pd.to_datetime(
        get_col(raw, ["到期时间", "到期日期", "maturity_dt"], "maturity_date"),
        errors="coerce",
    ).dt.date
    df["listing_date"] = pd.to_datetime(
        get_col(raw, ["上市时间", "上市日期", "list_dt"], "listing_date"),
        errors="coerce",
    ).dt.date
    df["remaining_years"] = as_number(get_col(raw, ["剩余年限"], "remaining_years"))
    df["call_status"] = get_col(raw, ["强赎状态", "强赎", "redeem_flag", "redeem_status"], "call_status").astype(str)
    return df.drop_duplicates(subset=["bond_code"])


def fetch_cb_daily_turnover(ak, bond_codes: Iterable[str], config: dict) -> pd.DataFrame:
    codes = sorted({str(c).zfill(6) for c in bond_codes if pd.notna(c)})
    name = f"bond_daily_turnover_{date.today():%Y%m%d}"
    cached_today = cache_path(name, stamp="latest")
    if cached_today.exists():
        cached = pd.read_csv(cached_today, dtype={"bond_code": str})
        cached_codes = set(cached["bond_code"].astype(str).str.zfill(6))
        if set(codes).issubset(cached_codes):
            logging.info("Using same-day bond turnover cache: %s", cached_today)
            return cached[cached["bond_code"].astype(str).str.zfill(6).isin(codes)].copy()

    rows: list[dict] = []
    cached_codes: set[str] = set()
    if cached_today.exists():
        cached = pd.read_csv(cached_today, dtype={"bond_code": str})
        cached["bond_code"] = cached["bond_code"].astype(str).str.zfill(6)
        cached_codes = set(cached["bond_code"])
        rows.extend(cached[cached["bond_code"].isin(codes)].to_dict("records"))

    missing_codes = [code for code in codes if code not in cached_codes]
    failure_count = 0
    max_failures = int(config.get("data", {}).get("max_per_symbol_fetch_failures", 10))
    for i, code in enumerate(missing_codes, start=1):
        try:
            hist = ak.bond_zh_hs_cov_daily(symbol=bond_symbol_with_exchange(code))
            if hist.empty:
                continue
            latest = hist.tail(1).iloc[0]
            close = pd.to_numeric(latest.get("close"), errors="coerce")
            volume = pd.to_numeric(latest.get("volume"), errors="coerce")
            rows.append(
                {
                    "bond_code": code,
                    "cb_close_daily": close,
                    "turnover_yuan_daily": close * volume,
                    "turnover_trade_date": latest.get("date"),
                }
            )
            if i % 50 == 0:
                logging.info("Fetched bond daily turnover for %s symbols.", i)
        except Exception as exc:
            logging.warning("Failed to fetch bond daily turnover for %s: %s", code, exc)
            failure_count += 1
            if failure_count >= max_failures:
                logging.warning("Stop fetching bond daily turnover after %s failures.", failure_count)
                break

    turnover = pd.DataFrame(
        rows,
        columns=["bond_code", "cb_close_daily", "turnover_yuan_daily", "turnover_trade_date"],
    )
    turnover = turnover.drop_duplicates(subset=["bond_code"], keep="last")
    if not turnover.empty:
        turnover.to_csv(cached_today, index=False, encoding="utf-8-sig")
        logging.info("Saved bond turnover cache: %s", cached_today)
        return turnover

    data_config = config.get("data", {})
    if data_config.get("use_cache_on_failure", True):
        cached = latest_cache("bond_daily_turnover", int(data_config.get("max_cache_age_days", 7)))
        if cached is not None:
            logging.warning("Using cached bond turnover: %s", cached)
            return pd.read_csv(cached, dtype={"bond_code": str})
    return turnover


def enrich_cb_with_daily_market_data(ak, cb: pd.DataFrame, config: dict) -> pd.DataFrame:
    turnover = fetch_cb_daily_turnover(ak, cb["bond_code"], config)
    if turnover.empty:
        return cb
    if config.get("data", {}).get("strict_original_rules", True):
        assert_required_fields(
            turnover,
            ["cb_close_daily", "turnover_yuan_daily", "turnover_trade_date"],
            "可转债收盘行情",
            require_all_rows=True,
        )
    merged = cb.merge(turnover, on="bond_code", how="left")
    if "cb_close_daily" not in merged.columns:
        merged["cb_close_daily"] = pd.NA
    # The strategy selects after close and trades on the next open.
    merged["cb_price"] = merged["cb_close_daily"].where(merged["cb_close_daily"].notna(), merged["cb_price"])
    merged["turnover_yuan"] = merged["turnover_yuan_daily"].where(
        merged["turnover_yuan_daily"].notna(), merged["turnover_yuan"]
    )
    return merged.drop(columns=["cb_close_daily", "turnover_yuan_daily"])


def fetch_stock_factors(ak, stock_codes: Iterable[str], end: date, config: dict) -> pd.DataFrame:
    start = end - timedelta(days=90)
    rows: list[dict] = []
    failure_count = 0
    max_failures = int(config.get("data", {}).get("max_per_symbol_fetch_failures", 10))
    for i, code in enumerate(sorted({normalize_stock_code(c) for c in stock_codes if pd.notna(c)}), start=1):
        if not code:
            continue
        try:
            try:
                hist = ak.stock_zh_a_daily(
                    symbol=stock_symbol_with_exchange(code),
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq",
                )
            except Exception:
                hist = ak.stock_zh_a_hist_tx(
                    symbol=stock_symbol_with_exchange(code),
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq",
                )
            close_col = first_existing_col(hist, ["收盘", "close"])
            if close_col is None or len(hist) < 21:
                logging.warning("Stock %s history is insufficient; skipping factors.", code)
                continue
            close = pd.to_numeric(hist[close_col], errors="coerce").dropna()
            if len(close) < 21:
                continue
            ret = close.pct_change().dropna()
            trade_date_col = first_existing_col(hist, ["日期", "date"])
            trade_date = hist.iloc[-1][trade_date_col] if trade_date_col is not None else hist.index[-1]
            share_col = first_existing_col(hist, ["outstanding_share"])
            market_cap_estimate = np.nan
            if share_col is not None:
                shares = pd.to_numeric(hist[share_col], errors="coerce").dropna()
                if not shares.empty:
                    market_cap_estimate = close.iloc[-1] * shares.iloc[-1]
            rows.append(
                {
                    "stock_code": code,
                    "stock_momentum_20d": close.iloc[-1] / close.iloc[-21] - 1,
                    "stock_volatility_20d": ret.tail(20).std() * np.sqrt(252),
                    "market_cap_estimate": market_cap_estimate,
                    "stock_factor_trade_date": trade_date,
                }
            )
            if i % 50 == 0:
                logging.info("Fetched stock history for %s symbols.", i)
        except Exception as exc:
            logging.warning("Failed to fetch stock history for %s: %s", code, exc)
            failure_count += 1
            if failure_count >= max_failures:
                logging.warning("Stop fetching stock history after %s failures.", failure_count)
                break
    return pd.DataFrame(
        rows,
        columns=[
            "stock_code",
            "stock_momentum_20d",
            "stock_volatility_20d",
            "market_cap_estimate",
            "stock_factor_trade_date",
        ],
    )


def fetch_stock_factors_with_cache(ak, stock_codes: Iterable[str], end: date, config: dict) -> pd.DataFrame:
    codes = sorted({normalize_stock_code(c) for c in stock_codes if pd.notna(c)})
    name = f"stock_factors_{end:%Y%m%d}"
    cached_today = cache_path(name, stamp="latest")
    if cached_today.exists():
        cached = pd.read_csv(cached_today, dtype={"stock_code": str})
        cached["stock_code"] = cached["stock_code"].astype(str).str.zfill(6)
        cached_codes = set(cached["stock_code"].astype(str).str.zfill(6))
        if set(codes).issubset(cached_codes):
            logging.info("Using same-day stock factor cache: %s", cached_today)
            return cached[cached["stock_code"].astype(str).str.zfill(6).isin(codes)].copy()

    cached = pd.DataFrame()
    if cached_today.exists():
        cached = pd.read_csv(cached_today, dtype={"stock_code": str})
        cached["stock_code"] = cached["stock_code"].astype(str).str.zfill(6)

    cached_codes = set(cached["stock_code"]) if not cached.empty else set()
    factors = fetch_stock_factors(ak, [code for code in codes if code not in cached_codes], end, config)
    factors = merge_latest_rows(cached, factors, "stock_code")
    if not factors.empty:
        factors.to_csv(cached_today, index=False, encoding="utf-8-sig")
        logging.info("Saved stock factor cache: %s", cached_today)
        return factors

    data_config = config.get("data", {})
    if data_config.get("use_cache_on_failure", True):
        cached = latest_cache("stock_factors", int(data_config.get("max_cache_age_days", 7)))
        if cached is not None:
            logging.warning("Using cached stock factors: %s", cached)
            factors = pd.read_csv(cached, dtype={"stock_code": str})
            factors["stock_code"] = factors["stock_code"].astype(str).str.zfill(6)
            return factors
    return factors


def fetch_stock_market_caps_close_snapshot(ak, stock_codes: Iterable[str]) -> pd.DataFrame:
    codes = sorted({normalize_stock_code(c) for c in stock_codes if pd.notna(c)})
    raw = ak.stock_zh_a_spot_em()
    code_col = first_existing_col(raw, ["代码", "stock_code"])
    market_cap_col = first_existing_col(raw, ["总市值", "market_cap"])
    name_col = first_existing_col(raw, ["名称", "股票简称", "stock_name"])
    if code_col is None or market_cap_col is None:
        raise RuntimeError("AKShare A股收盘快照缺少代码或总市值字段。")

    caps = pd.DataFrame(index=raw.index)
    caps["stock_code"] = raw[code_col].astype(str).str.extract(r"(\d{6})", expand=False).str.zfill(6)
    caps["market_cap"] = as_number(raw[market_cap_col])
    caps["stock_name_spot"] = raw[name_col].astype(str) if name_col is not None else pd.NA
    caps["industry"] = pd.NA
    caps["market_cap_as_of_date"] = date.today().isoformat()
    caps["market_cap_source"] = "eastmoney_total_mv"
    caps = caps.dropna(subset=["stock_code", "market_cap"]).drop_duplicates(subset=["stock_code"])
    return caps[caps["stock_code"].isin(codes)].copy()


def fetch_stock_market_caps(ak, stock_codes: Iterable[str], config: dict) -> pd.DataFrame:
    codes = sorted({normalize_stock_code(c) for c in stock_codes if pd.notna(c)})
    name = f"stock_market_caps_{date.today():%Y%m%d}"
    cached_today = cache_path(name, stamp="latest")
    rows: list[dict] = []
    cached_codes: set[str] = set()
    if cached_today.exists():
        cached = pd.read_csv(cached_today, dtype={"stock_code": str})
        cached["stock_code"] = cached["stock_code"].astype(str).str.zfill(6)
        cached_codes = set(cached["stock_code"])
        if set(codes).issubset(cached_codes):
            logging.info("Using same-day stock market cap cache: %s", cached_today)
            return cached[cached["stock_code"].astype(str).str.zfill(6).isin(codes)].copy()
        rows.extend(cached[cached["stock_code"].isin(codes)].to_dict("records"))

    # Primary source: Tencent quote API (eastmoney-free, login-free, batched).
    try:
        logging.info("Fetching stock total market caps from Tencent quote API")
        tencent_caps = fetch_stock_market_caps_tencent(codes)
        if not tencent_caps.empty:
            tencent_codes = set(tencent_caps["stock_code"])
            rows = [row for row in rows if row.get("stock_code") not in tencent_codes]
            rows.extend(tencent_caps.to_dict("records"))
            cached_codes = cached_codes | tencent_codes
            logging.info("Tencent market cap coverage: %s/%s", len(tencent_codes), len(codes))
    except Exception as exc:
        logging.warning("Failed to fetch stock market caps from Tencent: %s", exc)

    # Fallback source: eastmoney after-close snapshot (often blocked for Python requests).
    if any(code not in cached_codes for code in codes):
        try:
            logging.info("Fetching remaining stock total market caps from the after-close A-share snapshot")
            snapshot_caps = fetch_stock_market_caps_close_snapshot(ak, codes)
            if not snapshot_caps.empty:
                snapshot_codes = set(snapshot_caps["stock_code"])
                rows = [row for row in rows if row.get("stock_code") not in snapshot_codes]
                rows.extend(snapshot_caps.to_dict("records"))
                cached_codes = cached_codes | snapshot_codes
        except Exception as exc:
            logging.warning("Failed to fetch stock market caps from A-share close snapshot: %s", exc)

    failure_count = 0
    max_failures = int(config.get("data", {}).get("max_market_cap_fetch_failures", 10))
    missing_codes = [code for code in codes if code not in cached_codes]
    for i, code in enumerate(missing_codes, start=1):
        if not code:
            continue
        last_error: Exception | None = None
        for _ in range(3):
            try:
                raw = ak.stock_individual_info_em(symbol=code, timeout=10)
                info = dict(zip(raw["item"], raw["value"]))
                rows.append(
                    {
                        "stock_code": code,
                        "stock_name_spot": info.get("股票简称"),
                        "market_cap": pd.to_numeric(info.get("总市值"), errors="coerce"),
                        "industry": info.get("行业"),
                        "market_cap_as_of_date": date.today().isoformat(),
                        "market_cap_source": "eastmoney_total_mv",
                    }
                )
                break
            except Exception as exc:
                last_error = exc
        else:
            failure_count += 1
            logging.warning("Failed to fetch total market cap for %s: %s", code, last_error)
            if failure_count >= max_failures:
                logging.warning("Stop fetching total market caps after %s failures.", failure_count)
                break
        if i % 50 == 0:
            logging.info("Fetched total market cap for %s symbols.", i)

    caps = pd.DataFrame(
        rows,
        columns=[
            "stock_code",
            "stock_name_spot",
            "market_cap",
            "industry",
            "market_cap_as_of_date",
            "market_cap_source",
        ],
    )
    if not caps.empty:
        caps["stock_code"] = caps["stock_code"].astype(str).str.zfill(6)
        caps = caps.drop_duplicates(subset=["stock_code"], keep="last")
        caps.to_csv(cached_today, index=False, encoding="utf-8-sig")
        logging.info("Saved stock market cap cache: %s", cached_today)
        return caps

    data_config = config.get("data", {})
    if data_config.get("use_cache_on_failure", True):
        cached = latest_cache("stock_market_caps", int(data_config.get("max_cache_age_days", 7)))
        if cached is not None:
            logging.warning("Using cached stock market caps: %s", cached)
            caps = pd.read_csv(cached, dtype={"stock_code": str})
            caps["stock_code"] = caps["stock_code"].astype(str).str.zfill(6)
            return caps
    return caps


MARKET_CAP_ESTIMATE_SOURCE = "stock_history_close_x_outstanding_share"


def apply_market_cap_estimate(merged: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Fill 正股总市值 from the float-market-cap estimate when the total-cap source is down.

    The estimate is 收盘价 × 流通股 computed from the (working) sina stock history. It is a
    流通市值 proxy for the small-cap factor, used only when the eastmoney 总市值 source fails or
    returns staler data than the estimate. The fresh estimate is preferred over any stale
    cross-day cached cap, and the filled rows are dated and labeled so strict freshness checks
    still apply. No-op unless `data.allow_market_cap_estimate` is true.
    """
    if not config.get("data", {}).get("allow_market_cap_estimate", False):
        return merged
    if "market_cap_estimate" not in merged.columns:
        return merged
    if "market_cap" not in merged.columns:
        merged["market_cap"] = pd.NA
    if "market_cap_as_of_date" not in merged.columns:
        merged["market_cap_as_of_date"] = pd.NA
    if "market_cap_source" not in merged.columns:
        merged["market_cap_source"] = pd.NA

    estimate = merged["market_cap_estimate"]
    cap_date = pd.to_datetime(merged["market_cap_as_of_date"], errors="coerce")
    estimate_date = pd.to_datetime(merged["stock_factor_trade_date"], errors="coerce")
    # Use the estimate when the real cap is missing, undated, or staler than the estimate.
    use_estimate = estimate.notna() & (
        merged["market_cap"].isna() | cap_date.isna() | (estimate_date > cap_date)
    )
    merged.loc[use_estimate, "market_cap"] = estimate[use_estimate]
    merged.loc[use_estimate, "market_cap_as_of_date"] = merged.loc[use_estimate, "stock_factor_trade_date"]
    merged.loc[use_estimate, "market_cap_source"] = MARKET_CAP_ESTIMATE_SOURCE
    filled = int(use_estimate.sum())
    if filled:
        logging.warning(
            "正股总市值回退使用流通市值估算(收盘价×流通股)填充 %s 只，口径为流通市值而非总市值。", filled
        )
    return merged


def build_data_notes(df: pd.DataFrame, config: dict) -> list[str]:
    notes: list[str] = ["选债使用最近已完成交易日收盘数据，调仓建议用于下一交易日开盘执行。"]
    top_n = config["top_n"]
    if top_n != 15:
        notes.append(f"持仓数量按当前配置使用 Top{top_n}。")
    if "turnover_yuan" not in df.columns or not df["turnover_yuan"].notna().any():
        notes.append("日成交额字段缺失，本次未严格执行 日成交额 > 3000 万 过滤。")
    if "market_cap" in df.columns and df["market_cap"].notna().any():
        source = df["market_cap_source"] if "market_cap_source" in df.columns else pd.Series(pd.NA, index=df.index)
        estimate_count = int(source.fillna("").str.contains("outstanding_share").sum())
        total = int(df["market_cap"].notna().sum())
        if estimate_count == total:
            notes.append("正股市值本次全部使用流通市值估算(收盘价×流通股)，非总市值，小市值因子口径存在偏差。")
        elif estimate_count > 0:
            notes.append(
                f"正股市值部分使用流通市值估算({estimate_count}/{total} 只)，其余为总市值快照，小市值因子口径不完全一致。"
            )
        else:
            notes.append("正股市值使用 A 股行情快照或个股信息中的总市值字段。")
    else:
        notes.append("正股市值字段缺失，小市值因子本次可能失效。")
    if "call_status" not in df.columns or not df["call_status"].notna().any():
        notes.append("强赎状态字段缺失，本次强赎过滤可能不完整。")
    return notes


def enforce_original_rule_fields(df: pd.DataFrame, config: dict) -> None:
    if not config.get("data", {}).get("strict_original_rules", True):
        return
    assert_required_fields(
        df,
        ["cb_price", "premium_rate", "remaining_size_100m", "maturity_date", "call_status"],
        "可转债过滤",
        require_all_rows=True,
    )


def enforce_cb_filter_coverage(df: pd.DataFrame, config: dict) -> None:
    if not config.get("data", {}).get("strict_original_rules", True):
        return
    filters = config["filters"]
    listed = pd.to_datetime(df["listing_date"], errors="coerce") <= pd.Timestamp(date.today())
    active = df["active_reference"].eq(True) if "active_reference" in df.columns else False
    potential = df.loc[active & listed & df["cb_price"].notna() & (df["cb_price"] < filters["max_cb_price"])].copy()
    assert_required_fields(
        potential,
        ["premium_rate", "remaining_size_100m", "maturity_date", "call_status"],
        "当前已上市可转债过滤",
        require_all_rows=True,
    )


def enforce_factor_fields(df: pd.DataFrame, config: dict) -> None:
    if not config.get("data", {}).get("strict_original_rules", True):
        return
    assert_required_fields(
        df,
        ["stock_momentum_20d", "stock_volatility_20d", "market_cap"],
        "因子计算",
        require_all_rows=True,
    )


def apply_cb_prefilters(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    filters = config["filters"]
    mask = pd.Series(True, index=df.index)
    if config.get("data", {}).get("strict_original_rules", True):
        mask &= df["active_reference"].eq(True)
    mask &= pd.to_datetime(df["listing_date"], errors="coerce") <= pd.Timestamp(date.today())
    mask &= df["cb_price"] < filters["max_cb_price"]
    if df["remaining_size_100m"].notna().any():
        mask &= df["remaining_size_100m"] > filters["min_remaining_size_100m"]
    if df["remaining_years"].notna().any():
        mask &= df["remaining_years"] > filters["min_years_to_maturity"]
    elif df["maturity_date"].notna().any():
        min_maturity = pd.Timestamp(date.today() + timedelta(days=365 * filters["min_years_to_maturity"]))
        mask &= pd.to_datetime(df["maturity_date"], errors="coerce") > min_maturity
    if filters.get("exclude_call_risk", True) and df["call_status"].notna().any():
        risk_words = (
            "已满足",
            "满足强赎",
            "强赎公告",
            "公告强赎",
            "公告要强赎",
            "已公告强赎",
            "即将强赎",
            "强赎中",
            "赎回登记",
            "最后交易",
            "停止交易",
        )
        mask &= ~df["call_status"].fillna("").str.contains("|".join(risk_words), regex=True)
    if filters.get("exclude_st_stock", True):
        mask &= ~df["stock_name"].fillna("").str.upper().str.contains("ST", regex=False)
    filtered = df.loc[mask].copy()
    logging.info("CB prefiltered universe: %s -> %s", len(df), len(filtered))
    return filtered


def apply_filters(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    filters = config["filters"]
    mask = pd.Series(True, index=df.index)
    mask &= df["cb_price"] < filters["max_cb_price"]

    if df["remaining_size_100m"].notna().any():
        mask &= df["remaining_size_100m"] > filters["min_remaining_size_100m"]
    if df["turnover_yuan"].notna().any():
        mask &= df["turnover_yuan"] > filters["min_turnover_yuan"]
    if df["remaining_years"].notna().any():
        mask &= df["remaining_years"] > filters["min_years_to_maturity"]
    elif df["maturity_date"].notna().any():
        min_maturity = pd.Timestamp(date.today() + timedelta(days=365 * filters["min_years_to_maturity"]))
        mask &= pd.to_datetime(df["maturity_date"], errors="coerce") > min_maturity
    if filters.get("exclude_call_risk", True) and df["call_status"].notna().any():
        risk_words = (
            "已满足",
            "满足强赎",
            "强赎公告",
            "公告强赎",
            "公告要强赎",
            "已公告强赎",
            "即将强赎",
            "强赎中",
            "赎回登记",
            "最后交易",
            "停止交易",
        )
        mask &= ~df["call_status"].fillna("").str.contains("|".join(risk_words), regex=True)
    if filters.get("exclude_st_stock", True):
        stock_name_spot = df["stock_name_spot"] if "stock_name_spot" in df.columns else pd.Series(pd.NA, index=df.index)
        name = stock_name_spot.fillna(df["stock_name"]).fillna("")
        mask &= ~name.str.upper().str.contains("ST", regex=False)

    filtered = df.loc[mask].copy()
    logging.info("Filtered universe: %s -> %s", len(df), len(filtered))
    return filtered


def rank_score(series: pd.Series, higher_is_better: bool) -> pd.Series:
    valid = series.dropna()
    if valid.empty:
        return pd.Series(0.0, index=series.index)
    ranks = series.rank(ascending=not higher_is_better, method="average", na_option="bottom")
    if len(series) == 1:
        return pd.Series(1.0, index=series.index)
    return 1 - (ranks - 1) / (len(series) - 1)


def score_candidates(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    weights = config["weights"]
    scored = df.copy()
    scored["double_low"] = scored["cb_price"] + scored["premium_rate"]
    scored["score_double_low"] = rank_score(scored["double_low"], higher_is_better=False)
    scored["score_momentum"] = rank_score(scored["stock_momentum_20d"], higher_is_better=True)
    scored["score_low_volatility"] = rank_score(scored["stock_volatility_20d"], higher_is_better=False)
    scored["score_small_market_cap"] = rank_score(scored["market_cap"], higher_is_better=False)
    scored["score"] = (
        weights["double_low"] * scored["score_double_low"]
        + weights["stock_momentum_20d"] * scored["score_momentum"]
        + weights["stock_low_volatility_20d"] * scored["score_low_volatility"]
        + weights["stock_small_market_cap"] * scored["score_small_market_cap"]
    )
    return scored.sort_values(["score", "double_low"], ascending=[False, True])


def assign_equal_weight(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    top_n = min(config["top_n"], len(df))
    result = df.head(top_n).copy()
    if top_n == 0:
        result["target_weight"] = pd.Series(dtype=float)
        return result
    equal_weight = min(1 / top_n, config["position"]["max_single_weight"])
    result["target_weight"] = equal_weight
    return result


def load_current_positions(path: Path) -> pd.DataFrame:
    if not path.exists():
        logging.warning("Current positions file does not exist: %s", path)
        return pd.DataFrame(columns=["bond_code", "bond_name", "target_weight"])
    df = pd.read_csv(path, dtype={"bond_code": str})
    if "bond_code" not in df.columns:
        raise ValueError(f"当前持仓文件缺少 bond_code 列: {path}")
    df["bond_code"] = df["bond_code"].astype(str).str.zfill(6)
    return df


def build_rebalance_plan(current: pd.DataFrame, target: pd.DataFrame) -> pd.DataFrame:
    current_codes = set(current["bond_code"].dropna().astype(str))
    target_codes = set(target["bond_code"].dropna().astype(str))
    rows: list[dict] = []
    target_lookup = target.set_index("bond_code").to_dict("index")
    current_lookup = current.set_index("bond_code").to_dict("index") if not current.empty else {}

    for code in sorted(current_codes - target_codes):
        item = current_lookup.get(code, {})
        rows.append({"action": "SELL", "bond_code": code, "bond_name": item.get("bond_name", ""), "target_weight": 0.0})
    for code in sorted(target_codes - current_codes):
        item = target_lookup.get(code, {})
        rows.append({"action": "BUY", "bond_code": code, "bond_name": item.get("bond_name", ""), "target_weight": item.get("target_weight")})
    for code in sorted(current_codes & target_codes):
        item = target_lookup.get(code, {})
        rows.append({"action": "HOLD", "bond_code": code, "bond_name": item.get("bond_name", ""), "target_weight": item.get("target_weight")})
    return pd.DataFrame(rows, columns=["action", "bond_code", "bond_name", "target_weight"])


def save_outputs(
    target: pd.DataFrame,
    rebalance: pd.DataFrame,
    config: dict,
    log_file: Path,
    data_notes: list[str],
) -> RunArtifacts:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidates_csv = OUTPUT_DIR / f"cb_rotation_top{config['top_n']}_{stamp}.csv"
    candidates_xlsx = OUTPUT_DIR / f"cb_rotation_top{config['top_n']}_{stamp}.xlsx"
    rebalance_csv = OUTPUT_DIR / f"cb_rotation_rebalance_{stamp}.csv"
    report_md = OUTPUT_DIR / f"cb_rotation_report_{stamp}.md"

    columns = [
        "bond_code",
        "bond_name",
        "stock_code",
        "stock_name",
        "cb_price",
        "premium_rate",
        "double_low",
        "stock_momentum_20d",
        "stock_volatility_20d",
        "market_cap",
        "market_cap_estimate",
        "remaining_size_100m",
        "turnover_yuan",
        "turnover_trade_date",
        "stock_factor_trade_date",
        "market_cap_as_of_date",
        "maturity_date",
        "listing_date",
        "score",
        "target_weight",
    ]
    available = [c for c in columns if c in target.columns]
    target[available].to_csv(candidates_csv, index=False, encoding="utf-8-sig")
    target[available].to_excel(candidates_xlsx, index=False)
    rebalance.to_csv(rebalance_csv, index=False, encoding="utf-8-sig")

    report_md.write_text(
        "\n".join(
            [
                f"# {config['strategy_name']} 运行报告",
                "",
                f"- 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"- 候选数量: {len(target)}",
                f"- 单债目标权重: {target['target_weight'].iloc[0]:.2%}" if len(target) else "- 单债目标权重: N/A",
                f"- 日志文件: `{log_file}`",
                "",
                "## 数据口径提醒",
                "",
                "\n".join(f"- {note}" for note in data_notes) if data_notes else "- 本次未发现明显数据口径提醒。",
                "",
                "## Top 候选",
                "",
                target[["bond_code", "bond_name", "cb_price", "premium_rate", "double_low", "score", "target_weight"]]
                .to_markdown(index=False),
                "",
                "## 调仓建议",
                "",
                rebalance.to_markdown(index=False) if not rebalance.empty else "无需调仓。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return RunArtifacts(candidates_csv, candidates_xlsx, rebalance_csv, report_md)


def resolve_trade_date(df: pd.DataFrame) -> date:
    """Best-effort data 基准日 from the frame's trade-date columns, falling back to today."""
    for col in ("turnover_trade_date", "stock_factor_trade_date"):
        if col in df.columns:
            values = pd.to_datetime(df[col], errors="coerce").dropna()
            if not values.empty:
                return values.max().date()
    return date.today()


def snapshot_raw_data(
    trade_date: date, frames: dict[str, pd.DataFrame], config: dict, subdir: str | None = None
) -> Path | None:
    """Persist an immutable, date-keyed point-in-time snapshot to ``data/raw/<trade_date>/``.

    Keyed by the data 基准日 so each trading day has one reproducible snapshot: the榜单 can be
    re-derived later, a failed live source can fall back to the last good day, and history
    accumulates for backtesting. Re-running the same trade date overwrites idempotently. Disabled
    via ``data.save_raw_snapshot = false``. ``subdir`` namespaces per-strategy snapshots under the
    same trade-date folder (e.g. ``stock_smallcap``) so multiple strategies don't collide.
    """
    if not config.get("data", {}).get("save_raw_snapshot", True):
        return None
    day_dir = RAW_DIR / f"{trade_date:%Y%m%d}"
    if subdir:
        day_dir = day_dir / subdir
    day_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    for name, frame in frames.items():
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            frame.to_csv(day_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
            written[name] = int(len(frame))
    (day_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "trade_date": trade_date.isoformat(),
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "rows": written,
    }
    (day_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Saved raw snapshot to %s (%s)", day_dir, written)
    return day_dir


def run(config_path: Path, positions_path: Path, max_bonds: int | None = None) -> RunArtifacts:
    log_file = setup_logging()
    config = load_config(config_path)
    enforce_snapshot_run_window(config)
    ak = require_akshare()

    raw_cb = fetch_cb_universe(ak, config)
    cb = normalize_cb_data(raw_cb)
    if max_bonds:
        cb = cb.head(max_bonds).copy()
        logging.info("Limited universe to first %s bonds for test run.", max_bonds)

    cb = enrich_cb_with_redeem_data(ak, cb, config)
    enforce_cb_filter_coverage(cb, config)
    cb = apply_cb_prefilters(cb, config)
    cb = enrich_cb_with_daily_market_data(ak, cb, config)
    if config.get("data", {}).get("strict_original_rules", True):
        assert_required_fields(cb, ["turnover_yuan"], "可转债成交额过滤", require_all_rows=True)
        require_fresh_dates(
            cb,
            "turnover_trade_date",
            int(config.get("data", {}).get("max_market_data_age_days", 4)),
            "可转债成交额过滤",
        )
        # Some illiquid bonds may not have traded on the latest session; their last trade
        # date lags by one day. Drop them so all remaining bonds share a single trade date.
        # These bonds would also fail the turnover filter, so exclusion is correct.
        if "turnover_trade_date" in cb.columns:
            latest_td = pd.to_datetime(cb["turnover_trade_date"], errors="coerce").max()
            stale_mask = pd.to_datetime(cb["turnover_trade_date"], errors="coerce") < latest_td
            if stale_mask.any():
                logging.info(
                    "Dropping %d bonds with stale turnover_trade_date (< %s): %s",
                    stale_mask.sum(),
                    latest_td.date(),
                    cb.loc[stale_mask, "bond_code"].tolist(),
                )
                cb = cb[~stale_mask].copy()
        require_single_trade_date(cb, "turnover_trade_date", "可转债收盘行情")
    assert_required_fields(cb, ["turnover_yuan"], "可转债成交额过滤")
    enforce_original_rule_fields(cb, config)
    stock_factors = fetch_stock_factors_with_cache(ak, cb["stock_code"].dropna(), date.today(), config)
    merged = cb.merge(stock_factors, on="stock_code", how="left")
    logging.info(
        "Stock factor coverage after merge: momentum=%s/%s, volatility=%s/%s",
        int(merged["stock_momentum_20d"].notna().sum()) if "stock_momentum_20d" in merged.columns else 0,
        len(merged),
        int(merged["stock_volatility_20d"].notna().sum()) if "stock_volatility_20d" in merged.columns else 0,
        len(merged),
    )
    stock_caps = fetch_stock_market_caps(ak, merged["stock_code"].dropna(), config)
    merged = merged.merge(stock_caps, on="stock_code", how="left")
    logging.info(
        "Stock market cap coverage after merge: market_cap=%s/%s",
        int(merged["market_cap"].notna().sum()) if "market_cap" in merged.columns else 0,
        len(merged),
    )
    merged = apply_market_cap_estimate(merged, config)

    filtered = apply_filters(merged, config)
    if config.get("data", {}).get("strict_original_rules", True):
        assert_required_fields(
            filtered,
            ["stock_momentum_20d", "stock_volatility_20d", "market_cap"],
            "因子计算",
            require_all_rows=True,
        )
        require_fresh_dates(
            filtered,
            "stock_factor_trade_date",
            int(config.get("data", {}).get("max_market_data_age_days", 4)),
            "正股动量与波动率",
        )
        require_single_trade_date(filtered, "stock_factor_trade_date", "正股收盘行情")
        require_fresh_dates(
            filtered,
            "market_cap_as_of_date",
            int(config.get("data", {}).get("max_market_data_age_days", 4)),
            "正股总市值",
        )
    enforce_factor_fields(filtered, config)
    data_notes = build_data_notes(filtered, config)
    scored = score_candidates(filtered, config)
    target = assign_equal_weight(scored, config)
    current = load_current_positions(positions_path)
    rebalance = build_rebalance_plan(current, target)
    artifacts = save_outputs(target, rebalance, config, log_file, data_notes)
    logging.info("Saved report to %s", artifacts.report_md)
    try:
        snapshot_raw_data(
            resolve_trade_date(scored),
            {
                "cb_universe_raw": raw_cb,
                "enriched_universe": merged,
                "scored_universe": scored,
                "target_topn": target,
                "rebalance_plan": rebalance,
            },
            config,
        )
    except Exception as exc:  # Snapshot is a safety net; never fail a good run over it.
        logging.warning("Failed to save raw snapshot: %s", exc)
    return artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run convertible bond multi-factor rotation strategy.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config JSON.")
    parser.add_argument("--positions", type=Path, default=DEFAULT_POSITIONS, help="Current positions CSV.")
    parser.add_argument("--max-bonds", type=int, default=None, help="Limit bonds for quick smoke tests.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    artifacts = run(args.config, args.positions, args.max_bonds)
    print("候选持仓:", artifacts.candidates_csv)
    print("调仓建议:", artifacts.rebalance_csv)
    print("运行报告:", artifacts.report_md)
