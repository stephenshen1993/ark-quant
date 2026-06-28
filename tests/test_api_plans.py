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

    def test_missing_when_no_account(self):
        db._TEST_CONN.execute("DELETE FROM account_snapshots")
        db._TEST_CONN.commit()
        r = self.client.get("/api/plan")
        self.assertIn("account", r.json()["missing"])
