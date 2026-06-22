"""三方行情数据源(腾讯 qt.gtimg.cn)。

最底层的数据获取原语:股票/转债的代码-交易所符号转换,以及从腾讯行情接口拉取
个股快照、正股总市值、可转债价格。eastmoney-free、login-free,任何时段可调用,
与各策略的盘中运行锁解耦。上层(策略、账户估值等)只依赖本模块,不应自行拼接行情接口。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable

import pandas as pd


def normalize_stock_code(code: str) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def stock_symbol_with_exchange(code: str) -> str:
    code = normalize_stock_code(code)
    prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}{code}"


def bond_symbol_with_exchange(code: str) -> str:
    code = "".join(ch for ch in str(code) if ch.isdigit())[-6:]
    prefix = "sh" if code.startswith("11") else "sz"
    return f"{prefix}{code}"


def _to_num(value) -> float:
    return pd.to_numeric(value, errors="coerce")


def _parse_tencent_quote_caps(text: str, as_of_date: str) -> list[dict]:
    """Parse 总市值 from Tencent quote strings (`v_sh600519="1~贵州茅台~600519~...";`).

    Field index 45 of the `~`-delimited payload is 总市值 in 亿元; index 1 is the name, index 2
    the code. Returns one row per parseable quote line.
    """
    rows: list[dict] = []
    for line in text.split(";"):
        if '="' not in line:
            continue
        payload = line.split('"', 1)[1].rstrip('"')
        fields = payload.split("~")
        if len(fields) <= 45:
            continue
        total_mv_yi = pd.to_numeric(fields[45], errors="coerce")
        if pd.isna(total_mv_yi):
            continue
        rows.append(
            {
                "stock_code": normalize_stock_code(fields[2]),
                "stock_name_spot": fields[1],
                "market_cap": float(total_mv_yi) * 100_000_000,  # 亿元 -> 元
                "industry": pd.NA,
                "market_cap_as_of_date": as_of_date,
                "market_cap_source": "tencent_qt_total_mv",
            }
        )
    return rows


def fetch_stock_market_caps_tencent(stock_codes: Iterable[str], batch_size: int = 50) -> pd.DataFrame:
    """Fetch 正股总市值 from the Tencent quote API (qt.gtimg.cn).

    Eastmoney-free and login-free, so it works where eastmoney blocks Python requests. Stocks are
    queried in comma-separated batches. Used as the primary total-market-cap source.
    """
    import requests

    codes = sorted({normalize_stock_code(c) for c in stock_codes if pd.notna(c)})
    today = date.today().isoformat()
    rows: list[dict] = []
    for start in range(0, len(codes), batch_size):
        batch = [c for c in codes[start : start + batch_size] if c]
        if not batch:
            continue
        query = ",".join(stock_symbol_with_exchange(code) for code in batch)
        try:
            resp = requests.get(f"http://qt.gtimg.cn/q={query}", timeout=10)
            resp.encoding = "gbk"
            rows.extend(_parse_tencent_quote_caps(resp.text, today))
        except Exception as exc:  # Network/host hiccup on one batch should not abort the rest.
            logging.warning("Tencent market cap batch failed (%s..): %s", batch[0], exc)

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
        caps = caps.dropna(subset=["market_cap"]).drop_duplicates(subset=["stock_code"], keep="last")
    return caps


def fetch_tencent_snapshot(codes: Iterable[str], batch_size: int = 60) -> pd.DataFrame:
    """Snapshot fields from Tencent qt.gtimg.cn (eastmoney-free, batched)."""
    import requests

    codes = [str(c).zfill(6) for c in dict.fromkeys(codes) if str(c).strip()]
    rows: list[dict] = []
    for start in range(0, len(codes), batch_size):
        batch = codes[start : start + batch_size]
        query = ",".join(stock_symbol_with_exchange(c) for c in batch)
        try:
            resp = requests.get(f"http://qt.gtimg.cn/q={query}", timeout=10)
            resp.encoding = "gbk"
        except Exception as exc:
            logging.warning("Tencent snapshot batch failed (%s..): %s", batch[0], exc)
            continue
        for line in resp.text.split(";"):
            if '="' not in line:
                continue
            f = line.split('"', 1)[1].rstrip('"').split("~")
            if len(f) <= 48:
                continue
            rows.append(
                {
                    "stock_code": normalize_stock_code(f[2]),
                    "stock_name_q": f[1],
                    "price": _to_num(f[3]),
                    "prev_close": _to_num(f[4]),
                    "volume_hand": _to_num(f[6]),
                    "amount_yuan": _to_num(f[37]) * 10_000,  # 万元 -> 元
                    "pe_ttm": _to_num(f[39]),
                    "total_mv_yuan": _to_num(f[45]) * 100_000_000,  # 亿元 -> 元
                    "limit_up": _to_num(f[47]),
                    "limit_down": _to_num(f[48]),
                }
            )
    snap = pd.DataFrame(rows)
    if not snap.empty:
        snap["stock_code"] = snap["stock_code"].astype(str).str.zfill(6)
        snap = snap.drop_duplicates("stock_code")
    return snap


def fetch_cb_prices_tencent(codes: list[str], batch_size: int = 50) -> dict[str, float]:
    """Live convertible-bond prices from Tencent (field 3 of the quote string)."""
    import requests

    prices: dict[str, float] = {}
    codes = [str(c).zfill(6) for c in dict.fromkeys(codes) if str(c).strip()]
    for start in range(0, len(codes), batch_size):
        batch = codes[start : start + batch_size]
        query = ",".join(bond_symbol_with_exchange(code) for code in batch)
        try:
            resp = requests.get(f"http://qt.gtimg.cn/q={query}", timeout=10)
            resp.encoding = "gbk"
        except Exception as exc:
            logging.warning("Tencent CB price batch failed (%s..): %s", batch[0], exc)
            continue
        for line in resp.text.split(";"):
            if '="' not in line:
                continue
            fields = line.split('"', 1)[1].rstrip('"').split("~")
            if len(fields) <= 3:
                continue
            code = "".join(ch for ch in fields[2] if ch.isdigit())[-6:].zfill(6)
            price = pd.to_numeric(fields[3], errors="coerce")
            if code and pd.notna(price) and price > 0:
                prices[code] = float(price)
    return prices
