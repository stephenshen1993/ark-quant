"""Smoke replay stock sizing from immutable inputs without writing data."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.stock_smallcap.target_sizing import size_target_state  # noqa: E402


def _frozen_stock_inputs(plan: dict) -> tuple[pd.DataFrame, pd.DataFrame, float, dict[str, float]] | None:
    stock = plan.get("stock") or {}
    orders = stock.get("orders") or []
    summary = stock.get("summary") or {}
    provenance = (plan.get("snapshot") or {}).get("input_provenance") or {}
    rankings = (
        ((provenance.get("strategy_rankings") or {}).get("stock") or {}).get("items")
        or []
    )
    frozen_prices = (plan.get("price_snapshot") or {}).get("stock") or {}
    required_summary_fields = {"starting_cash", "transfer_delta"}
    if (
        len(rankings) != 20
        or not orders
        or not frozen_prices
        or not required_summary_fields <= summary.keys()
    ):
        return None

    positions = [
        {
            "stock_code": order["stock_code"],
            "stock_name": order.get("stock_name", ""),
            "shares": order.get("current_shares", 0),
        }
        for order in orders
        if order.get("current_shares", 0) > 0
    ]
    budget = float(summary["starting_cash"] or 0) + float(
        summary["transfer_delta"] or 0
    )
    prices = {str(code): float(price) for code, price in frozen_prices.items()}
    return pd.DataFrame(rankings), pd.DataFrame(positions), budget, prices


def _assert_smoke_replay_invariants(sheet: pd.DataFrame, summary: dict) -> None:
    if summary.get("solver_status") != "OPTIMAL":
        raise AssertionError("historical smoke replay lacks an optimal solver status")
    if summary.get("cash_left", -1) < 0:
        raise AssertionError("historical replay overspent available cash")

    unauthorized_sells = sheet.loc[
        (sheet["delta_shares"] < 0)
        & ~sheet["execution_reason"].isin(
            {"mandatory_exit", "mandatory_risk_reduction"}
        )
    ]
    if not unauthorized_sells.empty:
        raise AssertionError("historical replay emitted an unauthorized ordinary sale")

    risk_deltas = sheet.loc[
        sheet["execution_reason"] == "mandatory_risk_reduction",
        "delta_shares",
    ]
    if any(abs(int(delta)) % 100 for delta in risk_deltas):
        raise AssertionError("historical replay emitted a non-lot risk reduction")


def smoke_replay_historical_stock_plans(database: Path) -> dict[str, int | str]:
    """Check deterministic reconstruction and execution invariants, not optimality."""
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """SELECT plan_id, plan_json
               FROM generated_plans
               WHERE status IN ('complete', 'stale')
               ORDER BY created_at, plan_id"""
        ).fetchall()
    finally:
        connection.close()

    replayed = 0
    skipped = 0
    for plan_id, raw_plan in rows:
        frozen = _frozen_stock_inputs(json.loads(raw_plan))
        if frozen is None:
            skipped += 1
            continue
        rankings, positions, budget, prices = frozen
        first_sheet, first_summary = size_target_state(
            rankings,
            positions,
            budget,
            prices,
        )
        second_sheet, second_summary = size_target_state(
            rankings,
            positions,
            budget,
            prices,
        )
        _assert_smoke_replay_invariants(first_sheet, first_summary)
        if not first_sheet.equals(second_sheet) or first_summary != second_summary:
            raise AssertionError(f"historical replay is not deterministic: {plan_id}")
        replayed += 1

    if replayed == 0:
        raise RuntimeError("no historical plan contains complete frozen stock inputs")
    return {
        "result": "SMOKE_PASS",
        "replayed": replayed,
        "skipped_incomplete_frozen_inputs": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "data" / "ark_quant.db",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            smoke_replay_historical_stock_plans(args.database),
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
