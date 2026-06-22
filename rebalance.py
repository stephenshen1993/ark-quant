#!/usr/bin/env python3
"""顶层组合分配器 — 根据全市场温度计算各账户目标金额并生成资金调拨计划。

用法:
    python3 rebalance.py --temperature 45
    python3 rebalance.py --temperature 45 --stock 200000 --stock-cash 1000 \\
                         --bond 250000 --bond-cash 1000 \\
                         --changqian 50000 --cash-pool 20000 --overseas 30000

账户参数 (均为可选；省略则读取上次保存的值):
    --stock V       股票账户总市值（持仓 + 账户内现金）
    --stock-cash V  股票账户内现金（size_orders 使用，默认读取上次状态）
    --bond V        转债账户总市值
    --bond-cash V   转债账户内现金
    --changqian V   长钱账户总净值
    --cash-pool V   现金账户余额（银行/余额宝等）
    --overseas V    海外长钱总净值（不纳入国内公式，单独追踪）
    --no-save       本次计算结果不写入 accounts_state.json

温度来源: 有知有行温度计 https://youzhiyouxing.cn/data
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

STATE_FILE = Path(__file__).parent / "portfolios" / "accounts_state.json"

_THRESHOLD = 1000  # 差额低于此值视为无需调拨（元）


# ── State I/O ─────────────────────────────────────────────────────────────────

def _default_state() -> dict:
    return {
        "updated_at": str(date.today()),
        "temperature": None,
        "stock":     {"label": "股票账户", "total": 0.0, "cash": 0.0},
        "bond":      {"label": "转债账户", "total": 0.0, "cash": 0.0},
        "changqian": {"label": "长钱账户", "total": 0.0},
        "cash_pool": {"label": "现金账户", "total": 0.0},
        "overseas":  {"label": "海外长钱", "total": 0.0},
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        with STATE_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    return _default_state()


def save_state(state: dict) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── Formula ───────────────────────────────────────────────────────────────────

def calc_targets(domestic_total: float, T_raw: float) -> dict:
    """五. 核心公式 from docs/system/investment_system.md."""
    T_norm = T_raw / 100.0
    cash_target = domestic_total * 0.20 * T_norm
    B = domestic_total - cash_target
    return {
        "cash_pool": cash_target,
        "changqian": B * 0.20,
        "bond":      B * 0.80 * (0.30 + 0.004 * T_raw),
        "stock":     B * 0.80 * (0.70 - 0.004 * T_raw),
    }


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _money(n: float, width: int = 12) -> str:
    return f"{n:{width},.0f}"


def _delta(d: float) -> str:
    s = f"{abs(d):,.0f}"
    return f"+{s}" if d >= 0 else f"-{s}"


# ── Transfer plan ─────────────────────────────────────────────────────────────

def build_transfer_plan(deltas: dict) -> list[str]:
    """Generate practical ordered transfer steps.

    現金账户 acts as the hub: overweight accounts transfer out to it first,
    then it transfers in to underweight accounts.  长钱 is handled separately
    via fund 申购/赎回.
    """
    steps = []

    # Step 1 – overweight securities accounts → cash_pool
    for key, label in [("bond", "转债账户"), ("stock", "股票账户")]:
        d = deltas[key]
        if d < -_THRESHOLD:
            steps.append(
                f"{label} →[银证转出]→ 现金账户:  {_money(abs(d), 10)} 元"
            )

    # Step 2 – overweight changqian → cash_pool (赎回 takes days)
    if deltas["changqian"] < -_THRESHOLD:
        steps.append(
            f"长钱账户 →[基金赎回]→ 现金账户:  {_money(abs(deltas['changqian']), 10)} 元"
            "  (T+2~T+7)"
        )

    # Step 3 – cash_pool → underweight securities accounts
    for key, label in [("bond", "转债账户"), ("stock", "股票账户")]:
        d = deltas[key]
        if d > _THRESHOLD:
            steps.append(
                f"现金账户 →[银证转入]→ {label}:  {_money(d, 10)} 元"
            )

    # Step 4 – cash_pool → underweight changqian (申购)
    if deltas["changqian"] > _THRESHOLD:
        steps.append(
            f"现金账户 →[基金申购]→ 长钱账户:  {_money(deltas['changqian'], 10)} 元"
            "  (T+1/T+2 到账)"
        )

    return steps


# ── Main report ───────────────────────────────────────────────────────────────

def print_report(T_raw: float, state: dict) -> None:
    overseas = state["overseas"]["total"]
    domestic_total = (
        state["stock"]["total"]
        + state["bond"]["total"]
        + state["changqian"]["total"]
        + state["cash_pool"]["total"]
    )

    if domestic_total <= 0:
        print("\n⚠  国内账户总市值为 0，请先填写各账户数值。", file=sys.stderr)
        print("示例: python3 rebalance.py --temperature 45 "
              "--stock 200000 --stock-cash 1000 "
              "--bond 250000 --bond-cash 1000 "
              "--changqian 50000 --cash-pool 20000", file=sys.stderr)
        sys.exit(1)

    targets = calc_targets(domestic_total, T_raw)

    order = [
        ("cash_pool", "现金账户"),
        ("changqian", "长钱账户"),
        ("bond",      "转债账户"),
        ("stock",     "股票账户"),
    ]

    deltas = {k: targets[k] - state[k]["total"] for k, _ in order}

    W = 66
    print()
    print("=" * W)
    print(f"  ARK-QUANT 组合分配器  ·  {date.today()}  ·  温度: {int(T_raw)} / 100")
    print("=" * W)
    print(f"\n国内总资产: {domestic_total:,.0f} 元"
          + (f"（海外长钱 {overseas:,.0f} 元另计）" if overseas > 0 else ""))
    print()
    print(f"  {'账户':<10}{'当前(元)':>13}{'目标(元)':>13}{'差额(元)':>12}  动作")
    print("  " + "─" * (W - 2))

    action_labels = {
        "cash_pool": ("流入", "流出"),
        "changqian": ("申购", "赎回"),
        "bond":      ("转入", "转出"),
        "stock":     ("转入", "转出"),
    }

    for key, label in order:
        cur = state[key]["total"]
        tgt = targets[key]
        d = deltas[key]
        if abs(d) < _THRESHOLD:
            action = "持平"
        else:
            pos, neg = action_labels[key]
            action = pos if d > 0 else neg
        print(f"  {label:<10}{_money(cur)}{_money(tgt)}{_delta(d):>12}  {action}")

    print("  " + "─" * (W - 2))
    print(f"  {'合计':<10}{_money(domestic_total)}{_money(sum(targets.values()))}")

    # ── Transfer plan ──────────────────────────────────────────────────────────
    steps = build_transfer_plan(deltas)
    print("\n资金调拨计划:")
    if not steps:
        print("  各账户已达目标仓位，无需调拨。")
    else:
        numerals = "①②③④⑤⑥⑦⑧"
        for i, step in enumerate(steps):
            print(f"  {numerals[i]} {step}")

    # execution sequence note
    over = [k for k in ("bond", "stock") if deltas[k] < -_THRESHOLD]
    under = [k for k in ("bond", "stock") if deltas[k] > _THRESHOLD]
    if over and under:
        print("  → 建议顺序：先轮动超仓账户（释放资金），再银证划转，再轮动欠仓账户。")
    elif over:
        print("  → 建议顺序：先执行超仓账户策略轮动（卖出释放资金），再银证转出。")
    elif under:
        print("  → 建议顺序：先银证转入，再执行欠仓账户策略轮动。")

    # ── size_orders hints ──────────────────────────────────────────────────────
    bond_avail  = state["bond"]["cash"]  + max(0.0, deltas["bond"])
    stock_avail = state["stock"]["cash"] + max(0.0, deltas["stock"])

    print("\n调拨完成后策略可用现金:")
    print(f"  转债 {state['bond']['cash']:>10,.2f} 元"
          + (f" + 转入 {deltas['bond']:,.0f}" if deltas["bond"] > _THRESHOLD else "")
          + f"  =  {bond_avail:,.2f} 元")
    print(f"    python3 -m strategies.cb_rotation.size_orders --cash {bond_avail:.0f}")
    print()
    print(f"  股票 {state['stock']['cash']:>10,.2f} 元"
          + (f" + 转入 {deltas['stock']:,.0f}" if deltas["stock"] > _THRESHOLD else "")
          + f"  =  {stock_avail:,.2f} 元")
    print(f"    python3 -m strategies.stock_smallcap.size_orders --cash {stock_avail:.0f}")

    print("=" * W)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="顶层组合分配器 — 生成各账户目标金额及资金调拨计划",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--temperature", "-t", type=float, required=True,
                   metavar="T",
                   help="全市场温度 0-100 (来源: youzhiyouxing.cn/data)")
    p.add_argument("--stock",       type=float, metavar="V", help="股票账户总市值")
    p.add_argument("--stock-cash",  type=float, metavar="V", help="股票账户内现金")
    p.add_argument("--bond",        type=float, metavar="V", help="转债账户总市值")
    p.add_argument("--bond-cash",   type=float, metavar="V", help="转债账户内现金")
    p.add_argument("--changqian",   type=float, metavar="V", help="长钱账户总净值")
    p.add_argument("--cash-pool",   type=float, metavar="V", help="现金账户余额")
    p.add_argument("--overseas",    type=float, metavar="V", help="海外长钱总净值")
    p.add_argument("--no-save", action="store_true", help="不更新 accounts_state.json")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    T_raw = args.temperature
    if not (0 <= T_raw <= 100):
        sys.exit("错误: --temperature 必须在 0-100 之间")

    state = load_state()

    # Apply CLI overrides (only update fields explicitly provided)
    if args.stock      is not None: state["stock"]["total"]      = args.stock
    if args.stock_cash is not None: state["stock"]["cash"]       = args.stock_cash
    if args.bond       is not None: state["bond"]["total"]       = args.bond
    if args.bond_cash  is not None: state["bond"]["cash"]        = args.bond_cash
    if args.changqian  is not None: state["changqian"]["total"]  = args.changqian
    if args.cash_pool  is not None: state["cash_pool"]["total"]  = args.cash_pool
    if args.overseas   is not None: state["overseas"]["total"]   = args.overseas

    state["temperature"] = T_raw
    state["updated_at"]  = str(date.today())

    print_report(T_raw, state)

    if not args.no_save:
        save_state(state)
        print(f"  (已保存至 {STATE_FILE.relative_to(Path(__file__).parent)})")


if __name__ == "__main__":
    main()
