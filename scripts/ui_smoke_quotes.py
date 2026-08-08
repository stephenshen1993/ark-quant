"""Deterministic quote provider for the isolated browser smoke harness."""
from __future__ import annotations

import pandas as pd


STOCK_QUOTES = {
    "600051": {"name": "宁波联合", "price": 3044.5039},
}
CB_QUOTES = {
    "113062": {"name": "常银转债", "price": 174.32535},
}


def fetch_tencent_snapshot(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "stock_code": code,
            "stock_name_q": STOCK_QUOTES[code]["name"],
            "price": STOCK_QUOTES[code]["price"],
        }
        for code in codes
        if code in STOCK_QUOTES
    ])


def fetch_cb_quotes_tencent(codes: list[str]) -> dict[str, dict]:
    return {
        code: dict(CB_QUOTES[code])
        for code in codes
        if code in CB_QUOTES
    }
