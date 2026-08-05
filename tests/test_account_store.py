import sqlite3
import unittest

from datasource import account_store, db


class TestAccountStore(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:")
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_builds_current_summary_without_db_facade(self):
        conn = db._TEST_CONN
        account_store.insert_account_context(
            conn,
            "2026-06-29",
            45.0,
            check_type="quarterly",
            new_contribution=5000,
            b_purchase_status="available",
            b_purchase_limit=2000,
        )
        account_store.insert_account_value_snapshot(conn, "stock", "2026-06-29", 10000, 1000, 250)
        account_store.insert_account_value_snapshot(conn, "cb", "2026-06-29", 20000, 300)
        account_store.insert_account_value_snapshot(conn, "changqian", "2026-06-29", 30000)
        account_store.insert_account_value_snapshot(conn, "cash", "2026-06-29", 40000)
        account_store.insert_account_value_snapshot(conn, "overseas", "2026-06-29", 50000)

        summary = account_store.get_current_account_summary(conn)

        self.assertEqual(summary["snapshot_date"], "2026-06-29")
        self.assertEqual(summary["check_type"], "quarterly")
        self.assertEqual(summary["stock_available_cash"], 750)
        self.assertEqual(summary["total_assets"], 150000)
        self.assertEqual([item["id"] for item in summary["accounts"]], [
            "stock",
            "cb",
            "changqian",
            "overseas",
            "cash",
        ])
