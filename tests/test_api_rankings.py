import unittest
import sqlite3
from datetime import date

import pandas as pd
from fastapi.testclient import TestClient

from datasource import db


class TestRankingsApi(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()
        run_id = db.insert_strategy_run("cb", date(2026, 6, 27))
        df = pd.DataFrame([{
            "bond_code": "113062", "bond_name": "常银转债",
            "cb_price": 127.74, "premium_rate": 13.29, "double_low": 141.03, "score": 0.824,
        }])
        db.insert_cb_rankings(run_id, df)
        from app.main import app
        self.client = TestClient(app)

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_get_dates(self):
        r = self.client.get("/api/rankings/cb/dates")
        self.assertIn("2026-06-27", r.json())

    def test_get_latest(self):
        r = self.client.get("/api/rankings/cb/latest")
        data = r.json()
        self.assertEqual(data["data_date"], "2026-06-27")
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["bond_code"], "113062")

    def test_export_markdown(self):
        r = self.client.get("/api/rankings/cb/2026-06-27/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("常银转债", r.text)
        self.assertIn("| 排名 |", r.text)
