import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.replay_stock_sizing import smoke_replay_historical_stock_plans


class StockSizingReplayTests(unittest.TestCase):
    def test_replays_complete_frozen_inputs_without_exposing_account_values(self):
        codes = [f"{600000 + index:06d}" for index in range(20)]
        rankings = [
            {
                "rank": index,
                "stock_code": code,
                "stock_name": f"stock-{index}",
            }
            for index, code in enumerate(codes, start=1)
        ]
        orders = [
            {
                "stock_code": code,
                "stock_name": f"stock-{index}",
                "current_shares": 0,
            }
            for index, code in enumerate(codes, start=1)
        ]
        plan = {
            "snapshot": {
                "input_provenance": {
                    "strategy_rankings": {"stock": {"items": rankings}}
                }
            },
            "price_snapshot": {"stock": {code: 10.0 for code in codes}},
            "stock": {
                "orders": orders,
                "summary": {"starting_cash": 400_000.0, "transfer_delta": 0.0},
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.db"
            connection = sqlite3.connect(database)
            connection.execute(
                """CREATE TABLE generated_plans (
                    plan_id TEXT,
                    status TEXT,
                    plan_json TEXT,
                    created_at TEXT
                )"""
            )
            connection.execute(
                "INSERT INTO generated_plans VALUES (?, ?, ?, ?)",
                ("plan-fixture", "complete", json.dumps(plan), "2026-08-15"),
            )
            connection.commit()
            connection.close()

            result = smoke_replay_historical_stock_plans(database)

        self.assertEqual(
            result,
            {
                "result": "SMOKE_PASS",
                "replayed": 1,
                "skipped_incomplete_frozen_inputs": 0,
            },
        )
        self.assertNotIn("cash", result)
        self.assertNotIn("positions", result)


if __name__ == "__main__":
    unittest.main()
