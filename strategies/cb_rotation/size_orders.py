"""Convert a target Top-N list + current holdings (张数) + cash into a concrete order sheet.

This is the weekly execution step: it takes the latest CB rotation target list, your current
holdings in 张数, and your cash, and produces exactly how many 张 to buy/sell per bond — with the
10-张 lot rule and a cash constraint so you never overspend. It is decoupled from the strategy's
intraday run guard, so it works any time, and prices come live from Tencent (eastmoney-free).

Usage:
    python3 -m strategies.cb_rotation.size_orders --cash 14802.95
    python3 -m strategies.cb_rotation.size_orders --target outputs/cb_rotation_top20_20260605_091400.csv \
        --positions portfolios/current_cb_positions.csv --cash 14802.95
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from datasource.market import fetch_cb_prices_tencent
from strategies.cb_rotation.run import (
    OUTPUT_DIR,
    ROOT,
    setup_logging,
)

DEFAULT_POSITIONS = ROOT / "portfolios" / "current_cb_positions.csv"
LOT = 10  # 沪深可转债最小交易单位与递增均为 10 张
TARGET_SLOTS = 20


def latest_target_file() -> Path:
    files = sorted(OUTPUT_DIR.glob("cb_rotation_top*_*.csv"), reverse=True)
    if not files:
        raise SystemExit("找不到目标榜单文件。请先运行 strategies.cb_rotation.run 生成 Top 榜单。")
    return files[0]


def load_positions(path: Path) -> pd.DataFrame:
    if not path.exists():
        logging.warning("持仓文件不存在: %s（按空仓处理）", path)
        return pd.DataFrame(columns=["bond_code", "bond_name", "shares"])
    df = pd.read_csv(path, dtype={"bond_code": str})
    df["bond_code"] = df["bond_code"].astype(str).str.zfill(6)
    if "shares" not in df.columns:
        raise SystemExit(
            f"持仓文件缺少 shares（张数）列: {path}。请改成 bond_code,bond_name,shares 格式。"
        )
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0).astype(int)
    if "bond_name" not in df.columns:
        df["bond_name"] = ""
    return df[["bond_code", "bond_name", "shares"]]


def size_rebalance(
    target: pd.DataFrame,
    positions: pd.DataFrame,
    cash: float,
    prices: dict[str, float],
    lot: int = LOT,
    max_single_weight: float = 0.08,
) -> tuple[pd.DataFrame, dict]:
    """Return frozen net orders using the fixed Top-20 per-bond target unit."""
    target = target.copy()
    target["bond_code"] = target["bond_code"].astype(str).str.zfill(6)
    target_codes = list(dict.fromkeys(target["bond_code"]))
    name_map = dict(zip(target["bond_code"], target.get("bond_name", pd.Series(dtype=str))))
    held = dict(zip(positions["bond_code"], positions["shares"]))
    for _, r in positions.iterrows():
        name_map.setdefault(r["bond_code"], r.get("bond_name", ""))

    missing = [c for c in set(target_codes) | set(held) if c not in prices]
    if missing:
        raise SystemExit(f"缺少这些转债的报价，无法定张数: {missing}")

    holdings_value = sum(held[c] * prices[c] for c in held)
    total_value = holdings_value + cash
    n = len(target_codes)
    per = total_value / TARGET_SLOTS
    cap = max_single_weight * total_value
    per = min(per, cap)

    # 向下取整到 lot，保证不超目标权重；未占用余额保留在资金账户。
    desired = {c: max(int((per / prices[c]) // lot) * lot, 0) for c in target_codes}

    def cash_left() -> float:
        flow = cash
        for c in held:  # 卖出不在目标里的，全清；在目标里的按差额
            if c not in desired:
                flow += held[c] * prices[c]
        for c in target_codes:
            flow -= (desired[c] - held.get(c, 0)) * prices[c]
        return flow

    left = cash_left()
    # 余额不足则从排名靠后的目标里减仓位；剩余余额不再补仓。
    rank = {c: i for i, c in enumerate(target_codes)}
    while left < 0:
        candidates = [c for c in target_codes if desired[c] >= lot]
        if not candidates:
            break
        c = max(candidates, key=lambda x: (rank[x], prices[x]))
        desired[c] -= lot
        left += prices[c] * lot
    rows = []
    for c in sorted(set(held) - set(target_codes)):
        rows.append(_row("SELL", c, name_map, prices, held[c], 0))
    for c in target_codes:
        cur, tgt = held.get(c, 0), desired[c]
        if cur == 0:
            rows.append(_row("BUY", c, name_map, prices, cur, tgt))
        elif tgt > cur:
            rows.append(_row("ADD", c, name_map, prices, cur, tgt))
        elif tgt < cur:
            rows.append(_row("TRIM", c, name_map, prices, cur, tgt))
        else:
            rows.append(_row("HOLD", c, name_map, prices, cur, tgt))

    sheet = pd.DataFrame(rows)
    summary = {
        "total_value": total_value,
        "holdings_value": holdings_value,
        "cash_in": cash,
        "per_target": per,
        "cash_left": left,
        "n_target": n,
        "target_slot_count": TARGET_SLOTS,
        "uninvestable_cash": max(0.0, left),
    }
    return sheet, summary


def _row(action, code, name_map, prices, cur, tgt):
    delta = tgt - cur
    return {
        "action": action,
        "bond_code": code,
        "bond_name": name_map.get(code, ""),
        "price": round(prices[code], 3),
        "current_shares": int(cur),
        "target_shares": int(tgt),
        "ideal_target_shares": int(tgt),
        "executable_target_shares": int(tgt),
        "residual_shares": 0,
        "execution_reason": "mandatory_exit" if action == "SELL" else "frozen_target",
        "delta_shares": int(delta),
        "amount": round(abs(delta) * prices[code], 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="把目标榜单+持仓+现金换算成可转债下单张数。")
    parser.add_argument("--target", type=Path, default=None, help="目标榜单CSV，默认取 outputs/ 最新一份。")
    parser.add_argument("--positions", type=Path, default=DEFAULT_POSITIONS, help="持仓CSV（含 shares 张数）。")
    parser.add_argument("--cash", type=float, required=True, help="当前可用现金（元）。")
    args = parser.parse_args()

    setup_logging()

    # 优先从 DB 读最新榜单和持仓，CSV 作为 fallback
    target = None
    positions = None
    if args.target is None:
        try:
            from datasource.db import get_latest_rankings, get_latest_positions
            db_r = get_latest_rankings("cb")
            db_p = get_latest_positions("cb")
            if db_r:
                target = pd.DataFrame(db_r)[["bond_code", "bond_name"]]
                target["bond_code"] = target["bond_code"].astype(str).str.zfill(6)
                logging.info("榜单来源: DB (%d 只)", len(target))
            if db_p:
                positions = pd.DataFrame([
                    {"bond_code": r["code"], "bond_name": r["name"], "shares": r["shares"]}
                    for r in db_p
                ])
                positions["bond_code"] = positions["bond_code"].astype(str).str.zfill(6)
                logging.info("持仓来源: DB (%d 只)", len(positions))
        except Exception as _e:
            logging.warning("DB 读取失败，回退到文件: %s", _e)

    if target is None:
        target_path = args.target or latest_target_file()
        logging.info("目标榜单: %s", target_path)
        target = pd.read_csv(target_path, dtype={"bond_code": str})
    if positions is None:
        positions = load_positions(args.positions)

    codes = list(dict.fromkeys(list(target["bond_code"].astype(str).str.zfill(6)) + list(positions["bond_code"])))
    prices = fetch_cb_prices_tencent(codes)
    sheet, summary = size_rebalance(target, positions, args.cash, prices)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / f"cb_orders_{stamp}.csv"
    sheet.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"\n总资产 {summary['total_value']:,.0f} 元 = 持仓 {summary['holdings_value']:,.0f} + 现金 {summary['cash_in']:,.0f}")
    print(f"每只目标 {summary['per_target']:,.0f} 元 (≈{summary['per_target']/summary['total_value']:.1%})，共 {summary['n_target']} 只\n")
    for act, title in [("SELL", "清仓卖出"), ("TRIM", "减仓"), ("BUY", "买入"), ("ADD", "加仓"), ("HOLD", "持有不动")]:
        sub = sheet[sheet["action"] == act]
        if sub.empty:
            continue
        print(f"——— {title} ({len(sub)}) ———")
        for _, r in sub.iterrows():
            verb = {"SELL": "卖", "TRIM": "卖", "BUY": "买", "ADD": "买", "HOLD": ""}[act]
            qty = f"{verb}{abs(int(r['delta_shares']))}张" if r["delta_shares"] else "不动"
            print(f"  {r['bond_code']} {r['bond_name']:<7} {r['price']:>8.2f}  {qty:<8}({r['amount']:>9,.0f}元)")
        print()
    print(f"调仓后剩余现金 ≈ {summary['cash_left']:,.0f} 元")
    print(f"下单清单已存: {out}")
    try:
        from datasource.db import init_db, get_latest_run_id, insert_cb_orders
        init_db()
        run_id = get_latest_run_id("cb")
        if run_id is not None:
            insert_cb_orders(run_id, sheet)
            logging.info("DB write OK: cb orders run_id=%s", run_id)
        else:
            logging.warning("DB write skipped: no cb strategy_run found yet.")
    except Exception as exc:
        logging.warning("DB write failed (cb orders): %s", exc)


if __name__ == "__main__":
    main()
