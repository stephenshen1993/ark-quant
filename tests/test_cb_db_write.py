"""tests/test_cb_db_write.py — integration tests for CB strategy DB writes.

Tests use an in-memory SQLite database via db._TEST_CONN injection so they
never touch the real data/ark_quant.db file.
"""
import sqlite3
import unittest
from datetime import date

import pandas as pd

from datasource import db


class TestCbDbWrite(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:")
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_insert_cb_rankings_and_orders(self):
        """insert_strategy_run + insert_cb_rankings => get_rankings returns correct row."""
        run_id = db.insert_strategy_run("cb", date(2026, 6, 27))
        ranked_df = pd.DataFrame([{
            "bond_code": "113062", "bond_name": "常银转债",
            "cb_price": 127.74, "premium_rate": 13.29,
            "double_low": 141.03, "score": 0.824, "target_weight": 0.05,
        }])
        db.insert_cb_rankings(run_id, ranked_df)
        rows = db.get_rankings("cb", "2026-06-27")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bond_code"], "113062")
        self.assertEqual(rows[0]["rank"], 1)

    def test_insert_cb_orders(self):
        """insert_strategy_run + insert_cb_orders => get_orders returns correct row."""
        run_id = db.insert_strategy_run("cb", date(2026, 6, 27))
        orders_df = pd.DataFrame([{
            "action": "BUY", "bond_code": "123150", "bond_name": "九强转债",
            "price": 128.67, "delta_shares": 90, "amount": 11580.3,
            "current_shares": 0, "target_shares": 90,
        }])
        db.insert_cb_orders(run_id, orders_df)
        orders = db.get_orders("cb", "2026-06-27")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["action"], "BUY")

    def test_get_latest_run_id(self):
        """get_latest_run_id returns the most recently inserted run."""
        id1 = db.insert_strategy_run("cb", date(2026, 6, 26))
        id2 = db.insert_strategy_run("cb", date(2026, 6, 27))
        self.assertEqual(db.get_latest_run_id("cb"), id2)
        self.assertGreater(id2, id1)

    def test_rankings_ranking_order(self):
        """Multiple rows are ranked in insertion order (i.e., score order from run())."""
        run_id = db.insert_strategy_run("cb", date(2026, 6, 27))
        ranked_df = pd.DataFrame([
            {"bond_code": "113062", "bond_name": "常银转债",
             "cb_price": 127.74, "premium_rate": 13.29, "double_low": 141.03, "score": 0.9},
            {"bond_code": "123150", "bond_name": "九强转债",
             "cb_price": 128.67, "premium_rate": 12.00, "double_low": 140.67, "score": 0.8},
        ])
        db.insert_cb_rankings(run_id, ranked_df)
        rows = db.get_rankings("cb", "2026-06-27")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["bond_code"], "113062")
        self.assertEqual(rows[1]["rank"], 2)
        self.assertEqual(rows[1]["bond_code"], "123150")

    def test_no_run_returns_empty(self):
        """get_rankings on a date with no run returns empty list."""
        rows = db.get_rankings("cb", "2099-01-01")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
