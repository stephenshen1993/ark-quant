"""tests/test_db.py — datasource/db.py 单元测试"""
import unittest
import sqlite3
from datetime import date
import math
from pathlib import Path
import tempfile
from unittest.mock import patch
import pandas as pd
from datasource import db


class TestDb(unittest.TestCase):
    def setUp(self):
        # 用内存库隔离测试
        db._TEST_CONN = sqlite3.connect(":memory:")
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_account_summary_is_derived_from_context_and_account_snapshots(self):
        db.insert_account_context("2026-06-29", 45.0)
        db.insert_account_value_snapshot("stock", "2026-06-29", 209555, 274)
        db.insert_account_value_snapshot("cb", "2026-06-29", 227183, 110)
        db.insert_account_value_snapshot("changqian", "2026-06-29", 110606)
        db.insert_account_value_snapshot("cash", "2026-06-29", 59013)
        db.insert_account_value_snapshot("overseas", "2026-06-29", 93030)
        summary = db.get_current_account_summary()
        self.assertEqual(summary["snapshot_date"], "2026-06-29")
        self.assertAlmostEqual(summary["temperature"], 45.0)
        self.assertAlmostEqual(summary["total_assets"], 699387)

    def test_available_cash_defaults_to_cash_and_subtracts_frozen_cash(self):
        db.insert_account_value_snapshot("cb", "2026-07-17", 12000, 1000)
        db.insert_account_value_snapshot("stock", "2026-07-17", 12000, 1000, 250)
        summary = db.get_current_account_summary()
        accounts = {item["id"]: item for item in summary["accounts"]}
        self.assertEqual(accounts["cb"]["available_cash"], 1000)
        self.assertEqual(accounts["stock"]["available_cash"], 750)
        self.assertEqual(accounts["stock"]["frozen_cash"], 250)

    def test_insert_cb_rankings(self):
        run_id = db.insert_strategy_run("cb", date(2026, 6, 27))
        df = pd.DataFrame([
            {"bond_code": "113062", "bond_name": "常银转债", "cb_price": 127.74,
             "premium_rate": 13.29, "double_low": 141.03, "score": 0.824},
        ])
        db.insert_cb_rankings(run_id, df)
        rows = db.get_rankings("cb", "2026-06-27")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bond_code"], "113062")
        self.assertEqual(rows[0]["rank"], 1)

    def test_insert_stock_rankings(self):
        run_id = db.insert_strategy_run("stock", date(2026, 6, 27))
        df = pd.DataFrame([
            {"rank": 1, "stock_code": "600455", "stock_name_q": "博通股份",
             "total_mv_yuan": 1.357e9, "pe_ttm": 29.76, "roe_pct": 13.83},
        ])
        db.insert_stock_rankings(run_id, df)
        rows = db.get_rankings("stock", "2026-06-27")
        self.assertEqual(rows[0]["stock_code"], "600455")
        self.assertAlmostEqual(rows[0]["market_cap"], 13.57, places=1)

    def test_insert_positions(self):
        db.insert_positions("cb", "2026-06-29", [
            {"code": "113062", "name": "常银转债", "shares": 90},
        ])
        rows = db.get_latest_positions("cb")
        self.assertEqual(rows[0]["code"], "113062")
        self.assertEqual(rows[0]["shares"], 90)

    def test_position_snapshots_preserve_empty_and_same_date_versions(self):
        first = db.append_position_snapshot("cb", "2026-06-28", [
            {"code": "113062", "name": "常银转债", "shares": 90},
        ])
        empty = db.append_position_snapshot("cb", "2026-06-29", [])
        revised = db.append_position_snapshot("cb", "2026-06-29", [
            {"code": "123", "name": "测试转债", "shares": 10},
        ])
        latest_empty = db.append_position_snapshot("cb", "2026-06-30", [])

        self.assertGreater(revised["id"], empty["id"])
        exact = db.get_position_snapshot_by_date("cb", "2026-06-29")
        self.assertEqual(exact["id"], revised["id"])
        self.assertEqual(exact["items"][0]["code"], "000123")
        self.assertEqual(db.get_position_snapshot_asof("cb", "2026-06-28")["id"], first["id"])
        self.assertEqual(db.get_position_snapshot_asof("cb", "2026-06-29")["id"], revised["id"])
        self.assertEqual(db.get_latest_position_snapshot("cb")["id"], latest_empty["id"])
        self.assertEqual(db.get_latest_position_snapshot("cb")["items"], [])
        self.assertIsNone(db.get_position_snapshot_by_date("stock", "2026-06-29"))
        self.assertIsNone(db.get_position_snapshot_asof("stock", "2026-06-29"))

    def test_legacy_positions_migrate_once_per_strategy_and_date(self):
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO cb_positions (position_date,bond_code,bond_name,shares) VALUES (?,?,?,?)",
                ("2026-06-29", "113062", "常银转债", 90),
            )
            conn.execute(
                "INSERT INTO cb_positions (position_date,bond_code,bond_name,shares) VALUES (?,?,?,?)",
                ("2026-06-29", "113062", "常银转债（修订）", 100),
            )
        db.init_db()
        snapshot = db.get_position_snapshot_by_date("cb", "2026-06-29")
        self.assertEqual(snapshot["legacy"], 1)
        self.assertEqual(snapshot["items"][0]["code"], "113062")
        self.assertEqual(snapshot["items"][0]["shares"], 100)

        with db._conn() as conn:
            conn.execute(
                "INSERT INTO cb_positions (position_date,bond_code,bond_name,shares) VALUES (?,?,?,?)",
                ("2026-06-29", "113062", "不可变后修订", 200),
            )
            conn.execute(
                "INSERT INTO cb_positions (position_date,bond_code,bond_name,shares) VALUES (?,?,?,?)",
                ("2026-06-29", "113063", "新增转债", 50),
            )
        db.init_db()

        unchanged = db.get_position_snapshot_by_date("cb", "2026-06-29")
        self.assertEqual(unchanged["id"], snapshot["id"])
        self.assertEqual(unchanged["items"], snapshot["items"])
        with db._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM position_snapshots WHERE strategy='cb' AND position_date='2026-06-29'"
            ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_legacy_migration_deduplicates_after_code_normalization(self):
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO stock_positions (position_date,stock_code,stock_name,shares) VALUES (?,?,?,?)",
                ("2026-06-29", "1", "旧记录", 10),
            )
            conn.execute(
                "INSERT INTO stock_positions (position_date,stock_code,stock_name,shares) VALUES (?,?,?,?)",
                ("2026-06-29", "000001", "最新记录", 20),
            )

        db.init_db()

        snapshot = db.get_position_snapshot_by_date("stock", "2026-06-29")
        self.assertEqual(snapshot["items"], [{
            "code": "000001",
            "name": "最新记录",
            "shares": 20.0,
        }])

    def test_position_snapshot_normalization_and_validation(self):
        snapshot = db.append_position_snapshot("stock", "2026-06-29", [
            {"code": 1, "name": "测试股票", "shares": 0},
        ])
        self.assertEqual(snapshot["items"][0]["code"], "000001")

        invalid_calls = [
            lambda: db.append_position_snapshot("fund", "2026-06-29", []),
            lambda: db.append_position_snapshot("stock", "2026/06/29", []),
            lambda: db.append_position_snapshot("stock", "2026-06-29", [{"code": "ABC", "shares": 1}]),
            lambda: db.append_position_snapshot("stock", "2026-06-29", [{"code": "１２３", "shares": 1}]),
            lambda: db.append_position_snapshot("stock", "2026-06-29", [{"code": "1234567", "shares": 1}]),
            lambda: db.append_position_snapshot("stock", "2026-06-29", [{"code": "1", "shares": -1}]),
            lambda: db.insert_account_value_snapshot("unknown", "2026-06-29", 1),
            lambda: db.insert_account_value_snapshot("stock", "not-a-date", 1),
            lambda: db.insert_account_value_snapshot("stock", "2026-06-29", math.inf),
            lambda: db.insert_account_value_snapshot("stock", "2026-06-29", 1, -1),
            lambda: db.insert_account_value_snapshot("stock", "2026-06-29", 1, 1, 2),
        ]
        for call in invalid_calls:
            with self.subTest(call=call), self.assertRaises(ValueError):
                call()

    def test_account_state_save_rolls_back_both_inserts_under_test_connection(self):
        with db._conn() as conn:
            conn.execute("""
                CREATE TRIGGER fail_position_snapshot
                BEFORE INSERT ON position_snapshots
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END
            """)
        with self.assertRaises(Exception):
            db.append_account_state_snapshot(
                "stock", "2026-06-29", 1000, 100,
                [{"code": "1", "name": "测试", "shares": 10}],
            )
        with db._conn() as conn:
            account_count = conn.execute("SELECT COUNT(*) AS c FROM account_value_snapshots").fetchone()["c"]
            position_count = conn.execute("SELECT COUNT(*) AS c FROM position_snapshots").fetchone()["c"]
        self.assertEqual(account_count, 0)
        self.assertEqual(position_count, 0)

    def test_account_state_save_rolls_back_position_when_account_write_fails(self):
        with db._conn() as conn:
            conn.execute("""
                CREATE TRIGGER fail_account_snapshot
                BEFORE INSERT ON account_value_snapshots
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END
            """)
        with self.assertRaises(Exception):
            db.append_account_state_snapshot(
                "stock", "2026-06-29", 1000, 100,
                [{"code": "1", "name": "测试", "shares": 10}],
            )
        with db._conn() as conn:
            account_count = conn.execute("SELECT COUNT(*) AS c FROM account_value_snapshots").fetchone()["c"]
            position_count = conn.execute("SELECT COUNT(*) AS c FROM position_snapshots").fetchone()["c"]
        self.assertEqual(account_count, 0)
        self.assertEqual(position_count, 0)

    def test_account_state_save_rolls_back_on_file_backed_connection(self):
        test_conn = db._TEST_CONN
        db._TEST_CONN = None
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                database_path = Path(temp_dir) / "rollback.db"
                with patch.object(db, "DB_PATH", database_path):
                    db.init_db()
                    with db._conn() as conn:
                        conn.execute("""
                            CREATE TRIGGER fail_file_position_snapshot
                            BEFORE INSERT ON position_snapshots
                            BEGIN SELECT RAISE(ABORT, 'forced file failure'); END
                        """)

                    with self.assertRaises(Exception):
                        db.append_account_state_snapshot(
                            "stock", "2026-06-29", 1000, 100,
                            [{"code": "1", "name": "测试", "shares": 10}],
                        )

                    with db._conn() as conn:
                        account_count = conn.execute(
                            "SELECT COUNT(*) AS c FROM account_value_snapshots"
                        ).fetchone()["c"]
                        position_count = conn.execute(
                            "SELECT COUNT(*) AS c FROM position_snapshots"
                        ).fetchone()["c"]
                    self.assertEqual(account_count, 0)
                    self.assertEqual(position_count, 0)
        finally:
            db._TEST_CONN = test_conn

    def test_trade_date_is_next_weekday(self):
        run_id = db.insert_strategy_run("cb", date(2026, 6, 26))  # Friday
        rows = db.get_rankings("cb", "2026-06-26")  # empty but run exists
        with db._conn() as conn:
            r = conn.execute("SELECT trade_date FROM strategy_runs WHERE id=?", (run_id,)).fetchone()
        self.assertEqual(r["trade_date"], "2026-06-29")  # Monday

    def test_incomplete_and_empty_runs_are_ineligible_for_ranking_readers(self):
        complete_id = db.create_complete_strategy_run(
            "cb",
            date(2026, 6, 26),
            date(2026, 6, 29),
            pd.DataFrame([{
                "bond_code": "113062", "bond_name": "常银转债",
                "cb_price": 127.74, "premium_rate": 13.29,
                "double_low": 141.03, "score": 0.824,
            }]),
        )
        ghost_id = db.insert_strategy_run("cb", date(2026, 6, 27))
        empty_id = db.insert_strategy_run("cb", date(2026, 6, 28))
        db.insert_cb_rankings(empty_id, pd.DataFrame())

        self.assertEqual(db.get_ranking_dates("cb"), ["2026-06-26"])
        self.assertEqual(db.get_latest_run_id("cb"), complete_id)
        self.assertEqual(db.get_latest_strategy_run("cb")["id"], complete_id)
        self.assertIsNone(db.get_strategy_run_meta("cb", "2026-06-27"))
        self.assertEqual(db.get_rankings("cb", "2026-06-28"), [])
        with db._conn() as conn:
            statuses = {
                row["id"]: row["status"]
                for row in conn.execute(
                    "SELECT id, status FROM strategy_runs WHERE id IN (?,?,?)",
                    (complete_id, ghost_id, empty_id),
                )
            }
        self.assertEqual(statuses, {
            complete_id: "complete", ghost_id: "pending", empty_id: "pending",
        })

    def test_complete_strategy_run_rolls_back_header_and_rows_under_test_connection(self):
        older_id = db.create_complete_strategy_run(
            "cb", date(2026, 6, 26), date(2026, 6, 29),
            pd.DataFrame([{"bond_code": "110000", "bond_name": "旧榜单"}]),
        )
        with db._conn() as conn:
            conn.execute("""
                CREATE TRIGGER fail_second_cb_ranking
                BEFORE INSERT ON cb_rankings WHEN NEW.rank = 2
                BEGIN SELECT RAISE(ABORT, 'forced ranking failure'); END
            """)
        rankings = pd.DataFrame([
            {"bond_code": "113062", "bond_name": "一", "score": 0.9},
            {"bond_code": "123150", "bond_name": "二", "score": 0.8},
        ])

        with self.assertRaises(Exception):
            db.create_complete_strategy_run(
                "cb", date(2026, 6, 27), date(2026, 6, 29), rankings
            )

        with db._conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM strategy_runs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cb_rankings").fetchone()[0], 1)
        self.assertEqual(db.get_latest_strategy_run("cb")["id"], older_id)

    def test_complete_strategy_run_rejects_empty_rankings_without_header(self):
        with self.assertRaisesRegex(ValueError, "at least one ranking"):
            db.create_complete_strategy_run(
                "stock", date(2026, 6, 27), date(2026, 6, 29), pd.DataFrame()
            )
        with db._conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM strategy_runs").fetchone()[0], 0)

    def test_complete_strategy_run_rolls_back_header_and_rows_in_file_database(self):
        test_conn = db._TEST_CONN
        db._TEST_CONN = None
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                database_path = Path(temp_dir) / "ranking-rollback.db"
                with patch.object(db, "DB_PATH", database_path):
                    db.init_db()
                    with db._conn() as conn:
                        conn.execute("""
                            CREATE TRIGGER fail_file_cb_ranking
                            BEFORE INSERT ON cb_rankings WHEN NEW.rank = 2
                            BEGIN SELECT RAISE(ABORT, 'forced file ranking failure'); END
                        """)
                    rankings = pd.DataFrame([
                        {"bond_code": "113062", "bond_name": "一", "score": 0.9},
                        {"bond_code": "123150", "bond_name": "二", "score": 0.8},
                    ])
                    with self.assertRaises(Exception):
                        db.create_complete_strategy_run(
                            "cb", date(2026, 6, 27), date(2026, 6, 29), rankings
                        )
                    with db._conn() as conn:
                        self.assertEqual(conn.execute("SELECT COUNT(*) FROM strategy_runs").fetchone()[0], 0)
                        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cb_rankings").fetchone()[0], 0)
        finally:
            db._TEST_CONN = test_conn

    def test_status_migration_completes_only_runs_with_rankings(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("""CREATE TABLE strategy_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                data_date TEXT NOT NULL,
                trade_date TEXT,
                created_at TEXT NOT NULL
            )""")
            conn.execute("CREATE TABLE cb_rankings (run_id INTEGER NOT NULL)")
            conn.execute("CREATE TABLE stock_rankings (run_id INTEGER NOT NULL)")
            ranked_id = conn.execute(
                "INSERT INTO strategy_runs (strategy,data_date,trade_date,created_at) VALUES ('cb','2026-06-26','2026-06-29','now')"
            ).lastrowid
            ghost_id = conn.execute(
                "INSERT INTO strategy_runs (strategy,data_date,trade_date,created_at) VALUES ('cb','2026-06-27','2026-06-29','now')"
            ).lastrowid
            conn.execute(
                "INSERT INTO cb_rankings (run_id) VALUES (?)",
                (ranked_id,),
            )

            db._migrate_strategy_run_status(conn)

            statuses = {
                row["id"]: row["status"]
                for row in conn.execute("SELECT id,status FROM strategy_runs")
            }
            self.assertEqual(statuses[ranked_id], "complete")
            self.assertEqual(statuses[ghost_id], "pending")
        finally:
            conn.close()

    def test_account_history(self):
        db.insert_account_snapshot("2026-06-22", 52.0, 200000, 300, 220000, 200, 105000, 60000, 90000)
        db.insert_account_snapshot("2026-06-29", 45.0, 209555, 274, 227183, 110, 110606, 59013, 93030)
        history = db.get_account_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["snapshot_date"], "2026-06-22")

    def test_raw_snapshot_round_trip(self):
        db.save_raw_snapshot("20260702", "merged", '{"a":[1,2]}', "stock_smallcap")
        cached = db.get_raw_snapshot("20260702", "merged", "stock_smallcap")
        self.assertEqual(cached, '{"a":[1,2]}')

    def test_raw_snapshot_miss_returns_none(self):
        self.assertIsNone(db.get_raw_snapshot("20990101", "nonexistent", None))

    def test_raw_snapshot_replace_on_duplicate(self):
        db.save_raw_snapshot("20260702", "test", '{"v":1}', None)
        db.save_raw_snapshot("20260702", "test", '{"v":2}', None)
        self.assertEqual(db.get_raw_snapshot("20260702", "test", None), '{"v":2}')
