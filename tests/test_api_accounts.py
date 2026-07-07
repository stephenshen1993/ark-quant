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
        r = self.client.get("/api/account/summary")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json())

    def test_post_context_updates_only_context(self):
        r = self.client.post("/api/account/context", json={
            "snapshot_date": "2026-06-29",
            "temperature": 45.0,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["snapshot_date"], "2026-06-29")
        self.assertEqual(r.json()["temperature"], 45.0)
        self.assertEqual(r.json()["account_updated_at"], {})

    def test_context_and_account_snapshots_are_aggregated(self):
        self.client.post("/api/account/context", json={
            "snapshot_date": "2026-06-29",
            "temperature": 45.0,
        })
        self.client.post("/api/account/stock/snapshot", json={
            "snapshot_date": "2026-06-29",
            "total": 209555,
            "cash": 274,
        })
        self.client.post("/api/account/cb/snapshot", json={
            "snapshot_date": "2026-06-29",
            "total": 227183,
            "cash": 110,
        })
        r = self.client.get("/api/account/summary")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["temperature"], 45.0)
        self.assertEqual(r.json()["stock_total"], 209555)
        self.assertEqual(r.json()["stock_cash"], 274)
        self.assertEqual(r.json()["bond_total"], 227183)
        self.assertEqual(r.json()["bond_cash"], 110)

    def test_account_updates_are_per_account(self):
        r = self.client.post("/api/account/cash/snapshot", json={
            "snapshot_date": "2026-06-29",
            "total": 59013,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["cash_pool"], 59013)
        self.assertEqual(set(r.json()["account_updated_at"].keys()), {"cash"})

        r2 = self.client.post("/api/account/changqian/snapshot", json={
            "snapshot_date": "2026-06-29",
            "total": 111000,
        })
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["changqian_total"], 111000)
        self.assertEqual(set(r2.json()["account_updated_at"].keys()), {"cash", "changqian"})

    def test_post_and_get_positions(self):
        payload = [{"code": "113062", "name": "常银转债", "shares": 90}]
        r = self.client.post("/api/positions/cb?position_date=2026-06-29", json=payload)
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get("/api/positions/cb")
        self.assertEqual(r2.json()[0]["code"], "113062")
