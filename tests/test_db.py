"""tests/test_db.py — datasource/db.py 单元测试"""
import unittest
from datetime import date
import pandas as pd
from datasource import db


class TestDb(unittest.TestCase):
    def setUp(self):
        # 用内存库隔离测试
        import sqlite3
        db._TEST_CONN = sqlite3.connect(":memory:")
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_insert_and_get_account_snapshot(self):
        db.insert_account_snapshot("2026-06-29", 45.0, 209555, 274, 227183, 110, 110606, 59013, 93030)
        snap = db.get_latest_account_snapshot()
        self.assertEqual(snap["snapshot_date"], "2026-06-29")
        self.assertAlmostEqual(snap["temperature"], 45.0)

    def test_insert_cb_rankings(self):
        run_id = db.insert_strategy_run("cb", date(2026, 6, 27))
        df = pd.DataFrame([
            {"bond_code": "113062", "bond_name": "常银转债", "cb_price": 127.74,
             "premium_rate": 13.29, "double_low": 141.03, "score": 0.824},
        ])
        db.insert_cb_rankings(run_id, df)
        rows = db.get_rankings("cb", "2026-06-27")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bond_code"], "113062")
        self.assertEqual(rows[0]["rank"], 1)

    def test_insert_stock_rankings(self):
        run_id = db.insert_strategy_run("stock", date(2026, 6, 27))
        df = pd.DataFrame([
            {"rank": 1, "stock_code": "600455", "stock_name_q": "博通股份",
             "total_mv_yuan": 1.357e9, "pe_ttm": 29.76, "roe_pct": 13.83},
        ])
        db.insert_stock_rankings(run_id, df)
        rows = db.get_rankings("stock", "2026-06-27")
        self.assertEqual(rows[0]["stock_code"], "600455")
        self.assertAlmostEqual(rows[0]["market_cap"], 13.57, places=1)

    def test_insert_positions(self):
        db.insert_positions("cb", "2026-06-29", [
            {"code": "113062", "name": "常银转债", "shares": 90},
        ])
        rows = db.get_latest_positions("cb")
        self.assertEqual(rows[0]["code"], "113062")
        self.assertEqual(rows[0]["shares"], 90)

    def test_trade_date_is_next_weekday(self):
        run_id = db.insert_strategy_run("cb", date(2026, 6, 26))  # Friday
        rows = db.get_rankings("cb", "2026-06-26")  # empty but run exists
        with db.get_connection() as conn:
            r = conn.execute("SELECT trade_date FROM strategy_runs WHERE id=?", (run_id,)).fetchone()
        self.assertEqual(r["trade_date"], "2026-06-29")  # Monday

    def test_account_history(self):
        db.insert_account_snapshot("2026-06-22", 52.0, 200000, 300, 220000, 200, 105000, 60000, 90000)
        db.insert_account_snapshot("2026-06-29", 45.0, 209555, 274, 227183, 110, 110606, 59013, 93030)
        history = db.get_account_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["snapshot_date"], "2026-06-22")
