import sqlite3
import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi.testclient import TestClient

from datasource import db
from datasource.youzhiyouxing import DATA_URL

class TestPlansApi(unittest.TestCase):
    def setUp(self):
        self.temperature_clock = patch(
            "datasource.youzhiyouxing._now_shanghai",
            return_value=datetime(2026, 6, 29, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.temperature_clock.start()
        self.addCleanup(self.temperature_clock.stop)
        db._TEST_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()
        db._TEST_CONN.execute(
            """INSERT INTO market_temperatures
               (temperature,label,source_updated_at,source,fetched_at)
               VALUES (45.0,'正常','2026-06-29T15:00',?,'2026-06-29T15:30:00')""",
            (DATA_URL,),
        )
        db._TEST_CONN.commit()
        db.insert_account_context("2026-06-29", 45.0)
        db.insert_account_value_snapshot("stock", "2026-06-29", 209555, 274)
        db.insert_account_value_snapshot("cb", "2026-06-29", 227183, 110)
        db.insert_account_value_snapshot("changqian", "2026-06-29", 110606)
        db.insert_account_value_snapshot("cash", "2026-06-29", 59013)
        db.insert_account_value_snapshot("overseas", "2026-06-29", 93030)
        self.cb_run_id = db.insert_strategy_run("cb", date(2026, 6, 29))
        db.insert_cb_rankings(self.cb_run_id, pd.DataFrame([{
            "bond_code": "113062", "bond_name": "常银转债",
            "cb_price": 126.80, "premium_rate": 10.0, "double_low": 136.8, "score": 0.9,
        }]))
        db.insert_cb_orders(self.cb_run_id, pd.DataFrame([{
            "action": "BUY", "bond_code": "123150", "bond_name": "九强转债",
            "price": 128.67, "delta_shares": 90, "amount": 11580.3,
        }]))
        run_id2 = db.insert_strategy_run("stock", date(2026, 6, 29))
        db.insert_stock_rankings(run_id2, pd.DataFrame([{
            "rank": 1, "stock_code": "600051", "stock_name": "宁波联合",
            "total_mv_yuan": 1000000000, "pe_ttm": 10.0, "roe_pct": 12.0,
        }]))
        db.insert_stock_orders(run_id2, pd.DataFrame([{
            "action": "SELL", "stock_code": "600051", "stock_name": "宁波联合",
            "price": 5.68, "delta_shares": -1800, "amount": 10224.0,
        }]))
        db.insert_positions("cb", "2026-06-29", [{
            "code": "113062", "name": "常银转债", "shares": 10,
        }])
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
        self.assertIn("execution_sequence", data)
        self.assertIn("cb", data)
        self.assertIn("stock", data)
        self.assertGreater(len(data["cb"]["orders"]), 0)
        self.assertGreater(len(data["stock"]["orders"]), 0)
        self.assertLess(data["cb"]["summary"]["book_balance"], 110)
        self.assertGreater(data["stock"]["summary"]["book_balance"], 274)
        self.assertEqual(data["cb"]["data_date"], "2026-06-29")
        self.assertEqual(data["cb"]["trade_date"], "2026-06-30")
        self.assertEqual(data["stock"]["data_date"], "2026-06-29")
        self.assertEqual(data["stock"]["trade_date"], "2026-06-30")
        self.assertEqual([phase["phase"] for phase in data["execution_sequence"]], ["sell", "transfer", "buy"])

    def test_plan_returns_none_account_when_missing(self):
        db._TEST_CONN.execute("DELETE FROM account_contexts")
        db._TEST_CONN.execute("DELETE FROM account_value_snapshots")
        db._TEST_CONN.commit()
        r = self.client.get("/api/plan")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "PLAN_INPUT_DATE_MISMATCH")
        self.assertIn("account", {item["input"] for item in r.json()["detail"]["errors"]})

    def test_transfer_plan_does_not_require_strategy_rankings(self):
        db._TEST_CONN.execute("DELETE FROM cb_rankings")
        db._TEST_CONN.execute("DELETE FROM stock_rankings")
        db._TEST_CONN.commit()

        r = self.client.get("/api/plan/transfer")

        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["plan_date"], "2026-06-29")
        self.assertIn("transfer_steps", data)
        self.assertIn("transfer_deltas", data)
        self.assertNotIn("cb", data)
        self.assertNotIn("stock", data)

    def test_get_plan_returns_transfer_with_trade_errors_when_rankings_missing(self):
        db._TEST_CONN.execute("DELETE FROM cb_rankings")
        db._TEST_CONN.execute("DELETE FROM stock_rankings")
        db._TEST_CONN.commit()

        r = self.client.get("/api/plan")

        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("transfer_steps", data)
        self.assertEqual({e["input"] for e in data["trade_errors"]}, {"cb", "stock"})
        self.assertEqual(data["cb"]["rankings"], [])
        self.assertEqual(data["stock"]["rankings"], [])

    def test_plan_returns_rankings_when_no_orders(self):
        """缺 orders 时返回 rankings 作为降级数据"""
        # 写入榜单数据但不写 orders
        db.insert_cb_rankings(self.cb_run_id, pd.DataFrame([{
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
        r = self.client.post("/api/plan/cb/size-orders")
        self.assertEqual(r.status_code, 409)
        self.assertIn("PLAN_INPUT_DATE_MISMATCH", r.json()["detail"]["code"])

    def test_size_cb_orders_generates_and_persists_orders(self):
        db._TEST_CONN.execute("DELETE FROM cb_orders")
        db._TEST_CONN.execute("DELETE FROM cb_rankings")
        db._TEST_CONN.commit()
        db.insert_cb_rankings(self.cb_run_id, pd.DataFrame([
            {
                "bond_code": "113062", "bond_name": "常银转债",
                "cb_price": 126.80, "premium_rate": 10.0, "double_low": 136.8, "score": 0.9,
            },
            {
                "bond_code": "113052", "bond_name": "兴业转债",
                "cb_price": 115.09, "premium_rate": 12.0, "double_low": 127.09, "score": 0.8,
            },
        ]))

        with patch(
            "app.routers.plans._strategy_cash_after_transfer",
            return_value=30000,
        ), patch(
            "datasource.market.fetch_cb_prices_tencent",
            return_value={"113062": 126.80, "113052": 115.09},
        ):
            r = self.client.post("/api/plan/cb/size-orders")

        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertGreater(len(data["orders"]), 0)
        persisted = db.get_orders("cb", "2026-06-29")
        self.assertGreater(len(persisted), 0)

    def test_size_orders_rejects_insufficient_releasable_cash_before_persisting(self):
        db._TEST_CONN.execute("DELETE FROM cb_orders")
        db._TEST_CONN.commit()

        with patch(
            "app.routers.plans._strategy_cash_after_transfer",
            return_value=-5000,
        ), patch(
            "datasource.market.fetch_cb_prices_tencent",
            return_value={"113062": 126.80},
        ):
            r = self.client.post("/api/plan/cb/size-orders")

        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "INSUFFICIENT_RELEASABLE_CASH")
        self.assertEqual(db.get_orders("cb", "2026-06-29"), [])

    def test_sizing_cash_uses_available_cash_not_cash_balance(self):
        db.insert_account_value_snapshot("cb", "2026-06-29", 227183, 1110, 1000)
        account = db.get_current_account_summary()
        from app.routers.plans import _strategy_cash_after_transfer
        self.assertEqual(account["bond_cash"], 1110)
        self.assertEqual(account["bond_available_cash"], 110)
        self.assertEqual(_strategy_cash_after_transfer("cb", account, {"bond": 0}), 110)

    def test_size_cb_orders_reports_partial_quote_gaps(self):
        db._TEST_CONN.execute("DELETE FROM cb_orders")
        db._TEST_CONN.execute("DELETE FROM cb_rankings")
        db._TEST_CONN.commit()
        db.insert_cb_rankings(self.cb_run_id, pd.DataFrame([
            {
                "bond_code": "113062", "bond_name": "常银转债",
                "cb_price": 126.80, "premium_rate": 10.0, "double_low": 136.8, "score": 0.9,
            },
            {
                "bond_code": "113052", "bond_name": "兴业转债",
                "cb_price": 115.09, "premium_rate": 12.0, "double_low": 127.09, "score": 0.8,
            },
        ]))

        with patch(
            "app.routers.plans._strategy_cash_after_transfer",
            return_value=10000,
        ), patch(
            "datasource.market.fetch_cb_prices_tencent",
            return_value={"113062": 126.80},
        ):
            r = self.client.post("/api/plan/cb/size-orders")

        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json()["detail"]["code"], "DATA_SOURCE_UNAVAILABLE")
        self.assertIn("113052", r.json()["detail"]["message"])

    def test_size_orders_accepts_confirmed_empty_position_snapshot(self):
        db._TEST_CONN.execute("DELETE FROM cb_orders")
        db._TEST_CONN.execute("DELETE FROM position_snapshots WHERE strategy='cb'")
        db._TEST_CONN.commit()
        db.insert_positions("cb", "2026-06-29", [])

        with patch(
            "app.routers.plans._strategy_cash_after_transfer",
            return_value=10000,
        ), patch(
            "datasource.market.fetch_cb_prices_tencent",
            return_value={"113062": 126.80},
        ):
            r = self.client.post("/api/plan/cb/size-orders")

        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(order["action"] == "BUY" for order in r.json()["orders"]))

    def test_order_summary_reports_small_transfer_delta_without_transfer_step(self):
        with patch(
            "app.routers.plans._portfolio_targets_and_deltas",
            return_value=({}, {"stock": 0, "bond": 1, "changqian": 0, "cash_pool": -1}),
        ):
            r = self.client.get("/api/plan")

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["transfer_steps"], [])
        self.assertEqual(r.json()["cb"]["summary"]["transfer_delta"], 1)

    def test_overseas_stale_warns_without_blocking_domestic_plan(self):
        db.insert_account_value_snapshot("overseas", "2026-06-28", 93030)

        r = self.client.get("/api/plan")

        self.assertEqual(r.status_code, 200)
        warnings = r.json()["warnings"]
        self.assertEqual(warnings[0]["input"], "account.overseas")

    def test_monday_preopen_account_updates_are_valid_for_friday_plan(self):
        db._TEST_CONN.execute("DELETE FROM market_temperatures")
        db._TEST_CONN.execute(
            """INSERT INTO market_temperatures
               (temperature,label,source_updated_at,source,fetched_at)
               VALUES (45.0,'正常','2026-07-10T15:00',?,'2026-07-13T08:30:00')""",
            (DATA_URL,),
        )
        db._TEST_CONN.execute("DELETE FROM account_value_snapshots")
        db._TEST_CONN.commit()

        db.insert_account_value_snapshot("stock", "2026-07-13", 209555, 274)
        db.insert_account_value_snapshot("cb", "2026-07-13", 227183, 110)
        db.insert_account_value_snapshot("changqian", "2026-07-13", 110606)
        db.insert_account_value_snapshot("cash", "2026-07-13", 59013)
        run_id = db.create_complete_strategy_run("cb", date(2026, 7, 10), date(2026, 7, 13), pd.DataFrame([{
            "bond_code": "113062", "bond_name": "常银转债",
            "cb_price": 126.80, "premium_rate": 10.0, "double_low": 136.8, "score": 0.9,
        }]))
        stock_run_id = db.create_complete_strategy_run("stock", date(2026, 7, 10), date(2026, 7, 13), pd.DataFrame([{
            "rank": 1, "stock_code": "600051", "stock_name": "宁波联合",
            "total_mv_yuan": 1000000000, "pe_ttm": 10.0, "roe_pct": 12.0,
        }]))
        db.insert_cb_orders(run_id, pd.DataFrame([{
            "action": "HOLD", "bond_code": "113062", "bond_name": "常银转债",
            "price": 126.80, "delta_shares": 0, "amount": 0,
        }]))
        db.insert_stock_orders(stock_run_id, pd.DataFrame([{
            "action": "HOLD", "stock_code": "600051", "stock_name": "宁波联合",
            "price": 5.68, "delta_shares": 0, "amount": 0,
        }]))

        with patch(
            "datasource.youzhiyouxing._now_shanghai",
            return_value=datetime(2026, 7, 13, 8, 45, tzinfo=ZoneInfo("Asia/Shanghai")),
        ):
            r = self.client.get("/api/plan")

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["plan_date"], "2026-07-10")
        self.assertEqual(r.json()["cb"]["trade_date"], "2026-07-13")
