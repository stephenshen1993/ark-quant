import sqlite3
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from app import order_sizing
from app.plan_service import PlanServiceError
from datasource import db


class TestOrderSizing(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:")
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()
        db.insert_account_context("2026-06-29", 45.0)
        db.insert_account_value_snapshot("cb", "2026-06-29", 227183, 110)
        self.cb_run_id = db.insert_strategy_run("cb", date(2026, 6, 29))
        db.insert_cb_rankings(self.cb_run_id, pd.DataFrame([
            {
                "bond_code": "113062",
                "bond_name": "常银转债",
                "cb_price": 126.80,
                "premium_rate": 10.0,
                "double_low": 136.8,
                "score": 0.9,
            }
        ]))
        db.insert_positions("cb", "2026-06-29", [{
            "code": "113062",
            "name": "常银转债",
            "shares": 10,
        }])

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_sizes_cb_orders_without_http_route(self):
        with patch(
            "datasource.market.fetch_cb_prices_tencent",
            return_value={"113062": 126.80},
        ):
            result = order_sizing.size_cb_orders(10000, plan_date="2026-06-29")

        self.assertGreater(len(result["orders"]), 0)
        self.assertEqual(result["summary"]["starting_cash"], 110)
        persisted = db.get_orders("cb", "2026-06-29")
        self.assertGreater(len(persisted), 0)

    def test_invalid_strategy_uses_plan_service_error(self):
        with self.assertRaises(PlanServiceError) as caught:
            order_sizing.size_strategy_orders("unknown")

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail["code"], "INVALID_STRATEGY")
