import unittest
import sqlite3
from datetime import date

import pandas as pd

from datasource import db


class TestStockDbWrite(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:")
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_insert_stock_rankings(self):
        run_id = db.insert_strategy_run("stock", date(2026, 6, 27))
        df = pd.DataFrame([{
            "rank": 1, "stock_code": "600455", "stock_name_q": "博通股份",
            "total_mv_yuan": 1.357e9, "pe_ttm": 29.76, "roe_pct": 13.83,
        }])
        db.insert_stock_rankings(run_id, df)
        rows = db.get_rankings("stock", "2026-06-27")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_code"], "600455")
        self.assertAlmostEqual(rows[0]["market_cap"], 13.57, places=1)

    def test_insert_stock_orders(self):
        run_id = db.insert_strategy_run("stock", date(2026, 6, 27))
        orders_df = pd.DataFrame([{
            "action": "SELL", "stock_code": "600051", "stock_name": "宁波联合",
            "price": 5.68, "delta_shares": -1800, "amount": 10224.0,
        }])
        db.insert_stock_orders(run_id, orders_df)
        orders = db.get_orders("stock", "2026-06-27")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["action"], "SELL")

    def test_rankings_stock_name_fallback(self):
        """stock_name_q 不存在时应 fallback 到 stock_name。"""
        run_id = db.insert_strategy_run("stock", date(2026, 6, 28))
        df = pd.DataFrame([{
            "rank": 1, "stock_code": "000001", "stock_name": "平安银行",
            "total_mv_yuan": 3.0e10, "pe_ttm": 6.5, "roe_pct": 11.0,
        }])
        db.insert_stock_rankings(run_id, df)
        rows = db.get_rankings("stock", "2026-06-28")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_name"], "平安银行")

    def test_get_latest_run_id_returns_none_when_empty(self):
        result = db.get_latest_run_id("stock")
        self.assertIsNone(result)

    def test_get_latest_run_id_returns_latest(self):
        id1 = db.insert_strategy_run("stock", date(2026, 6, 27))
        id2 = db.insert_strategy_run("stock", date(2026, 6, 28))
        self.assertEqual(db.get_latest_run_id("stock"), id2)
        self.assertGreater(id2, id1)
