import sqlite3
import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import HTTPException
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
        self.assertEqual(
            set(data["cb"]["summary"]),
            {"starting_cash", "transfer_delta", "order_delta", "cash_left"},
        )
        self.assertEqual(
            set(data["stock"]["summary"]),
            {"starting_cash", "transfer_delta", "order_delta", "cash_left"},
        )
        self.assertLess(data["cb"]["summary"]["cash_left"], 110)
        self.assertGreater(data["stock"]["summary"]["cash_left"], 274)
        self.assertEqual(data["cb"]["data_date"], "2026-06-29")
        self.assertEqual(data["cb"]["trade_date"], "2026-06-30")
        self.assertEqual(data["stock"]["data_date"], "2026-06-29")
        self.assertEqual(data["stock"]["trade_date"], "2026-06-30")
        self.assertEqual([phase["phase"] for phase in data["execution_sequence"]], ["sell", "transfer", "buy"])

    def test_plan_service_builds_current_plan_without_http_route(self):
        from app.plan_service import build_current_plan

        data = build_current_plan()

        self.assertEqual(data["plan_date"], "2026-06-29")
        self.assertIn("fund_transfer", data)
        self.assertEqual([phase["phase"] for phase in data["execution_sequence"]], ["sell", "transfer", "buy"])
        self.assertGreater(len(data["cb"]["orders"]), 0)
        self.assertGreater(len(data["stock"]["orders"]), 0)

    def test_full_plan_uses_the_same_structured_fund_transfer_result(self):
        r = self.client.get("/api/plan")

        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue({"top_level", "a_internal", "cash"} <= set(data["fund_transfer"]))
        self.assertEqual(data["fund_transfer"]["a_internal"]["status"], "ready")
        self.assertNotEqual(data["transfer_deltas"]["stock"], 0)
        self.assertNotEqual(data["transfer_deltas"]["bond"], 0)
        self.assertTrue(data["transfer_steps"])
        self.assertFalse(any(step.startswith("A ") for step in data["transfer_steps"]))

    def test_generate_plan_runs_complete_plan_on_server_once(self):
        with patch(
            "app.order_sizing.size_cb_orders",
            return_value={
                "orders": [{
                    "action": "BUY", "bond_code": "113062", "bond_name": "常银转债",
                    "price": 126.80, "delta_shares": 10, "amount": 1268.0,
                }],
                "summary": {
                    "starting_cash": 110.0,
                    "transfer_delta": 0.0,
                    "order_delta": -1268.0,
                    "cash_left": 0.0,
                },
            },
        ) as cb_size, patch(
            "app.order_sizing.size_stock_orders",
            return_value={
                "orders": [{
                    "action": "SELL", "stock_code": "600051", "stock_name": "宁波联合",
                    "price": 5.68, "delta_shares": -100, "amount": 568.0,
                }],
                "summary": {
                    "starting_cash": 274.0,
                    "transfer_delta": 0.0,
                    "order_delta": 568.0,
                    "cash_left": 842.0,
                },
            },
        ) as stock_size:
            r = self.client.post("/api/plan/generate")

        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["generation"]["status"], "complete")
        self.assertEqual(data["generation"]["plan_date"], data["plan_date"])
        self.assertRegex(data["generation"]["plan_id"], r"^plan-2026-06-29-[0-9a-f]{8}$")
        saved = db.get_generated_plan(data["generation"]["plan_id"])
        self.assertIsNotNone(saved)
        self.assertEqual(saved["plan_date"], data["plan_date"])
        self.assertEqual(saved["plan"]["transfer_deltas"], data["transfer_deltas"])
        self.assertEqual(saved["plan"]["cb"]["orders"], data["cb"]["orders"])
        self.assertEqual(saved["plan"]["stock"]["orders"], data["stock"]["orders"])
        cb_batch = db.get_plan_order_batch(data["generation"]["plan_id"], "cb")
        stock_batch = db.get_plan_order_batch(data["generation"]["plan_id"], "stock")
        self.assertEqual(cb_batch["orders"], data["cb"]["orders"])
        self.assertEqual(cb_batch["summary"], data["cb"]["summary"])
        self.assertEqual(stock_batch["orders"], data["stock"]["orders"])
        self.assertEqual(stock_batch["summary"], data["stock"]["summary"])
        self.assertEqual(data["cb"]["orders"][0]["bond_code"], "113062")
        self.assertEqual(data["stock"]["orders"][0]["stock_code"], "600051")
        self.assertEqual([phase["phase"] for phase in data["execution_sequence"]], ["sell", "transfer", "buy"])
        cb_size.assert_called_once()
        stock_size.assert_called_once()

    def test_generate_plan_records_failed_plan_when_order_stage_fails(self):
        cb_orders = [{
            "action": "BUY", "bond_code": "113062", "bond_name": "常银转债",
            "price": 126.80, "delta_shares": 10, "amount": 1268.0,
        }]
        with patch(
            "app.order_sizing.size_cb_orders",
            return_value={
                "orders": cb_orders,
                "summary": {
                    "starting_cash": 110.0,
                    "transfer_delta": 0.0,
                    "order_delta": -1268.0,
                    "cash_left": 0.0,
                },
            },
        ), patch(
            "app.order_sizing.size_stock_orders",
            side_effect=HTTPException(
                status_code=409,
                detail={"code": "STOCK_SIZING_FAILED", "message": "stock sizing failed"},
            ),
        ):
            r = self.client.post("/api/plan/generate")

        self.assertEqual(r.status_code, 409)
        detail = r.json()["detail"]
        self.assertRegex(detail["plan_id"], r"^plan-2026-06-29-[0-9a-f]{8}$")
        saved = db.get_generated_plan(detail["plan_id"])
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["error"]["stage"], "stock_orders")
        self.assertEqual(saved["error"]["code"], "STOCK_SIZING_FAILED")
        self.assertEqual(saved["plan"]["cb"]["orders"], cb_orders)

    def test_account_changes_mark_generated_plans_stale(self):
        with patch(
            "app.order_sizing.size_cb_orders",
            return_value={"orders": [], "summary": {
                "starting_cash": 110.0, "transfer_delta": 0.0,
                "order_delta": 0.0, "cash_left": 110.0,
            }},
        ), patch(
            "app.order_sizing.size_stock_orders",
            return_value={"orders": [], "summary": {
                "starting_cash": 274.0, "transfer_delta": 0.0,
                "order_delta": 0.0, "cash_left": 274.0,
            }},
        ):
            generated = self.client.post("/api/plan/generate").json()

        plan_id = generated["generation"]["plan_id"]
        self.assertEqual(db.get_generated_plan(plan_id)["status"], "complete")

        r = self.client.post("/api/account/stock/snapshot", json={
            "snapshot_date": "2026-06-29",
            "total": 210000,
            "cash": 500,
            "frozen_cash": 0,
        })

        self.assertEqual(r.status_code, 200)
        saved = db.get_generated_plan(plan_id)
        self.assertEqual(saved["status"], "stale")
        self.assertEqual(saved["error"]["code"], "PLAN_INPUTS_CHANGED")

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
        self.assertEqual(data["fund_transfer"]["top_level"]["status"], "ready")
        self.assertEqual(data["fund_transfer"]["a_internal"]["status"], "not_requested")
        self.assertNotIn("cb", data)
        self.assertNotIn("stock", data)

    def test_transfer_plan_exposes_structured_fund_transfer_result(self):
        r = self.client.get("/api/plan/transfer")

        self.assertEqual(r.status_code, 200)
        self.assertTrue({"top_level", "a_internal", "cash"} <= set(r.json()["fund_transfer"]))

    def test_transfer_plan_requires_current_overseas_snapshot(self):
        db._TEST_CONN.execute(
            "DELETE FROM account_value_snapshots WHERE account_id='overseas'"
        )
        db._TEST_CONN.commit()

        r = self.client.get("/api/plan/transfer")

        self.assertEqual(r.status_code, 409)
        self.assertIn("account.overseas", {e["input"] for e in r.json()["detail"]["errors"]})

    def test_order_sizing_stops_when_complete_funding_inputs_are_missing(self):
        db._TEST_CONN.execute(
            "DELETE FROM account_value_snapshots WHERE account_id='overseas'"
        )
        db._TEST_CONN.commit()

        r = self.client.post("/api/plan/cb/size-orders")

        self.assertEqual(r.status_code, 409)
        self.assertIn("account.overseas", {e["input"] for e in r.json()["detail"]["errors"]})

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

    def test_get_plan_reports_missing_rankings_even_when_orders_exist(self):
        db._TEST_CONN.execute("DELETE FROM cb_rankings")
        db._TEST_CONN.commit()
        raw_orders = db._TEST_CONN.execute("SELECT COUNT(*) FROM cb_orders").fetchone()[0]

        r = self.client.get("/api/plan")

        self.assertEqual(r.status_code, 200)
        data = r.json()
        cb_error = next(item for item in data["trade_errors"] if item["input"] == "cb")
        self.assertEqual(cb_error["expected"], "2026-06-29")
        self.assertGreater(raw_orders, 0)
        self.assertEqual(data["cb"]["orders"], [])

    def test_full_plan_reports_missing_latest_strategy_inputs(self):
        db._TEST_CONN.execute(
            """INSERT INTO market_temperatures
               (temperature,label,source_updated_at,source,fetched_at)
               VALUES (45.0,'正常','2026-06-30T15:00',?,'2026-06-30T20:00:00')""",
            (DATA_URL,),
        )
        db._TEST_CONN.commit()
        db.insert_account_context("2026-06-30", 45.0)
        db.insert_account_value_snapshot("stock", "2026-06-30", 209555, 274)
        db.insert_account_value_snapshot("cb", "2026-06-30", 227183, 110)
        db.insert_account_value_snapshot("changqian", "2026-06-30", 110606)
        db.insert_account_value_snapshot("cash", "2026-06-30", 59013)
        db.insert_account_value_snapshot("overseas", "2026-06-30", 93030)
        stock_run_id = db.create_complete_strategy_run("stock", date(2026, 6, 30), date(2026, 7, 1), pd.DataFrame([{
            "rank": 1, "stock_code": "600051", "stock_name": "宁波联合",
            "total_mv_yuan": 1000000000, "pe_ttm": 10.0, "roe_pct": 12.0,
        }]))
        db.insert_stock_orders(stock_run_id, pd.DataFrame([{
            "action": "HOLD", "stock_code": "600051", "stock_name": "宁波联合",
            "price": 5.68, "delta_shares": 0, "amount": 0,
        }]))

        with patch(
            "datasource.youzhiyouxing._now_shanghai",
            return_value=datetime(2026, 6, 30, 20, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        ):
            r = self.client.get("/api/plan")

        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["market_temperature"]["updated_at"][:10], "2026-06-30")
        self.assertEqual(data["plan_date"], "2026-06-30")
        self.assertEqual(data["cb"]["data_date"], "2026-06-30")
        self.assertEqual(data["stock"]["data_date"], "2026-06-30")
        self.assertEqual({e["input"] for e in data["trade_errors"]}, {"cb"})
        self.assertEqual(data["cb"]["rankings"], [])
        self.assertGreater(len(data["stock"]["orders"]), 0)

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
        """没有榜单时 size-orders 在输入一致性校验阶段拦截"""
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
            "app.plan_service.strategy_cash_after_transfer",
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
            "app.plan_service.strategy_cash_after_transfer",
            return_value=-5000,
        ), patch(
            "datasource.market.fetch_cb_prices_tencent",
            return_value={"113062": 126.80},
        ):
            r = self.client.post("/api/plan/cb/size-orders")

        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "INSUFFICIENT_RELEASABLE_CASH")
        self.assertEqual(db.get_orders("cb", "2026-06-29"), [])

    def test_order_cash_uses_available_cash_not_cash_balance(self):
        db.insert_account_value_snapshot("cb", "2026-06-29", 227183, 1110, 1000)
        account = db.get_current_account_summary()
        from app.plan_service import strategy_cash_after_transfer
        self.assertEqual(account["bond_cash"], 1110)
        self.assertEqual(account["bond_available_cash"], 110)
        self.assertEqual(strategy_cash_after_transfer("cb", account, {"bond": 0}), 110)

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
            "app.plan_service.strategy_cash_after_transfer",
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
            "app.plan_service.strategy_cash_after_transfer",
            return_value=10000,
        ), patch(
            "datasource.market.fetch_cb_prices_tencent",
            return_value={"113062": 126.80},
        ):
            r = self.client.post("/api/plan/cb/size-orders")

        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(order["action"] == "BUY" for order in r.json()["orders"]))

    def test_order_summary_uses_explicit_internal_transfer_plan(self):
        r = self.client.get("/api/plan")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(
            data["cb"]["summary"]["transfer_delta"],
            data["transfer_deltas"]["bond"],
        )
        cb_summary = data["cb"]["summary"]
        cb_available = data["account"]["bond_available_cash"]
        self.assertEqual(
            cb_summary["cash_left"],
            round(cb_available + cb_summary["transfer_delta"] + cb_summary["order_delta"], 2),
        )

    def test_overseas_stale_blocks_complete_funding_plan(self):
        db._TEST_CONN.execute(
            "DELETE FROM account_value_snapshots WHERE account_id='overseas'"
        )
        db._TEST_CONN.commit()

        r = self.client.get("/api/plan")

        self.assertEqual(r.status_code, 409)
        self.assertIn("account.overseas", {e["input"] for e in r.json()["detail"]["errors"]})

    def test_account_fact_date_cannot_be_hidden_by_recent_entry_time(self):
        db.insert_account_value_snapshot("stock", "2026-06-01", 209555, 274)
        db._TEST_CONN.execute(
            """UPDATE account_value_snapshots
               SET created_at='2026-06-30T08:30:00'
               WHERE id=(SELECT MAX(id) FROM account_value_snapshots WHERE account_id='stock')"""
        )
        db._TEST_CONN.commit()

        r = self.client.get("/api/plan")

        self.assertEqual(r.status_code, 409)
        stock_error = next(
            item for item in r.json()["detail"]["errors"]
            if item["input"] == "account.stock"
        )
        self.assertEqual(stock_error["date"], "2026-06-01")

    def test_position_fact_date_cannot_be_hidden_by_recent_entry_time(self):
        db._TEST_CONN.execute("DELETE FROM position_snapshot_items")
        db._TEST_CONN.execute("DELETE FROM position_snapshots")
        db._TEST_CONN.commit()
        db.append_position_snapshot("cb", "2026-06-01", [{
            "code": "113062", "name": "常银转债", "shares": 10,
        }])
        db._TEST_CONN.execute(
            "UPDATE position_snapshots SET created_at='2026-06-30T08:30:00'"
        )
        db._TEST_CONN.commit()

        from app.plan_service import position_snapshot_for_plan

        self.assertIsNone(
            position_snapshot_for_plan("cb", "2026-06-29", "2026-06-30")
        )

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
        db.insert_account_value_snapshot("overseas", "2026-07-13", 93030)
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
