import unittest
import sqlite3
from fastapi.testclient import TestClient
from datasource import db


class TestAccountsApi(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()
        from app.main import app
        self.client = TestClient(app)

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_get_latest_returns_null_when_empty(self):
        r = self.client.get("/api/account/latest")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json())

    def test_post_and_get_snapshot(self):
        payload = {
            "snapshot_date": "2026-06-29", "temperature": 45.0,
            "stock_total": 209555, "stock_cash": 274,
            "bond_total": 227183, "bond_cash": 110,
            "changqian_total": 110606, "cash_pool": 59013, "overseas_total": 93030,
        }
        r = self.client.post("/api/account/snapshot", json=payload)
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get("/api/account/latest")
        self.assertEqual(r2.json()["temperature"], 45.0)

    def test_post_and_get_positions(self):
        payload = [{"code": "113062", "name": "常银转债", "shares": 90}]
        r = self.client.post("/api/positions/cb?position_date=2026-06-29", json=payload)
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get("/api/positions/cb")
        self.assertEqual(r2.json()[0]["code"], "113062")
