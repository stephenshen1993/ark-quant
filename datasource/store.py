"""自录数据的读写入口(账户状态 JSON、持仓 CSV)。

上层只通过本模块读写本地录入的数据,不直接接触文件格式。以后若底层从文件换成数据库,
上层无需改动——这是把"手动录入的数据"也归入数据源层的意义。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def load_accounts_state(path: Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def save_accounts_state(state: dict, path: Path) -> None:
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_holdings(path: Path, code_col: str, share_col: str = "shares") -> dict[str, int]:
    """读持仓 CSV,返回 {6位代码: 数量}。文件不存在按空仓处理。"""
    path = Path(path)
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype={code_col: str})
    holdings: dict[str, int] = {}
    for _, row in df.iterrows():
        code = "".join(ch for ch in str(row[code_col]) if ch.isdigit())[-6:].zfill(6)
        shares = pd.to_numeric(row[share_col], errors="coerce")
        if pd.notna(shares):
            holdings[code] = int(shares)
    return holdings
