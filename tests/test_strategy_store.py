import sqlite3
import unittest
from datetime import date
from unittest.mock import patch

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

    def test_both_strategies_persist_next_exchange_date_after_holiday(self):
        with patch("datasource.trade_calendar.load_exchange_trading_days", return_value=[
            date(2026, 9, 30), date(2026, 10, 8),
        ]):
            for strategy in ("cb", "stock"):
                with self.subTest(strategy=strategy):
                    pending_id = strategy_store.insert_strategy_run(
                        db._TEST_CONN, strategy, date(2026, 9, 30),
                    )
                    pending = db._TEST_CONN.execute(
                        "SELECT trade_date FROM strategy_runs WHERE id=?", (pending_id,),
                    ).fetchone()
                    self.assertEqual(pending["trade_date"], "2026-10-08")
                    run_id = strategy_store.create_complete_strategy_run(
                        db._TEST_CONN, strategy, date(2026, 9, 30), None,
                        pd.DataFrame([{"bond_code": "113062", "stock_code": "600001"}]),
                    )
                    result = strategy_store.get_strategy_run(db._TEST_CONN, run_id, strategy)
                    self.assertEqual(result["trade_date"], "2026-10-08")

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
