import sqlite3
import unittest
from datetime import date

import pandas as pd

from datasource import db, strategy_store


class TestStrategyStore(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:")
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_creates_complete_run_and_reads_rankings_without_db_facade(self):
        run_id = strategy_store.create_complete_strategy_run(
            db._TEST_CONN,
            "cb",
            date(2026, 6, 29),
            None,
            pd.DataFrame([{
                "bond_code": "113062",
                "bond_name": "常银转债",
                "cb_price": 126.8,
                "premium_rate": 10,
                "double_low": 136.8,
                "score": 0.9,
            }]),
        )

        self.assertEqual(strategy_store.get_latest_run_id(db._TEST_CONN, "cb"), run_id)
        rankings = strategy_store.get_rankings(db._TEST_CONN, "cb", "2026-06-29")
        self.assertEqual(rankings[0]["bond_code"], "113062")
        self.assertEqual(rankings[0]["trade_date"], "2026-06-30")
