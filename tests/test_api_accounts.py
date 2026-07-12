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
        self.assertNotIn("snapshot_date", r.json())
        self.assertNotIn("temperature", r.json())
        self.assertEqual(r.json()["context"]["snapshot_date"], "2026-06-29")
        self.assertEqual(r.json()["context"]["temperature"], 45.0)
        self.assertNotIn("account_updated_at", r.json())
        self.assertEqual(r.json()["total_assets"], 0)
        self.assertEqual(len(r.json()["accounts"]), 5)

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
