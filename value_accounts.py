"""账户估值:对"有持仓明细 + 现金"的账户,用数据层实时取价自动算总市值并回填 accounts_state.json。

只对配置了持仓文件的账户(股票、转债)自动估值——总市值 = Σ(持仓 × 最新价) + 现金。
没有持仓明细的账户(长钱 / 现金 / 海外)保持原值,需手动维护其总市值。

取价走数据层(datasource.market),与策略的盘中运行锁解耦,任何时段可用。

用法:
    python3 value_accounts.py                # 估值并回填 accounts_state.json
    python3 value_accounts.py --no-save      # 只预览,不写回
"""
from __future__ import annotations

import argparse
from pathlib import Path

from datasource import store
from datasource.market import fetch_cb_prices_tencent, fetch_tencent_snapshot

ROOT = Path(__file__).resolve().parent
DEFAULT_STATE = ROOT / "portfolios" / "accounts_state.json"

PRICED_ACCOUNTS = {
    "stock": {
        "positions": ROOT / "portfolios" / "current_stock_positions.csv",
        "code_col": "stock_code",
        "kind": "stock",
    },
    "bond": {
        "positions": ROOT / "portfolios" / "current_cb_positions.csv",
        "code_col": "bond_code",
        "kind": "bond",
    },
}


def compute_market_value(holdings: dict[str, int], prices: dict[str, float]) -> float:
    """Σ(持仓 × 最新价)。任一标的缺报价则报错,不静默漏算。"""
    missing = [c for c in holdings if c not in prices]
    if missing:
        raise ValueError(f"缺少这些标的的报价,无法估值: {missing}")
    return float(sum(holdings[c] * prices[c] for c in holdings))


def fetch_prices(kind: str, codes: list[str]) -> dict[str, float]:
    if not codes:
        return {}
    if kind == "stock":
        snap = fetch_tencent_snapshot(codes)
        if snap.empty:
            return {}
        return {str(code): float(price) for code, price in zip(snap["stock_code"], snap["price"])}
    return fetch_cb_prices_tencent(list(codes))


def main() -> None:
    parser = argparse.ArgumentParser(description="对有持仓+现金的账户自动估值并回填账户状态。")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="账户状态 JSON,默认 portfolios/accounts_state.json。")
    parser.add_argument("--no-save", action="store_true", help="只预览,不写回。")
    args = parser.parse_args()

    state = store.load_accounts_state(args.state)

    print("\n账户估值(数据层实时取价):")
    for acct, cfg in PRICED_ACCOUNTS.items():
        if acct not in state:
            continue
        holdings = store.load_holdings(cfg["positions"], cfg["code_col"])
        prices = fetch_prices(cfg["kind"], list(holdings))
        try:
            mv = compute_market_value(holdings, prices)
        except ValueError as exc:
            raise SystemExit(str(exc))
        cash = float(state[acct].get("cash", 0.0))
        total = round(mv + cash, 2)
        state[acct]["total"] = total
        label = state[acct].get("label", acct)
        print(f"  {label}: 持仓 {mv:,.2f} + 现金 {cash:,.2f} = 总 {total:,.2f}  ({len(holdings)} 只)")

    manual = [k for k, v in state.items() if isinstance(v, dict) and k not in PRICED_ACCOUNTS]
    if manual:
        print("\n需手动维护总市值(无持仓明细):")
        for acct in manual:
            label = state[acct].get("label", acct)
            cur = state[acct].get("total")
            cur_str = f"{cur:,.2f}" if isinstance(cur, (int, float)) else "—"
            print(f"  {label}: 当前 {cur_str}(未改动)")

    if args.no_save:
        print("\n--no-save: 未写回。")
    else:
        store.save_accounts_state(state, args.state)
        print(f"\n已回填: {args.state}")


if __name__ == "__main__":
    main()
