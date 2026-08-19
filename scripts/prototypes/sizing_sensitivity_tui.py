"""THROWAWAY PROTOTYPE — interactive sensitivity analysis for a frozen CB plan."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from sizing_sensitivity_logic import PlanInput, Target, add_lot, evaluate, most_underweight_affordable_rank, remove_lot


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "ark_quant.db"


def _cb_fee(turnover: float) -> float:
    return max(10.0, turnover * 0.0001) if turnover > 0 else 0.0


def load_plan(db_path: Path, plan_id: str | None) -> PlanInput:
    connection = sqlite3.connect(db_path)
    try:
        if plan_id:
            row = connection.execute(
                "SELECT plan_id, plan_json FROM generated_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT plan_id, plan_json FROM generated_plans WHERE status = 'complete' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise SystemExit("没有可读取的已完成计划")

    stored_plan_id, raw_plan = row
    plan = json.loads(raw_plan)
    account_plan = next(
        item
        for item in plan["execution_read_model"]["account_trading_plans"]
        if item["account_id"] == "cb"
    )
    orders = plan["cb"]["orders"]
    targets = []
    mandatory_sell_proceeds = 0.0
    for order in orders:
        action = order["action"]
        price = float(order.get("price", order.get("reference_price")))
        if action == "SELL":
            mandatory_sell_proceeds += abs(int(order["delta_shares"])) * price
            continue
        targets.append(
            Target(
                rank=len(targets) + 1,
                code=str(order["bond_code"]),
                name=order.get("bond_name", ""),
                price=price,
                current_shares=int(order["current_shares"]),
                baseline_shares=int(order["target_shares"]),
                lot=10,
            )
        )
    cash = account_plan["cash"]
    return PlanInput(
        plan_id=stored_plan_id,
        starting_cash=float(cash["starting_available"]),
        transfer_delta=float(cash["transfer_in"]) - float(cash["transfer_out"]),
        mandatory_sell_proceeds=mandatory_sell_proceeds,
        targets=tuple(targets),
        fee=_cb_fee,
    )


def crossover_case() -> PlanInput:
    """20 targets at 4.69%; a 10-lot buy raises one target to 5.36%."""
    targets = tuple(
        Target(
            rank=index + 1,
            code=f"TEST{index + 1:02}",
            name=f"高价样本{index + 1}",
            price=130.0,
            current_shares=70,
            baseline_shares=70,
            lot=10,
        )
        for index in range(20)
    )
    return PlanInput(
        plan_id="synthetic-crossover",
        starting_cash=12_000.0,
        transfer_delta=0.0,
        mandatory_sell_proceeds=0.0,
        targets=targets,
        fee=_cb_fee,
    )


def render(plan: PlanInput, shares: tuple[int, ...]) -> None:
    metrics = evaluate(plan, shares)
    print("\033[2J\033[H", end="")
    print("\033[1m账户内交易定额敏感性原型（只读 / throwaway）\033[0m")
    print(f"计划: {plan.plan_id}  目标数: {len(plan.targets)}  费用: max(10 元, 总成交额 × 0.01%)")
    print(
        f"现金余量 ¥{metrics.cash_left:,.2f}  费用 ¥{metrics.fee:,.2f}  "
        f"买入 ¥{metrics.buys:,.2f}  卖出 ¥{metrics.sells:,.2f}  订单 {metrics.nonzero_orders} 笔"
    )
    print(
        f"最大偏离 {metrics.max_weight_deviation:.2%}  总绝对偏离 {metrics.total_absolute_deviation:.2%}  "
        f"总平方偏离 {metrics.total_squared_deviation:.4%}"
    )
    print("\n排名  代码    标的        现持仓  试验目标  价格     计划后权重")
    total = metrics.total_after_fee
    for target, final_shares in zip(plan.targets, shares):
        weight = (final_shares * target.price / total) if total else 0.0
        print(
            f"{target.rank:>2}  {target.code:<6}  {target.name:<10.10}  {target.current_shares:>5}  "
            f"{final_shares:>8}  {target.price:>6.3f}  {weight:>8.2%}"
        )
    print("\n\033[1m命令\033[0m  add <排名>  remove <排名>  fill  reset  quit")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--plan-id")
    parser.add_argument("--case", choices=["latest", "crossover"], default="latest")
    args = parser.parse_args()
    plan = crossover_case() if args.case == "crossover" else load_plan(args.db, args.plan_id)
    shares = tuple(target.baseline_shares for target in plan.targets)
    while True:
        render(plan, shares)
        command = input("> ").strip().lower()
        if command in {"q", "quit"}:
            return
        if command == "reset":
            shares = tuple(target.baseline_shares for target in plan.targets)
            continue
        if command == "fill":
            rank = most_underweight_affordable_rank(plan, shares)
            if rank is not None:
                shares = add_lot(plan, shares, rank)
            continue
        verb, _, value = command.partition(" ")
        try:
            rank = int(value)
            if not 1 <= rank <= len(plan.targets):
                raise ValueError("排名超出范围")
            shares = add_lot(plan, shares, rank) if verb == "add" else remove_lot(plan, shares, rank)
        except ValueError:
            input("命令无效；按回车继续")


if __name__ == "__main__":
    main()
