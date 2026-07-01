#!/usr/bin/env python3
"""CSV vs DB 持仓对账脚本。

对比 portfolios/ 下的 CSV 持仓文件和 SQLite 中的持仓记录，输出差异报告。
--fix 参数可将 CSV 写入 DB，覆盖 DB 中的记录。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datasource.db import get_latest_positions, init_db, insert_positions

CSV_PATHS = {
    "cb": ROOT / "portfolios" / "current_cb_positions.csv",
    "stock": ROOT / "portfolios" / "current_stock_positions.csv",
}


def load_csv(strategy: str) -> pd.DataFrame:
    path = CSV_PATHS[strategy]
    if not path.exists():
        print(f"[WARN] CSV 文件不存在: {path}")
        return pd.DataFrame()
    df = pd.read_csv(path, dtype=str)
    code_col = "bond_code" if strategy == "cb" else "stock_code"
    if code_col not in df.columns:
        print(f"[WARN] CSV 缺少 {code_col} 列: {path}")
        return pd.DataFrame()
    df[code_col] = df[code_col].astype(str).str.zfill(6)
    df["shares"] = pd.to_numeric(df.get("shares", 0), errors="coerce").fillna(0).astype(int)
    return df[[code_col, "shares"]].groupby(code_col, as_index=False).sum()


def load_db(strategy: str) -> pd.DataFrame:
    rows = get_latest_positions(strategy)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["shares"] = pd.to_numeric(df.get("shares", 0), errors="coerce").fillna(0).astype(int)
    return df[["code", "shares"]]


def reconcile(strategy: str, csv_df: pd.DataFrame, db_df: pd.DataFrame) -> list[str]:
    code_col = "bond_code" if strategy == "cb" else "stock_code"
    name = "转债" if strategy == "cb" else "股票"

    csv_codes = set(csv_df[code_col]) if not csv_df.empty else set()
    db_codes = set(db_df["code"]) if not db_df.empty else set()

    lines: list[str] = [f"## {name} 持仓对账", ""]

    if not csv_codes and not db_codes:
        lines.append("两边均为空，无需对账。")
        return lines

    only_csv = csv_codes - db_codes
    only_db = db_codes - csv_codes
    common = csv_codes & db_codes

    if only_csv:
        lines.append(f"### 仅在 CSV 中（{len(only_csv)} 只）")
        for c in sorted(only_csv):
            shares = int(csv_df[csv_df[code_col] == c]["shares"].iloc[0])
            lines.append(f"  - {c}: {shares} 张/股")
        lines.append("")

    if only_db:
        lines.append(f"### 仅在 DB 中（{len(only_db)} 只）")
        for c in sorted(only_db):
            shares = int(db_df[db_df["code"] == c]["shares"].iloc[0])
            lines.append(f"  - {c}: {shares} 张/股")
        lines.append("")

    diff_count = 0
    for c in sorted(common):
        csv_shares = int(csv_df[csv_df[code_col] == c]["shares"].iloc[0])
        db_shares = int(db_df[db_df["code"] == c]["shares"].iloc[0])
        if csv_shares != db_shares:
            diff_count += 1
            if diff_count == 1:
                lines.append("### 数量不一致")
            lines.append(f"  - {c}: CSV={csv_shares}, DB={db_shares}")
    if diff_count:
        lines.append("")

    if not only_csv and not only_db and diff_count == 0:
        lines.append("CSV 与 DB 完全一致 ✓")
    else:
        summary = []
        if only_csv:
            summary.append(f"仅CSV: {len(only_csv)}")
        if only_db:
            summary.append(f"仅DB: {len(only_db)}")
        if diff_count:
            summary.append(f"数量不一致: {diff_count}")
        lines.append(f"**差异总计**: {', '.join(summary)}")

    return lines


def fix_db(strategy: str, csv_df: pd.DataFrame) -> None:
    code_col = "bond_code" if strategy == "cb" else "stock_code"
    name_col = "bond_name" if strategy == "cb" else "stock_name"
    today = date.today().isoformat()

    rows = []
    for _, r in csv_df.iterrows():
        rows.append({"code": r[code_col], "name": r.get(name_col, ""), "shares": int(r["shares"])})

    init_db()
    insert_positions(strategy, today, rows)
    name = "转债" if strategy == "cb" else "股票"
    print(f"[OK] 已将 {name} CSV 持仓同步到 DB ({len(rows)} 条, position_date={today})")


def main():
    parser = argparse.ArgumentParser(description="CSV vs DB 持仓对账")
    parser.add_argument("--fix", action="store_true", help="将 CSV 中的持仓写入 DB")
    parser.add_argument("--strategy", choices=["cb", "stock"], help="仅对账指定策略")
    args = parser.parse_args()

    strategies = [args.strategy] if args.strategy else ["cb", "stock"]
    has_diff = False

    for s in strategies:
        csv_df = load_csv(s)
        db_df = load_db(s)

        if args.fix:
            if csv_df.empty:
                print(f"[SKIP] CSV 为空，跳过 {s}")
                continue
            fix_db(s, csv_df)
        else:
            lines = reconcile(s, csv_df, db_df)
            print("\n".join(lines))
            print()
            if "完全一致" not in lines[-2] and "两边均为空" not in lines[1]:
                has_diff = True

    if has_diff:
        print("💡 使用 --fix 参数可将 CSV 同步到 DB。")


if __name__ == "__main__":
    main()
