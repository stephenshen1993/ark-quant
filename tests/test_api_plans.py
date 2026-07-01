import unittest
import sqlite3
from datetime import date
import pandas as pd
from fastapi.testclient import TestClient
from datasource import db

class TestPlansApi(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()
        db.insert_account_snapshot("2026-06-29", 45.0, 209555, 274, 227183, 110, 110606, 59013, 93030)
        run_id = db.insert_strategy_run("cb", date(2026, 6, 27))
        db.insert_cb_orders(run_id, pd.DataFrame([{
            "action": "BUY", "bond_code": "123150", "bond_name": "九强转债",
            "price": 128.67, "delta_shares": 90, "amount": 11580.3,
        }]))
        run_id2 = db.insert_strategy_run("stock", date(2026, 6, 29))
        db.insert_stock_orders(run_id2, pd.DataFrame([{
            "action": "SELL", "stock_code": "600051", "stock_name": "宁波联合",
            "price": 5.68, "delta_shares": -1800, "amount": 10224.0,
        }]))
        from app.main import app
        self.client = TestClient(app)

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_get_plan_returns_all_sections(self):
        r = self.client.get("/api/plan")
        data = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertIn("transfer_steps", data)
        self.assertIn("cb", data)
        self.assertIn("stock", data)
        self.assertGreater(len(data["cb"]["orders"]), 0)
        self.assertGreater(len(data["stock"]["orders"]), 0)
        self.assertEqual(data["cb"]["data_date"], "2026-06-27")
        self.assertEqual(data["cb"]["trade_date"], "2026-06-29")
        self.assertEqual(data["stock"]["data_date"], "2026-06-29")
        self.assertEqual(data["stock"]["trade_date"], "2026-06-30")

    def test_plan_returns_none_account_when_missing(self):
        db._TEST_CONN.execute("DELETE FROM account_snapshots")
        db._TEST_CONN.commit()
        r = self.client.get("/api/plan")
        self.assertIsNone(r.json()["account"])
        self.assertEqual(r.json()["transfer_steps"], [])

    def test_plan_returns_rankings_when_no_orders(self):
        """缺 orders 时返回 rankings 作为降级数据"""
        # 写入榜单数据但不写 orders
        run_id = db.insert_strategy_run("cb", date(2026, 6, 27))
        db.insert_cb_rankings(run_id, pd.DataFrame([{
            "bond_code": "113062", "bond_name": "常银转债",
            "cb_price": 127.74, "premium_rate": 13.29, "double_low": 141.03, "score": 0.824,
        }]))
        # 清除已有 orders
        db._TEST_CONN.execute("DELETE FROM cb_orders")
        db._TEST_CONN.commit()

        r = self.client.get("/api/plan")
        data = r.json()
        self.assertEqual(len(data["cb"]["orders"]), 0)
        self.assertGreater(len(data["cb"]["rankings"]), 0)
        self.assertEqual(data["cb"]["rankings"][0]["bond_code"], "113062")

    def test_plan_rankings_empty_when_orders_exist(self):
        """有 orders 时不返回冗余 rankings"""
        r = self.client.get("/api/plan")
        data = r.json()
        self.assertGreater(len(data["cb"]["orders"]), 0)
        self.assertEqual(data["cb"]["rankings"], [])

    def test_size_orders_missing_strategy(self):
        r = self.client.post("/api/plan/unknown/size-orders", json={"cash": 10000})
        self.assertEqual(r.status_code, 400)

    def test_size_orders_no_rankings(self):
        """没有榜单时 size-orders 返回 400"""
        db._TEST_CONN.execute("DELETE FROM cb_rankings")
        db._TEST_CONN.commit()
        r = self.client.post("/api/plan/cb/size-orders", json={"cash": 10000})
        self.assertEqual(r.status_code, 400)
        self.assertIn("NO_RANKINGS", r.json()["detail"]["code"])
