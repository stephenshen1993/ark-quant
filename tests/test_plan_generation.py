import sqlite3
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch

import pandas as pd

from app import account_current_state, plan_generation, plan_lifecycle
from app.plan_service import PlanServiceError
from datasource import db
from datasource.youzhiyouxing import DATA_URL


class TestPlanGeneration(unittest.TestCase):
    def setUp(self):
        self.stock_quotes = patch(
            "datasource.market.fetch_tencent_snapshot",
            return_value=pd.DataFrame([{
                "stock_code": "600051",
                "stock_name_q": "宁波联合",
                "price": (209555 - 274) / 1800,
            }]),
        )
        self.cb_quotes = patch(
            "datasource.market.fetch_cb_quotes_tencent",
            return_value={
                "113062": {"name": "常银转债", "price": (227183 - 110) / 10},
            },
        )
        self.stock_quotes_mock = self.stock_quotes.start()
        self.cb_quotes.start()
        self.addCleanup(self.stock_quotes.stop)
        self.addCleanup(self.cb_quotes.stop)
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
        db.insert_positions("cb", "2026-06-29", [{
            "code": "113062", "name": "常银转债", "shares": 10,
        }])
        db.insert_positions("stock", "2026-06-29", [{
            "code": "600051", "name": "宁波联合", "shares": 1800,
        }])
        cb_run_id = db.create_complete_strategy_run(
            "cb",
            date(2026, 6, 29),
            date(2026, 6, 30),
            pd.DataFrame([{
                "bond_code": "113062",
                "bond_name": "常银转债",
                "cb_price": 126.80,
                "premium_rate": 10.0,
                "double_low": 136.8,
                "score": 0.9,
            }]),
        )
        db.insert_cb_orders(cb_run_id, pd.DataFrame([{
            "action": "HOLD",
            "bond_code": "113062",
            "bond_name": "常银转债",
            "price": 126.80,
            "delta_shares": 0,
            "amount": 0,
        }]))
        stock_run_id = db.create_complete_strategy_run(
            "stock",
            date(2026, 6, 29),
            date(2026, 6, 30),
            pd.DataFrame([{
                "rank": 1,
                "stock_code": "600051",
                "stock_name": "宁波联合",
                "total_mv_yuan": 1000000000,
                "pe_ttm": 10.0,
                "roe_pct": 12.0,
            }]),
        )
        db.insert_stock_orders(stock_run_id, pd.DataFrame([{
            "action": "HOLD",
            "stock_code": "600051",
            "stock_name": "宁波联合",
            "price": 5.68,
            "delta_shares": 0,
            "amount": 0,
        }]))

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_preview_current_plan_is_plan_generation_boundary(self):
        data = plan_generation.preview_current_plan()

        self.assertEqual(data["plan_date"], "2026-06-29")
        self.assertIn("market_temperature", data)
        self.assertIn("account_read_model", data)
        self.assertIn("fund_transfer", data)
        self.assertEqual(data["cb"]["data_date"], "2026-06-29")
        self.assertEqual(data["stock"]["data_date"], "2026-06-29")

    def test_complete_plan_generation_uses_the_refreshed_current_valuation(self):
        self.stock_quotes_mock.return_value = pd.DataFrame([{
            "stock_code": "600051",
            "stock_name_q": "宁波联合",
            "price": 200.0,
        }])

        _, account, _, _ = plan_generation.prepare_complete_plan_generation()

        self.assertEqual(account["stock_total"], 360274.0)
        stock = {item["id"]: item for item in account["accounts"]}["stock"]
        self.assertEqual(stock["total"], 360274.0)

    def test_reads_generated_plan_by_lifecycle_identity(self):
        plan_id = "plan-2026-06-29-deadbeef"
        plan = {
            "plan_date": "2026-06-29",
            "generation": {
                "plan_id": plan_id,
                "plan_date": "2026-06-29",
                "status": plan_lifecycle.RUNNING,
                "stages": [],
            },
        }

        plan_lifecycle.start(plan_id, "2026-06-29", plan)

        saved = plan_generation.get_generated_plan(plan_id)
        self.assertEqual(saved["plan"]["generation"]["plan_id"], plan_id)

    def test_reads_legacy_generated_plan_with_current_execution_guardrails(self):
        plan = {
            "plan_date": "2026-06-29",
            "account_read_model": {
                "accounts": [{
                    "id": "cb",
                    "name": "华泰账户",
                    "portfolio_id": "A",
                    "strategy_names": ["多因子可转债策略"],
                    "available_cash": 1000.0,
                }],
                "portfolios": [{"id": "A", "name": "主动组合", "account_ids": ["cb"]}],
                "legacy_adapter": {"strategy_to_account_id": {"cb": "cb"}},
            },
            "fund_transfer": {
                "top_level": {"executed_actions": [], "outflows": []},
                "a_internal": {"actions": []},
            },
            "cb": {
                "data_date": "2026-06-29",
                "trade_date": "2026-06-30",
                "summary": {"starting_cash": 1000.0},
                "orders": [{
                    "action": "BUY",
                    "bond_code": "113062",
                    "bond_name": "常银转债",
                    "delta_shares": 10,
                    "price": 126.8,
                    "amount": 1268.0,
                }],
            },
            "stock": {"data_date": "2026-06-29", "trade_date": "2026-06-30", "orders": []},
            "execution_read_model": {"account_trading_plans": []},
        }
        with patch(
            "app.plan_generation.plan_lifecycle.get_plan",
            return_value={"plan": plan},
        ):
            saved = plan_generation.get_generated_plan("legacy-plan")

        account_plan = saved["plan"]["execution_read_model"]["account_trading_plans"][0]
        self.assertEqual(account_plan["execution_guardrails"]["price_basis_date"], "2026-06-29")
        self.assertEqual(
            account_plan["execution_guardrails"]["rules"][0]["kind"],
            "buy_price_ceiling",
        )

    def test_generate_complete_plan_prepares_missing_rankings_through_strategy_runner(self):
        self._clear_strategy_outputs()

        def generate_rankings(strategy: str):
            if strategy == "cb":
                run_id = db.create_complete_strategy_run(
                    "cb",
                    date(2026, 6, 29),
                    date(2026, 6, 30),
                    pd.DataFrame([{
                        "bond_code": "113062",
                        "bond_name": "常银转债",
                        "cb_price": 126.80,
                        "premium_rate": 10.0,
                        "double_low": 136.8,
                        "score": 0.9,
                    }]),
                )
            else:
                run_id = db.create_complete_strategy_run(
                    "stock",
                    date(2026, 6, 29),
                    date(2026, 6, 30),
                    pd.DataFrame([{
                        "rank": 1,
                        "stock_code": "600051",
                        "stock_name": "宁波联合",
                        "total_mv_yuan": 1000000000,
                        "pe_ttm": 10.0,
                        "roe_pct": 12.0,
                    }]),
                )
            return SimpleNamespace(run_id=run_id)

        cb_size = Mock(return_value={
            "orders": [{"action": "HOLD", "bond_code": "113062", "delta_shares": 0}],
            "summary": {"starting_cash": 110, "transfer_delta": 0, "order_delta": 0, "cash_left": 110},
        })
        stock_size = Mock(return_value={
            "orders": [{"action": "HOLD", "stock_code": "600051", "delta_shares": 0}],
            "summary": {"starting_cash": 274, "transfer_delta": 0, "order_delta": 0, "cash_left": 274},
        })

        with patch("app.strategy_runner._run_strategy_impl", side_effect=generate_rankings) as run_impl:
            plan = plan_generation.generate_complete_plan(
                size_cb_orders=cb_size,
                size_stock_orders=stock_size,
            )

        self.assertEqual([call.args[0] for call in run_impl.call_args_list], ["cb", "stock"])
        self.assertEqual(plan["generation"]["status"], "complete")
        self.assertEqual(plan["plan_date"], "2026-06-29")
        cb_size.assert_called_once()
        stock_size.assert_called_once()

    def test_complete_plan_persists_a_frozen_versioned_snapshot(self):
        cb_size = Mock(return_value={
            "orders": [],
            "summary": {
                "starting_cash": 110.0,
                "transfer_delta": 0.0,
                "order_delta": 0.0,
                "cash_left": 110.0,
            },
        })
        stock_size = Mock(return_value={
            "orders": [],
            "summary": {
                "starting_cash": 274.0,
                "transfer_delta": 0.0,
                "order_delta": 0.0,
                "cash_left": 274.0,
            },
        })

        plan = plan_generation.generate_complete_plan(
            size_cb_orders=cb_size,
            size_stock_orders=stock_size,
        )

        snapshot = plan["snapshot"]
        plan_id = plan["generation"]["plan_id"]
        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(snapshot["plan_id"], plan_id)
        self.assertEqual(snapshot["data_date"], "2026-06-29")
        self.assertEqual(snapshot["execution_date"], "2026-06-30")
        self.assertEqual(snapshot["scope"], "complete_next_trading_day")
        self.assertEqual(snapshot["generated_at"], plan["generated_at"])
        self.assertEqual(
            snapshot["input_provenance"]["strategies"]["stock"],
            {"data_date": "2026-06-29", "trade_date": "2026-06-30"},
        )

        before_stale = plan_lifecycle.get_plan(plan_id)["plan"]
        plan_lifecycle.mark_stale()
        after_stale = plan_lifecycle.get_plan(plan_id)
        self.assertEqual(after_stale["status"], plan_lifecycle.STALE)
        self.assertEqual(after_stale["plan"], before_stale)

        regenerated = plan_generation.generate_complete_plan(
            size_cb_orders=cb_size,
            size_stock_orders=stock_size,
        )
        self.assertNotEqual(regenerated["generation"]["plan_id"], plan_id)
        self.assertEqual(
            plan_lifecycle.get_plan(plan_id)["plan"],
            before_stale,
        )

    def test_account_change_during_generation_returns_stale_instead_of_old_complete_plan(self):
        def cb_size(*_args, **_kwargs):
            plan_lifecycle.mark_stale()
            return {
                "orders": [],
                "summary": {
                    "starting_cash": 110,
                    "transfer_delta": 0,
                    "order_delta": 0,
                    "cash_left": 110,
                },
            }

        stock_size = Mock(return_value={
            "orders": [],
            "summary": {
                "starting_cash": 274,
                "transfer_delta": 0,
                "order_delta": 0,
                "cash_left": 274,
            },
        })

        with self.assertRaises(PlanServiceError) as caught:
            plan_generation.generate_complete_plan(
                size_cb_orders=cb_size,
                size_stock_orders=stock_size,
            )

        detail = caught.exception.detail
        saved = plan_lifecycle.get_plan(detail["plan_id"])
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(detail["code"], "PLAN_INPUTS_CHANGED")
        self.assertEqual(saved["status"], plan_lifecycle.STALE)
        self.assertEqual(saved["error"]["code"], "PLAN_INPUTS_CHANGED")

    def test_market_change_during_generation_stales_the_captured_plan(self):
        def cb_size(*_args, **_kwargs):
            running = plan_lifecycle.get_running_plan_status("2026-06-29")
            captured = plan_lifecycle.get_plan(running["plan_id"])
            self.assertIn("account", captured["plan"])
            self.stock_quotes_mock.return_value = pd.DataFrame([{
                "stock_code": "600051",
                "stock_name_q": "宁波联合",
                "price": 200.0,
            }])
            account_current_state.build_current_account_summary()
            return {
                "orders": [],
                "summary": {
                    "starting_cash": 110,
                    "transfer_delta": 0,
                    "order_delta": 0,
                    "cash_left": 110,
                },
            }

        stock_size = Mock(return_value={
            "orders": [],
            "summary": {
                "starting_cash": 274,
                "transfer_delta": 0,
                "order_delta": 0,
                "cash_left": 274,
            },
        })

        with self.assertRaises(PlanServiceError) as caught:
            plan_generation.generate_complete_plan(
                size_cb_orders=cb_size,
                size_stock_orders=stock_size,
            )

        detail = caught.exception.detail
        saved = plan_lifecycle.get_plan(detail["plan_id"])
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(detail["code"], "PLAN_INPUTS_CHANGED")
        self.assertEqual(saved["status"], plan_lifecycle.STALE)

    def test_strategy_ranking_date_mismatch_has_plan_generation_stage(self):
        self._clear_strategy_outputs()

        def generate_wrong_date(strategy: str):
            run_id = db.create_complete_strategy_run(
                strategy,
                date(2026, 6, 28),
                date(2026, 6, 29),
                pd.DataFrame([{
                    "bond_code": "113062",
                    "bond_name": "常银转债",
                    "cb_price": 126.80,
                    "premium_rate": 10.0,
                    "double_low": 136.8,
                    "score": 0.9,
                }]),
            )
            return SimpleNamespace(run_id=run_id)

        with patch("app.strategy_runner._run_strategy_impl", side_effect=generate_wrong_date):
            with self.assertRaises(PlanServiceError) as caught:
                plan_generation.prepare_complete_plan_generation()

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["stage"], "strategy_rankings")
        self.assertEqual(caught.exception.detail["code"], "PLAN_INPUT_DATE_MISMATCH")
        self.assertEqual(caught.exception.detail["errors"][0]["input"], "cb")

    def test_size_strategy_orders_uses_plan_generation_context(self):
        cb_size = Mock(return_value={"orders": [], "summary": {"cash_left": 0}})
        stock_size = Mock(return_value={"orders": [], "summary": {"cash_left": 0}})
        account = {"bond_available_cash": 110, "stock_available_cash": 274}
        deltas = {"bond": 1000, "stock": -500}

        with patch(
            "app.plan_generation.prepare_strategy_order_context",
            return_value=("2026-06-29", account, deltas),
        ):
            result = plan_generation.size_strategy_orders(
                "cb",
                size_cb_orders=cb_size,
                size_stock_orders=stock_size,
            )

        self.assertEqual(result["summary"]["cash_left"], 0)
        cb_size.assert_called_once_with(1110, plan_date="2026-06-29")
        stock_size.assert_not_called()

    def test_size_strategy_orders_rejects_unknown_strategy_at_use_case_boundary(self):
        with self.assertRaises(PlanServiceError) as caught:
            plan_generation.size_strategy_orders(
                "unknown",
                size_cb_orders=Mock(),
                size_stock_orders=Mock(),
            )

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail["code"], "INVALID_STRATEGY")

    def _clear_strategy_outputs(self):
        db._TEST_CONN.execute("DELETE FROM cb_orders")
        db._TEST_CONN.execute("DELETE FROM stock_orders")
        db._TEST_CONN.execute("DELETE FROM cb_rankings")
        db._TEST_CONN.execute("DELETE FROM stock_rankings")
        db._TEST_CONN.execute("DELETE FROM strategy_runs")
        db._TEST_CONN.commit()


if __name__ == "__main__":
    unittest.main()
