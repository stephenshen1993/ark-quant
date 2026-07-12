import unittest
import sqlite3
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_export_stock_markdown(self):
        run_id = db.insert_strategy_run("stock", date(2026, 6, 27))
        df = pd.DataFrame([{
            "rank": 1, "stock_code": "600455", "stock_name_q": "博通股份",
            "total_mv_yuan": 1.357e9, "pe_ttm": 29.76, "roe_pct": 13.83,
        }])
        db.insert_stock_rankings(run_id, df)
        r = self.client.get("/api/rankings/stock/2026-06-27/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("博通股份", r.text)
        self.assertIn("| 排名 |", r.text)

    def test_run_returns_exact_generated_run_during_same_date_reruns(self):
        generated_id = db.create_complete_strategy_run(
            "cb", date(2026, 6, 27), date(2026, 6, 29),
            pd.DataFrame([{"bond_code": "123150", "bond_name": "本次生成", "score": 0.9}]),
        )
        db.create_complete_strategy_run(
            "cb", date(2026, 6, 27), date(2026, 6, 29),
            pd.DataFrame([{"bond_code": "110000", "bond_name": "全局最新", "score": 0.8}]),
        )

        with patch(
            "app.routers.rankings._run_strategy_impl",
            return_value=SimpleNamespace(run_id=generated_id, data_date=date(2026, 6, 27)),
        ):
            response = self.client.post("/api/rankings/cb/run")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run_id"], generated_id)
        self.assertEqual(response.json()["items"][0]["bond_name"], "本次生成")

    def test_failed_run_keeps_older_complete_run_and_returns_structured_failure(self):
        old_run = db.get_latest_strategy_run("cb")
        with patch(
            "app.routers.rankings._run_strategy_impl",
            side_effect=RuntimeError("forced persistence failure"),
        ):
            response = self.client.post("/api/rankings/cb/run")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"]["code"], "STRATEGY_ERROR")
        self.assertEqual(db.get_latest_strategy_run("cb")["id"], old_run["id"])
