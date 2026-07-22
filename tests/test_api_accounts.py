import unittest
import sqlite3
from datetime import date

import pandas as pd
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

    def test_investment_model_separates_accounts_and_strategies(self):
        r = self.client.get("/api/account/model")
        self.assertEqual(r.status_code, 200)
        model = r.json()
        self.assertEqual(model["concepts"], ["资产类别", "策略", "账户"])
        accounts = {item["id"]: item for item in model["accounts"]}
        self.assertEqual(accounts["stock"]["label"], "广发账户")
        self.assertEqual(accounts["stock"]["strategy_ids"], ["smallcap_stock"])
        self.assertFalse(accounts["overseas"]["participates_in_domestic_rebalance"])
        strategies = {item["id"]: item for item in model["strategies"]}
        self.assertEqual(strategies["fund_transfer"]["scope"], "跨账户")

    def test_post_context_updates_only_context(self):
        r = self.client.post("/api/account/context", json={
            "snapshot_date": "2026-06-29",
            "temperature": 45.0,
        })
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("snapshot_date", r.json())
        self.assertNotIn("temperature", r.json())
        self.assertEqual(r.json()["context"]["snapshot_date"], "2026-06-29")
        self.assertEqual(r.json()["context"]["temperature"], 45.0)
        self.assertNotIn("account_updated_at", r.json())
        self.assertEqual(r.json()["total_assets"], 0)
        self.assertEqual(len(r.json()["accounts"]), 5)

    def test_context_accepts_auditable_funding_check_facts(self):
        r = self.client.post("/api/account/context", json={
            "snapshot_date": "2026-07-21",
            "temperature": 50.0,
            "check_type": "quarterly",
            "new_contribution": 5_000,
            "b_purchase_limit": 2_000,
            "b_purchase_checked_at": "2026-07-21T15:30:00",
            "b_purchase_source": "manual",
        })

        self.assertEqual(r.status_code, 200)
        context = r.json()["context"]
        self.assertEqual(context["check_type"], "quarterly")
        self.assertEqual(context["b_purchase_limit"], 2_000)
        self.assertEqual(context["b_purchase_source"], "manual")

    def test_b_purchase_limit_requires_auditable_check_facts(self):
        r = self.client.post("/api/account/context", json={
            "snapshot_date": "2026-07-21",
            "temperature": 50.0,
            "b_purchase_limit": 1_000,
        })

        self.assertEqual(r.status_code, 400)
        self.assertIn("checked_at", r.json()["detail"])

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
        self.assertEqual(r.json()["context"]["temperature"], 45.0)
        self.assertEqual(r.json()["total_assets"], 436738)
        self.assertNotIn("stock_total", r.json())
        self.assertNotIn("stock_cash", r.json())
        self.assertNotIn("bond_total", r.json())
        self.assertNotIn("bond_cash", r.json())
        accounts = {item["id"]: item for item in r.json()["accounts"]}
        self.assertEqual(accounts["stock"]["label"], "广发账户")
        self.assertEqual(accounts["stock"]["total"], 209555)
        self.assertEqual(accounts["stock"]["cash"], 274)
        self.assertEqual(accounts["stock"]["snapshot_date"], "2026-06-29")
        self.assertIsNotNone(accounts["stock"]["updated_at"])
        self.assertEqual(accounts["cb"]["label"], "华泰账户")
        self.assertEqual(accounts["cb"]["total"], 227183)
        self.assertEqual(accounts["cb"]["cash"], 110)

    def test_account_updates_are_per_account(self):
        r = self.client.post("/api/account/cash/snapshot", json={
            "snapshot_date": "2026-06-29",
            "total": 59013,
        })
        self.assertEqual(r.status_code, 200)
        accounts = {item["id"]: item for item in r.json()["accounts"]}
        self.assertEqual(accounts["cash"]["total"], 59013)
        self.assertIsNotNone(accounts["cash"]["updated_at"])
        self.assertNotIn("cash_pool", r.json())

        r2 = self.client.post("/api/account/changqian/snapshot", json={
            "snapshot_date": "2026-06-29",
            "total": 111000,
        })
        self.assertEqual(r2.status_code, 200)
        accounts = {item["id"]: item for item in r2.json()["accounts"]}
        self.assertEqual(accounts["changqian"]["total"], 111000)
        self.assertIsNotNone(accounts["changqian"]["updated_at"])
        self.assertNotIn("changqian_total", r2.json())

    def test_frozen_cash_is_subtracted_from_available_cash(self):
        r = self.client.post("/api/account/cb/snapshot", json={
            "snapshot_date": "2026-07-17", "total": 12000,
            "cash": 1000, "frozen_cash": 1000,
        })
        self.assertEqual(r.status_code, 200)
        cb = {item["id"]: item for item in r.json()["accounts"]}["cb"]
        self.assertEqual(cb["cash"], 1000)
        self.assertEqual(cb["frozen_cash"], 1000)
        self.assertEqual(cb["available_cash"], 0)

    def test_available_and_frozen_cash_derive_cash_balance(self):
        r = self.client.post("/api/account/cb/snapshot", json={
            "snapshot_date": "2026-07-17", "total": 12000,
            "available_cash": 900, "frozen_cash": 100,
        })
        self.assertEqual(r.status_code, 200)
        cb = {item["id"]: item for item in r.json()["accounts"]}["cb"]
        self.assertEqual(cb["cash"], 1000)
        self.assertEqual(cb["available_cash"], 900)

    def test_cash_balance_must_match_available_plus_frozen(self):
        r = self.client.post("/api/account/cb/snapshot", json={
            "snapshot_date": "2026-07-17", "total": 12000,
            "cash": 1000, "available_cash": 900, "frozen_cash": 200,
        })
        self.assertEqual(r.status_code, 400)

    def test_frozen_cash_cannot_exceed_cash_balance(self):
        r = self.client.post("/api/account/cb/snapshot", json={
            "snapshot_date": "2026-07-17", "total": 12000,
            "cash": 1000, "frozen_cash": 1001,
        })
        self.assertEqual(r.status_code, 400)

    def test_post_and_get_positions(self):
        payload = [{"code": "113062", "name": "常银转债", "shares": 90}]
        r = self.client.post("/api/positions/cb?position_date=2026-06-29", json=payload)
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get("/api/positions/cb")
        self.assertEqual(r2.json()[0]["code"], "113062")

    def test_atomic_account_state_save_returns_summary_and_position_metadata(self):
        r = self.client.post("/api/account/stock/state", json={
            "snapshot_date": "2026-06-29",
            "total": 209555,
            "cash": 274,
            "positions": [{"code": "1", "name": "测试股票", "shares": 100}],
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        accounts = {item["id"]: item for item in body["accounts"]}
        self.assertEqual(accounts["stock"]["total"], 209555)
        self.assertEqual(accounts["stock"]["cash"], 274)
        self.assertEqual(body["position_snapshot"]["strategy"], "stock")
        self.assertEqual(body["position_snapshot"]["position_date"], "2026-06-29")
        self.assertEqual(body["position_snapshot"]["items"][0]["code"], "000001")

    def test_atomic_account_state_save_persists_frozen_cash(self):
        r = self.client.post("/api/account/stock/state", json={
            "snapshot_date": "2026-07-17",
            "total": 12000,
            "cash": 1000,
            "frozen_cash": 400,
            "positions": [],
        })
        self.assertEqual(r.status_code, 200)
        stock = {item["id"]: item for item in r.json()["accounts"]}["stock"]
        self.assertEqual(stock["available_cash"], 600)
        self.assertEqual(stock["frozen_cash"], 400)

    def test_account_fact_update_invalidates_current_derived_orders(self):
        cb_run = db.insert_strategy_run("cb", date(2026, 7, 17))
        db.insert_cb_rankings(cb_run, pd.DataFrame([{
            "bond_code": "113062", "bond_name": "常银转债",
            "cb_price": 126.8, "premium_rate": 10.0,
            "double_low": 136.8, "score": 0.9,
        }]))
        db.insert_cb_orders(cb_run, pd.DataFrame([{
            "action": "BUY", "bond_code": "113062", "bond_name": "常银转债",
            "price": 126.8, "delta_shares": 10, "amount": 1268.0,
        }]))
        stock_run = db.insert_strategy_run("stock", date(2026, 7, 17))
        db.insert_stock_rankings(stock_run, pd.DataFrame([{
            "rank": 1, "stock_code": "600051", "stock_name": "宁波联合",
            "total_mv_yuan": 1000000000, "pe_ttm": 10.0, "roe_pct": 12.0,
        }]))
        db.insert_stock_orders(stock_run, pd.DataFrame([{
            "action": "BUY", "stock_code": "600051", "stock_name": "宁波联合",
            "price": 5.68, "delta_shares": 100, "amount": 568.0,
        }]))

        r = self.client.post("/api/account/cash/snapshot", json={
            "snapshot_date": "2026-07-17", "total": 10000,
        })

        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.get_orders("cb", "2026-07-17"), [])
        self.assertEqual(db.get_orders("stock", "2026-07-17"), [])

    def test_plan_context_update_invalidates_current_derived_orders(self):
        cb_run = db.insert_strategy_run("cb", date(2026, 7, 17))
        db.insert_cb_rankings(cb_run, pd.DataFrame([{
            "bond_code": "113062", "bond_name": "常银转债",
            "cb_price": 126.8, "premium_rate": 10.0,
            "double_low": 136.8, "score": 0.9,
        }]))
        db.insert_cb_orders(cb_run, pd.DataFrame([{
            "action": "BUY", "bond_code": "113062", "bond_name": "常银转债",
            "price": 126.8, "delta_shares": 10, "amount": 1268.0,
        }]))

        r = self.client.post("/api/account/context", json={
            "snapshot_date": "2026-07-17", "temperature": 45,
            "check_type": "a_internal",
        })

        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.get_orders("cb", "2026-07-17"), [])

    def test_position_metadata_distinguishes_missing_from_empty(self):
        missing = self.client.get("/api/positions/cb/snapshot?date=2026-06-29")
        self.assertEqual(missing.status_code, 200)
        self.assertIsNone(missing.json())

        saved = self.client.post("/api/positions/cb?position_date=2026-06-29", json=[])
        self.assertEqual(saved.status_code, 200)
        empty = self.client.get("/api/positions/cb/snapshot?date=2026-06-29")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["items"], [])
        self.assertEqual(empty.json()["position_date"], "2026-06-29")

    def test_position_metadata_exact_latest_and_asof_selection(self):
        first = self.client.post(
            "/api/positions/stock?position_date=2026-06-28",
            json=[{"code": "1", "name": "第一版", "shares": 10}],
        ).json()["position_snapshot"]
        self.client.post(
            "/api/positions/stock?position_date=2026-06-30",
            json=[{"code": "2", "name": "旧版", "shares": 20}],
        )
        revised = self.client.post(
            "/api/positions/stock?position_date=2026-06-30",
            json=[{"code": "3", "name": "修订版", "shares": 30}],
        ).json()["position_snapshot"]

        exact = self.client.get(
            "/api/positions/stock/snapshot?date=2026-06-30"
        ).json()
        latest = self.client.get("/api/positions/stock/snapshot").json()
        asof = self.client.get(
            "/api/positions/stock/snapshot?date=2026-06-29&asof=true"
        ).json()
        self.assertEqual(exact["id"], revised["id"])
        self.assertEqual(latest["id"], revised["id"])
        self.assertEqual(asof["id"], first["id"])

    def test_compatibility_position_gets_reject_malformed_dates(self):
        for path in (
            "/api/positions/cb?date=bad-date",
            "/api/positions/cb?date=bad-date&asof=true",
            "/api/positions/cb/quotes?date=bad-date",
            "/api/positions/cb/quotes?date=bad-date&asof=true",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 400)

    def test_position_routes_reject_empty_date_and_asof_without_date(self):
        for route in (
            "/api/positions/cb",
            "/api/positions/cb/snapshot",
            "/api/positions/cb/quotes",
        ):
            for query in ("date=", "asof=true"):
                path = f"{route}?{query}"
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 400)

    def test_position_writes_reject_unicode_digits_without_sqlite_error(self):
        response = self.client.post(
            "/api/positions/stock?position_date=2026-06-29",
            json=[{"code": "１２３", "name": "非法代码", "shares": 1}],
        )
        self.assertIn(response.status_code, {400, 422})
        state_response = self.client.post("/api/account/stock/state", json={
            "snapshot_date": "2026-06-29",
            "total": 100,
            "cash": 10,
            "positions": [{"code": "１２３", "name": "非法代码", "shares": 1}],
        })
        self.assertIn(state_response.status_code, {400, 422})

        with db._conn() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS c FROM position_snapshots").fetchone()["c"],
                0,
            )

    def test_state_save_validates_account_date_values_and_positions(self):
        cases = [
            (400, "/api/account/cash/state", {"snapshot_date": "2026-06-29", "total": 1, "cash": 0, "positions": []}),
            (400, "/api/account/stock/state", {"snapshot_date": "bad", "total": 1, "cash": 0, "positions": []}),
            (422, "/api/account/stock/state", {"snapshot_date": "2026-06-29", "total": -1, "cash": 0, "positions": []}),
            (400, "/api/account/stock/state", {"snapshot_date": "2026-06-29", "total": 1, "cash": 0, "positions": [{"code": "abc", "shares": 1}]}),
        ]
        for expected, url, payload in cases:
            with self.subTest(url=url, payload=payload):
                self.assertEqual(self.client.post(url, json=payload).status_code, expected)

        with db._conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM account_value_snapshots").fetchone()["c"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM position_snapshots").fetchone()["c"], 0)

    def test_request_models_forbid_extras_and_reject_invalid_numbers(self):
        cases = [
            ("/api/account/context", {"snapshot_date": "2026-06-29", "temperature": 45, "extra": True}),
            ("/api/account/stock/snapshot", {"snapshot_date": "2026-06-29", "total": -1}),
            ("/api/account/stock/state", {
                "snapshot_date": "2026-06-29", "total": 1, "cash": 0,
                "positions": [], "extra": True,
            }),
            ("/api/account/stock/state", {
                "snapshot_date": "2026-06-29", "total": 1, "cash": 0,
                "positions": [{"code": "1", "shares": 1, "extra": True}],
            }),
            ("/api/positions/stock?position_date=2026-06-29", [
                {"code": "1", "shares": -1},
            ]),
            ("/api/positions/stock?position_date=2026-06-29", [
                {"code": "1", "shares": 1, "extra": True},
            ]),
        ]
        for url, payload in cases:
            with self.subTest(url=url, payload=payload):
                self.assertEqual(self.client.post(url, json=payload).status_code, 422)

        from pydantic import ValidationError
        from app.routers.accounts import AccountStateIn
        from app.routers.positions import PositionIn

        with self.assertRaises(ValidationError):
            AccountStateIn(
                snapshot_date="2026-06-29",
                total=float("nan"),
                cash=0,
                positions=[],
            )
        with self.assertRaises(ValidationError):
            PositionIn(code="1", shares=float("inf"))
