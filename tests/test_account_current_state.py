import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from app import account_current_state
from datasource import db


class TestAccountCurrentState(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()
        from app.main import app

        self.client = TestClient(app)

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_account_list_starts_with_independent_missing_states(self):
        response = self.client.get("/api/accounts")

        self.assertEqual(response.status_code, 200)
        accounts = response.json()["accounts"]
        self.assertEqual([item["account_id"] for item in accounts], [
            "stock", "cb", "cash", "changqian", "overseas",
        ])
        labels = {item["account_id"]: item["label"] for item in accounts}
        self.assertEqual(labels["changqian"], "国内长钱")
        self.assertEqual(labels["overseas"], "海外长钱")
        self.assertTrue(all(item["record_state"] == "missing" for item in accounts))
        self.assertTrue(all(item["version"] is None for item in accounts))

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_security_update_reuses_raw_state_and_derives_valuation(self, fetch):
        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001", "stock_name_q": "平安银行", "price": 10.0,
        }])

        response = self.client.put("/api/accounts/stock", json={
            "expected_version": None,
            "available_cash": 100,
            "frozen_cash": 20,
            "positions": [{"code": "1", "quantity": 10}],
        })

        self.assertEqual(response.status_code, 200)
        state = response.json()
        self.assertEqual(state["raw_data"]["positions"], [
            {"code": "000001", "quantity": 10.0},
        ])
        self.assertEqual(state["valuation"]["total"], 220.0)
        self.assertEqual(state["valuation"]["items"][0]["name"], "平安银行")
        self.assertEqual(self.client.get("/api/accounts/stock").json(), state)

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_quote_failure_saves_raw_facts_without_turning_unknown_into_zero(self, fetch):
        fetch.return_value = pd.DataFrame()

        response = self.client.put("/api/accounts/stock", json={
            "expected_version": None,
            "available_cash": 100,
            "frozen_cash": 0,
            "positions": [{"code": "1", "quantity": 10}],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["valuation"]["status"], "unavailable")
        self.assertIsNone(response.json()["valuation"]["total"])
        summary = self.client.get("/api/account/summary").json()
        stock = {item["id"]: item for item in summary["accounts"]}["stock"]
        self.assertIsNone(stock["total"])
        self.assertIsNone(summary["total_assets"])

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_explicit_securities_state_refreshes_a_new_market_price(self, fetch):
        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001", "stock_name_q": "平安银行", "price": 10.0,
        }])
        saved = self.client.put("/api/accounts/stock", json={
            "expected_version": None,
            "available_cash": 100,
            "frozen_cash": 0,
            "positions": [{"code": "1", "quantity": 10}],
        }).json()

        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001", "stock_name_q": "平安银行", "price": 20.0,
        }])
        refreshed = self.client.get("/api/accounts/stock").json()

        self.assertEqual(refreshed["version"], saved["version"])
        self.assertEqual(refreshed["valuation"]["total"], 300.0)
        self.assertEqual(refreshed["valuation"]["items"][0]["price"], 20.0)

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_explicit_securities_state_fails_closed_when_new_quotes_fail(self, fetch):
        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001", "stock_name_q": "平安银行", "price": 10.0,
        }])
        self.client.put("/api/accounts/stock", json={
            "expected_version": None,
            "available_cash": 100,
            "frozen_cash": 0,
            "positions": [{"code": "1", "quantity": 10}],
        })

        fetch.return_value = pd.DataFrame()
        refreshed = self.client.get("/api/accounts/stock").json()

        self.assertEqual(refreshed["valuation"]["status"], "unavailable")
        self.assertIsNone(refreshed["valuation"]["total"])
        self.assertEqual(refreshed["readiness"], "waiting_for_valuation")

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_current_valuation_change_stales_a_plan_with_old_inputs(self, fetch):
        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001", "stock_name_q": "平安银行", "price": 10.0,
        }])
        self.client.put("/api/accounts/stock", json={
            "expected_version": None,
            "available_cash": 100,
            "frozen_cash": 0,
            "positions": [{"code": "1", "quantity": 10}],
        })
        db.insert_generated_plan("plan-1", "2026-08-08", "complete", {
            "account": {"stock_total": 200.0, "bond_total": None},
        })

        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001", "stock_name_q": "平安银行", "price": 20.0,
        }])
        account_current_state.build_current_account_summary()

        self.assertEqual(db.get_generated_plan("plan-1")["status"], "stale")

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_running_plan_without_captured_inputs_is_not_staled_by_initial_refresh(self, fetch):
        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001", "stock_name_q": "平安银行", "price": 10.0,
        }])
        self.client.put("/api/accounts/stock", json={
            "expected_version": None,
            "available_cash": 100,
            "frozen_cash": 0,
            "positions": [{"code": "1", "quantity": 10}],
        })
        db.insert_generated_plan("plan-1", "2026-08-08", "running", {
            "generation": {"plan_id": "plan-1", "status": "running"},
        })

        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001", "stock_name_q": "平安银行", "price": 20.0,
        }])
        account_current_state.build_current_account_summary()

        self.assertEqual(db.get_generated_plan("plan-1")["status"], "running")

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_confirm_retries_an_unavailable_derived_valuation(self, fetch):
        fetch.return_value = pd.DataFrame()
        saved = self.client.put("/api/accounts/stock", json={
            "expected_version": None,
            "available_cash": 100,
            "frozen_cash": 0,
            "positions": [{"code": "1", "quantity": 10}],
        }).json()
        self.assertEqual(saved["valuation"]["status"], "unavailable")

        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001", "stock_name_q": "平安银行", "price": 10.0,
        }])
        confirmed = self.client.post("/api/accounts/stock/confirm", json={
            "expected_version": saved["version"],
        })

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["valuation"]["status"], "available")
        self.assertEqual(confirmed.json()["valuation"]["total"], 200.0)
        self.assertEqual(confirmed.json()["operation"], "confirm")

    def test_securities_update_requires_an_explicit_position_list(self):
        response = self.client.put("/api/accounts/stock", json={
            "expected_version": None,
            "available_cash": 100,
            "frozen_cash": 0,
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn("空仓请明确提交空列表", response.json()["detail"])

    def test_missing_zero_balance_and_confirmed_empty_holdings_stay_distinct(self):
        zero_cash = self.client.put("/api/accounts/cash", json={
            "expected_version": None,
            "amount": 0,
        }).json()
        empty_stock = self.client.put("/api/accounts/stock", json={
            "expected_version": None,
            "available_cash": 0,
            "frozen_cash": 0,
            "positions": [],
        }).json()
        missing_cb = self.client.get("/api/accounts/cb").json()

        self.assertEqual(zero_cash["record_state"], "recorded")
        self.assertEqual(zero_cash["raw_data"]["amount"], 0.0)
        self.assertEqual(empty_stock["holding_state"], "confirmed_empty")
        self.assertEqual(empty_stock["record_state"], "recorded")
        self.assertEqual(missing_cb["holding_state"], "missing")
        self.assertEqual(missing_cb["record_state"], "missing")

    def test_confirming_unchanged_data_does_not_invalidate_plan(self):
        saved = self.client.put("/api/accounts/cash", json={
            "expected_version": None,
            "amount": 100,
        }).json()
        db.insert_generated_plan("plan-1", "2026-08-08", "complete", {"ok": True})

        response = self.client.post("/api/accounts/cash/confirm", json={
            "expected_version": saved["version"],
        })

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()["version"], saved["version"])
        self.assertEqual(response.json()["operation"], "confirm")
        self.assertEqual(db.get_generated_plan("plan-1")["status"], "complete")

    def test_stale_editor_cannot_overwrite_a_newer_account_version(self):
        first = self.client.put("/api/accounts/cash", json={
            "expected_version": None, "amount": 100,
        }).json()
        second = self.client.put("/api/accounts/cash", json={
            "expected_version": first["version"], "amount": 200,
        })

        conflict = self.client.put("/api/accounts/cash", json={
            "expected_version": first["version"], "amount": 300,
        })

        self.assertEqual(second.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["latest"]["raw_data"]["amount"], 200.0)
        self.assertEqual(
            self.client.get("/api/accounts/cash").json()["raw_data"]["amount"],
            200.0,
        )

    def test_backfilled_older_data_does_not_replace_current_account_data(self):
        current = self.client.put("/api/accounts/cash", json={
            "expected_version": None, "amount": 100,
        }).json()

        account_current_state.backfill_account("cash", as_of="2020-01-01", amount=50)

        after = self.client.get("/api/accounts/cash").json()
        self.assertEqual(after["version"], current["version"])
        self.assertEqual(after["raw_data"]["amount"], 100.0)

    def test_backfill_rejects_a_date_that_is_not_strictly_older_than_current(self):
        current = self.client.put("/api/accounts/cash", json={
            "expected_version": None, "amount": 100,
        }).json()

        with self.assertRaisesRegex(
            account_current_state.CurrentAccountError,
            "必须早于",
        ):
            account_current_state.backfill_account(
                "cash", as_of=current["as_of"], amount=50,
            )

    def test_backfill_rejects_a_future_date_without_existing_current_data(self):
        with self.assertRaisesRegex(
            account_current_state.CurrentAccountError,
            "不能晚于今天",
        ):
            account_current_state.backfill_account(
                "cash", as_of="2099-01-01", amount=50,
            )

    def test_backfill_on_a_missing_account_stays_history_only(self):
        result = account_current_state.backfill_account(
            "cash", as_of="2026-08-08", amount=50,
        )

        self.assertEqual(result["record_state"], "missing")
        self.assertEqual(self.client.get("/api/accounts/cash").json()["record_state"], "missing")
        row = db._TEST_CONN.execute(
            """SELECT operation,account_snapshot_id,position_snapshot_id
               FROM account_state_versions WHERE account_id='cash'"""
        ).fetchone()
        self.assertEqual(row["operation"], "backfill")
        self.assertIsNone(row["account_snapshot_id"])
        self.assertIsNone(row["position_snapshot_id"])

    def test_legacy_securities_state_requires_same_date_cash_and_holdings(self):
        db.insert_account_value_snapshot("stock", "2026-08-08", 100, 100)

        state = self.client.get("/api/accounts/stock").json()

        self.assertEqual(state["record_state"], "missing")
        self.assertEqual(state["holding_state"], "missing")
        self.assertEqual(state["readiness"], "needs_update")
        self.assertEqual(state["raw_data"]["available_cash"], 100.0)

    def test_legacy_securities_state_never_splices_different_fact_dates(self):
        db.insert_account_value_snapshot("stock", "2026-08-08", 100, 100)
        db.append_position_snapshot("stock", "2026-08-09", [
            {"code": "000001", "name": "平安银行", "shares": 10},
        ])

        state = self.client.get("/api/accounts/stock").json()

        self.assertEqual(state["as_of"], "2026-08-09")
        self.assertEqual(state["record_state"], "missing")
        self.assertEqual(state["holding_state"], "recorded")
        self.assertIsNone(state["raw_data"]["available_cash"])
        self.assertEqual(state["raw_data"]["positions"][0]["code"], "000001")

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_legacy_securities_read_derives_prices_without_reentering_holdings(self, fetch):
        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001",
            "stock_name_q": "平安银行",
            "price": 10.0,
        }])
        db.insert_account_value_snapshot("stock", "2026-08-08", 200, 100)
        db.append_position_snapshot("stock", "2026-08-08", [{
            "code": "000001",
            "name": "平安银行",
            "shares": 10,
        }])

        state = self.client.get("/api/accounts/stock").json()

        self.assertEqual(state["operation"], "migrated")
        self.assertEqual(state["valuation"]["status"], "available")
        self.assertEqual(state["valuation"]["total"], 200.0)
        self.assertEqual(state["valuation"]["items"][0]["price"], 10.0)
        self.assertEqual(state["valuation"]["items"][0]["market_value"], 100.0)
        self.assertEqual(state["raw_data"]["positions"], [
            {"code": "000001", "quantity": 10.0},
        ])

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_legacy_securities_read_reports_unavailable_when_quotes_fail(self, fetch):
        fetch.return_value = pd.DataFrame()
        db.insert_account_value_snapshot("stock", "2026-08-08", 200, 100)
        db.append_position_snapshot("stock", "2026-08-08", [{
            "code": "000001",
            "name": "平安银行",
            "shares": 10,
        }])

        state = self.client.get("/api/accounts/stock").json()

        self.assertEqual(state["valuation"]["status"], "unavailable")
        self.assertIsNone(state["valuation"]["total"])
        self.assertEqual(state["valuation"]["missing_codes"], ["000001"])
        self.assertEqual(state["valuation"]["items"][0]["name"], "平安银行")
        self.assertEqual(state["readiness"], "waiting_for_valuation")

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_plan_summary_uses_the_same_refreshed_legacy_valuation(self, fetch):
        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001",
            "stock_name_q": "平安银行",
            "price": 20.0,
        }])
        db.insert_account_value_snapshot("stock", "2026-08-08", 200, 100)
        db.append_position_snapshot("stock", "2026-08-08", [{
            "code": "000001",
            "name": "平安银行",
            "shares": 10,
        }])

        summary = account_current_state.build_current_account_summary()

        stock = {item["id"]: item for item in summary["accounts"]}["stock"]
        self.assertEqual(summary["stock_total"], 300.0)
        self.assertEqual(stock["total"], 300.0)

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_plan_summary_fails_closed_when_legacy_quotes_are_unavailable(self, fetch):
        fetch.return_value = pd.DataFrame()
        db.insert_account_value_snapshot("stock", "2026-08-08", 200, 100)
        db.append_position_snapshot("stock", "2026-08-08", [{
            "code": "000001",
            "name": "平安银行",
            "shares": 10,
        }])

        summary = account_current_state.build_current_account_summary()

        stock = {item["id"]: item for item in summary["accounts"]}["stock"]
        self.assertIsNone(summary["stock_total"])
        self.assertIsNone(stock["total"])
        self.assertIsNone(summary["total_assets"])

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_confirming_legacy_securities_data_persists_derived_prices(self, fetch):
        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001",
            "stock_name_q": "平安银行",
            "price": 10.0,
        }])
        db.insert_account_value_snapshot("stock", "2026-08-08", 200, 100)
        db.append_position_snapshot("stock", "2026-08-08", [{
            "code": "000001",
            "name": "平安银行",
            "shares": 10,
        }])
        legacy = self.client.get("/api/accounts/stock").json()

        confirmed = self.client.post("/api/accounts/stock/confirm", json={
            "expected_version": legacy["version"],
        })

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["operation"], "confirm")
        self.assertEqual(confirmed.json()["valuation"]["items"][0]["price"], 10.0)

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_confirming_a_changed_legacy_valuation_invalidates_the_plan(self, fetch):
        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001",
            "stock_name_q": "平安银行",
            "price": 20.0,
        }])
        db.insert_account_value_snapshot("stock", "2026-08-08", 200, 100)
        db.append_position_snapshot("stock", "2026-08-08", [{
            "code": "000001",
            "name": "平安银行",
            "shares": 10,
        }])
        legacy = self.client.get("/api/accounts/stock").json()
        db.insert_generated_plan("plan-1", "2026-08-08", "complete", {"ok": True})

        confirmed = self.client.post("/api/accounts/stock/confirm", json={
            "expected_version": legacy["version"],
        })

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["valuation"]["total"], 300.0)
        self.assertEqual(db.get_generated_plan("plan-1")["status"], "stale")

    @patch("datasource.market.fetch_tencent_snapshot")
    def test_confirming_the_same_legacy_valuation_preserves_the_plan(self, fetch):
        fetch.return_value = pd.DataFrame([{
            "stock_code": "000001",
            "stock_name_q": "平安银行",
            "price": 10.0,
        }])
        db.insert_account_value_snapshot("stock", "2026-08-08", 200, 100)
        db.append_position_snapshot("stock", "2026-08-08", [{
            "code": "000001",
            "name": "平安银行",
            "shares": 10,
        }])
        legacy = self.client.get("/api/accounts/stock").json()
        db.insert_generated_plan("plan-1", "2026-08-08", "complete", {"ok": True})

        confirmed = self.client.post("/api/accounts/stock/confirm", json={
            "expected_version": legacy["version"],
        })

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(db.get_generated_plan("plan-1")["status"], "complete")

    def test_legacy_write_endpoint_is_blocked_after_current_state_activation(self):
        self.client.put("/api/accounts/cash", json={
            "expected_version": None, "amount": 100,
        })

        legacy = self.client.post("/api/account/cash/snapshot", json={
            "snapshot_date": "2026-08-08", "total": 200,
        })

        self.assertEqual(legacy.status_code, 409)
        self.assertEqual(
            self.client.get("/api/accounts/cash").json()["raw_data"]["amount"],
            100.0,
        )

    def test_legacy_backfill_uses_business_date_not_insert_order_for_current(self):
        db.insert_account_value_snapshot("cash", "2026-08-08", 100)
        db.insert_account_value_snapshot("cash", "2020-01-01", 50)

        summary = db.get_current_account_summary()

        cash = {item["id"]: item for item in summary["accounts"]}["cash"]
        self.assertEqual(cash["total"], 100)
        self.assertEqual(cash["snapshot_date"], "2026-08-08")


class TestLegacyCurrentStateConcurrency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path_patch = patch.object(
            db,
            "DB_PATH",
            Path(self.temp_dir.name) / "concurrency.db",
        )
        self.db_path_patch.start()
        db._TEST_CONN = None
        db.init_db()
        from app.main import app

        self.app = app

    def tearDown(self):
        self.db_path_patch.stop()
        self.temp_dir.cleanup()

    def test_legacy_guard_and_write_are_atomic_during_first_activation(self):
        entered_legacy_insert = threading.Event()
        allow_legacy_insert = threading.Event()
        results = {}
        failures = []
        original_insert = db.account_store.insert_account_value_snapshot

        def blocking_legacy_insert(conn, *args, **kwargs):
            entered_legacy_insert.set()
            if not allow_legacy_insert.wait(timeout=5):
                raise RuntimeError("timed out waiting to complete legacy insert")
            return original_insert(conn, *args, **kwargs)

        def legacy_write():
            try:
                with TestClient(self.app) as client:
                    results["legacy"] = client.post("/api/account/cash/snapshot", json={
                        "snapshot_date": "2026-08-08",
                        "total": 200,
                    })
            except Exception as exc:  # pragma: no cover - surfaced below
                failures.append(exc)

        def current_write():
            try:
                with TestClient(self.app) as client:
                    results["current"] = client.put("/api/accounts/cash", json={
                        "expected_version": None,
                        "amount": 100,
                    })
            except Exception as exc:  # pragma: no cover - surfaced below
                failures.append(exc)

        with patch.object(
            db.account_store,
            "insert_account_value_snapshot",
            side_effect=blocking_legacy_insert,
        ):
            legacy_thread = threading.Thread(target=legacy_write)
            legacy_thread.start()
            self.assertTrue(entered_legacy_insert.wait(timeout=5))
            current_thread = threading.Thread(target=current_write)
            current_thread.start()
            allow_legacy_insert.set()
            legacy_thread.join(timeout=5)
            current_thread.join(timeout=5)

        self.assertFalse(legacy_thread.is_alive())
        self.assertFalse(current_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(results["legacy"].status_code, 200)
        self.assertEqual(results["current"].status_code, 409)
        with TestClient(self.app) as client:
            self.assertEqual(
                client.get("/api/accounts/cash").json()["raw_data"]["amount"],
                200.0,
            )
            summary = client.get("/api/account/summary").json()
        cash = {item["id"]: item for item in summary["accounts"]}["cash"]
        self.assertEqual(cash["total"], 200.0)


if __name__ == "__main__":
    unittest.main()
