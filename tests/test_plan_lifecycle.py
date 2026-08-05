import re
import sqlite3
import unittest

from app import plan_lifecycle
from datasource import db


class TestPlanLifecycle(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:")
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_records_complete_plan_and_order_batch(self):
        plan_id = plan_lifecycle.new_plan_id("2026-08-05")
        plan = {
            "plan_date": "2026-08-05",
            "generation": {
                "plan_id": plan_id,
                "plan_date": "2026-08-05",
                "status": plan_lifecycle.DRAFT,
                "stages": plan_lifecycle.generation_stages(),
            },
        }

        self.assertRegex(plan_id, r"^plan-2026-08-05-[0-9a-f]{8}$")
        plan_lifecycle.start(plan_id, "2026-08-05", plan)
        plan_lifecycle.record_order_batch(
            plan_id,
            "cb",
            [{"action": "BUY", "bond_code": "113062"}],
            {"cash_left": 100.0},
        )
        plan_lifecycle.complete(plan_id, plan)

        saved = plan_lifecycle.get_plan(plan_id)
        batch = plan_lifecycle.get_order_batch(plan_id, "cb")
        self.assertEqual(saved["status"], plan_lifecycle.COMPLETE)
        self.assertEqual(saved["plan"]["generation"]["status"], plan_lifecycle.COMPLETE)
        self.assertEqual(batch["orders"][0]["bond_code"], "113062")
        self.assertEqual(batch["summary"]["cash_left"], 100.0)

    def test_records_failed_plan_error(self):
        plan_id = "plan-2026-08-05-deadbeef"
        plan = {
            "plan_date": "2026-08-05",
            "generation": {
                "plan_id": plan_id,
                "plan_date": "2026-08-05",
                "status": plan_lifecycle.RUNNING,
                "stages": [],
            },
        }
        error = {"stage": "cb_orders", "code": "PLAN_GENERATION_FAILED"}

        plan_lifecycle.start(plan_id, "2026-08-05", plan)
        failed_plan = plan_lifecycle.fail(plan_id, plan, error)

        saved = plan_lifecycle.get_plan(plan_id)
        self.assertEqual(failed_plan["generation"]["status"], plan_lifecycle.FAILED)
        self.assertEqual(saved["status"], plan_lifecycle.FAILED)
        self.assertEqual(saved["error"], error)

    def test_marks_complete_plans_stale(self):
        plan_id = "plan-2026-08-05-deadbeef"
        plan = {
            "plan_date": "2026-08-05",
            "generation": {
                "plan_id": plan_id,
                "plan_date": "2026-08-05",
                "status": plan_lifecycle.COMPLETE,
                "stages": [],
            },
        }

        plan_lifecycle.start(plan_id, "2026-08-05", plan)
        plan_lifecycle.complete(plan_id, plan)
        plan_lifecycle.mark_stale()

        saved = plan_lifecycle.get_plan(plan_id)
        self.assertEqual(saved["status"], plan_lifecycle.STALE)
        self.assertEqual(saved["error"]["code"], "PLAN_INPUTS_CHANGED")

    def test_plan_id_format_documents_lifecycle_identity(self):
        plan_id = plan_lifecycle.new_plan_id("2026-08-05")

        self.assertIsNotNone(re.match(r"^plan-2026-08-05-[0-9a-f]{8}$", plan_id))
