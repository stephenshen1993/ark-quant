import sqlite3
import unittest

from datasource import db, position_store


class TestPositionStore(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:")
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_preserves_versions_and_reads_asof_without_db_facade(self):
        conn = db._TEST_CONN
        first = position_store.append_position_snapshot(conn, "cb", "2026-06-28", [
            {"code": "113062", "name": "常银转债", "shares": 90},
        ])
        empty = position_store.append_position_snapshot(conn, "cb", "2026-06-29", [])
        revised = position_store.append_position_snapshot(conn, "cb", "2026-06-29", [
            {"code": "123", "name": "测试转债", "shares": 10},
        ])

        self.assertGreater(revised["id"], empty["id"])
        self.assertEqual(
            position_store.get_position_snapshot_by_date(conn, "cb", "2026-06-29")["id"],
            revised["id"],
        )
        self.assertEqual(
            position_store.get_position_snapshot_asof(conn, "cb", "2026-06-28")["id"],
            first["id"],
        )
        self.assertEqual(
            position_store.get_latest_positions(conn, "cb")[0]["code"],
            "000123",
        )
