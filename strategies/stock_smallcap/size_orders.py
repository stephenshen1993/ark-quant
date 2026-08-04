"""把股票小市值调仓建议换算成具体买卖手数。

每期全量等权对齐 (Model I)：目标持仓 = HOLD + BUY 的并集，按等权目标重新计算每只应持
手数，对现有仓位做加仓 / 减仓 / 不动，同时把退出仓位全清。资金利用率最大化，残余现金
通过贪心追加方式压缩到最小。

用法:
    python3 -m strategies.stock_smallcap.size_orders --cash 30852
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from datasource.market import fetch_tencent_snapshot
from strategies.stock_smallcap.run import (
    DEFAULT_POSITIONS,
    OUTPUT_DIR,
    load_current_positions,
    setup_logging,
)
from strategies.stock_smallcap.target_sizing import size_target_state

LOT = 100  # 1手 = 100股


def latest_rebalance_file() -> Path:
    files = sorted(OUTPUT_DIR.glob("stock_smallcap_rebalance_*.csv"), reverse=True)
    if not files:
        raise SystemExit("找不到调仓文件，请先运行 strategies.stock_smallcap.run。")
    return files[0]


def size_rebalance(
    rankings: pd.DataFrame,
    positions: pd.DataFrame,
    cash: float,
    prices: dict[str, float],
    **_ignored,
) -> tuple[pd.DataFrame, dict]:
    """Compatibility adapter for the shared formal target-state sizer."""
    return size_target_state(rankings, positions, cash, prices)


def _trade_cost(amount: float, action: str) -> float:
    """A 股交易费用(元)：佣金最低 5 元 + 卖出印花税 0.05%。"""
    commission = max(5.0, amount * 0.00025)
    stamp = amount * 0.0005 if action in ("SELL", "TRIM") else 0.0
    return round(commission + stamp, 2)


def _row(action: str, code: str, name_map: dict, prices: dict, cur: int, tgt: int) -> dict:
    delta = tgt - cur
    amount = round(abs(delta) * prices[code], 2)
    return {
        "action": action,
        "stock_code": code,
        "stock_name": name_map.get(code, ""),
        "price": round(prices[code], 3),
        "current_shares": cur,
        "target_shares": tgt,
        "delta_shares": delta,
        "amount": amount,
        "est_cost": _trade_cost(amount, action) if delta != 0 else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="把股票小市值调仓建议换算成具体买卖手数。")
    parser.add_argument("--rebalance", type=Path, default=None, help="调仓建议CSV，默认取最新一份。")
    parser.add_argument("--positions", type=Path, default=DEFAULT_POSITIONS)
    parser.add_argument("--cash", type=float, required=True, help="当前可用现金（元）。")
    parser.add_argument("--min-trade-value", type=float, default=1000.0,
                        help="加仓/买入金额下限（元），低于此的小单跳过，默认 1000。")
    args = parser.parse_args()

    setup_logging()

    # 优先从 DB 读最新榜单和持仓，CSV 作为 fallback
    reb = None
    positions = None
    if args.rebalance is None:
        try:
            from datasource.db import get_latest_rankings, get_latest_positions
            db_r = get_latest_rankings("stock")
            db_p = get_latest_positions("stock")
            if db_r:
                reb = pd.DataFrame(db_r)[["stock_code", "stock_name", "rank"]]
                reb["stock_code"] = reb["stock_code"].astype(str).str.zfill(6)
                reb["action"] = "BUY"  # size_rebalance 只用 HOLD/BUY 确定目标集合
                logging.info("榜单来源: DB (%d 只)", len(reb))
            if db_p:
                positions = pd.DataFrame([
                    {"stock_code": r["code"], "stock_name": r["name"], "shares": r["shares"]}
                    for r in db_p
                ])
                positions["stock_code"] = positions["stock_code"].astype(str).str.zfill(6)
                logging.info("持仓来源: DB (%d 只)", len(positions))
        except Exception as _e:
            logging.warning("DB 读取失败，回退到文件: %s", _e)

    if reb is None:
        reb_path = args.rebalance or latest_rebalance_file()
        reb = pd.read_csv(reb_path, dtype={"stock_code": str})
        reb["stock_code"] = reb["stock_code"].astype(str).str.zfill(6)
    if positions is None:
        positions = load_current_positions(args.positions)

    all_codes = list(dict.fromkeys(
        list(reb["stock_code"]) + list(positions["stock_code"])
    ))
    snap = fetch_tencent_snapshot(all_codes)
    prices = dict(zip(snap["stock_code"], snap["price"]))
    missing = [c for c in all_codes if c not in prices]
    if missing:
        raise SystemExit(f"缺少报价，无法计算: {missing}")

    sheet, summary = size_rebalance(reb, positions, args.cash, prices,
                                    min_trade_value=args.min_trade_value)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / f"stock_orders_{stamp}.csv"
    sheet.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"\n总资产 {summary['total_value']:>12,.0f} 元  (持仓 {summary['holdings_value']:,.0f} + 现金 {summary['cash_in']:,.0f})")
    print(f"每只目标 {summary['per_target']:>10,.0f} 元 (≈{summary['per_target']/summary['total_value']:.1%})，共 {summary['n_target']} 只\n")

    label = {"SELL": "清仓卖出", "TRIM": "减仓", "BUY": "买入", "ADD": "加仓", "HOLD": "持有不动", "SKIP": "跳过（摩擦过高）"}
    verb  = {"SELL": "卖", "TRIM": "卖", "BUY": "买", "ADD": "买", "HOLD": "", "SKIP": ""}
    skipped = summary.get("skipped", {})
    for act in ("SELL", "TRIM", "BUY", "ADD", "SKIP", "HOLD"):
        sub = sheet[sheet["action"] == act]
        if sub.empty:
            continue
        print(f"——— {label[act]} ({len(sub)}) ———")
        for _, r in sub.iterrows():
            if act == "SKIP":
                skip_lots, skip_val = skipped.get(r["stock_code"], (0, 0.0))
                qty = f"跳过 +{skip_lots}股(+{skip_lots // LOT}手)  {skip_val:.0f}元 < {args.min_trade_value:.0f}元"
            elif r["delta_shares"]:
                qty = f"{verb[act]}{abs(int(r['delta_shares']))}股({abs(int(r['delta_shares']))//LOT}手)  {r['amount']:>10,.0f}元"
            else:
                qty = f"不动  {int(r['current_shares'])}股  {int(r['current_shares'])*r['price']:>10,.0f}元"
            print(f"  {r['stock_code']} {r['stock_name']:<7} {r['price']:>8.2f}元  {qty}")
        print()

    print(f"调仓后剩余现金 ≈ {summary['cash_left']:,.0f} 元")
    print(f"下单清单已存: {out}")

    try:
        from datasource.db import init_db, get_latest_run_id, insert_stock_orders
        init_db()
        _run_id = get_latest_run_id("stock")
        if _run_id is not None:
            insert_stock_orders(_run_id, sheet)
        else:
            import logging as _log
            _log.warning("DB write skipped: no stock strategy_run found yet")
    except Exception as _exc:
        import logging as _log
        _log.warning("DB write failed (stock orders): %s", _exc)


if __name__ == "__main__":
    main()
