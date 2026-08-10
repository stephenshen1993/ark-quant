import re
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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

    def test_account_change_stales_running_plan_and_late_completion_cannot_revive_it(self):
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
        self.assertTrue(plan_lifecycle.start(plan_id, "2026-08-05", plan))

        plan_lifecycle.mark_stale()
        completed = plan_lifecycle.complete(plan_id, plan)
        plan_lifecycle.fail(plan_id, plan, {"code": "LATE_FAILURE"})

        saved = plan_lifecycle.get_plan(plan_id)
        self.assertFalse(completed)
        self.assertEqual(saved["status"], plan_lifecycle.STALE)
        self.assertEqual(saved["error"]["code"], "PLAN_INPUTS_CHANGED")

    def test_plan_id_format_documents_lifecycle_identity(self):
        plan_id = plan_lifecycle.new_plan_id("2026-08-05")

        self.assertIsNotNone(re.match(r"^plan-2026-08-05-[0-9a-f]{8}$", plan_id))

    def test_latest_plan_status_is_empty_when_the_plan_date_has_no_generation(self):
        self.assertIsNone(plan_lifecycle.get_latest_plan_status("2026-08-05"))

    def test_start_is_mutually_exclusive_for_each_plan_date(self):
        first_id = "plan-2026-08-05-00000001"
        second_id = "plan-2026-08-05-00000002"
        plan = {
            "plan_date": "2026-08-05",
            "generation": {
                "plan_id": first_id,
                "plan_date": "2026-08-05",
                "status": plan_lifecycle.DRAFT,
                "stages": [],
            },
        }

        first_started = plan_lifecycle.start(first_id, "2026-08-05", plan)
        second_started = plan_lifecycle.start(second_id, "2026-08-05", plan)

        self.assertTrue(first_started)
        self.assertFalse(second_started)
        self.assertEqual(
            plan_lifecycle.get_latest_plan_status("2026-08-05")["plan_id"],
            first_id,
        )

    def test_latest_plan_status_is_date_isolated_and_returns_the_newest_status(self):
        first_id = "plan-2026-08-05-00000001"
        latest_id = "plan-2026-08-05-00000002"
        other_date_id = "plan-2026-08-06-00000003"
        for plan_id, plan_date, status in [
            (first_id, "2026-08-05", plan_lifecycle.COMPLETE),
            (latest_id, "2026-08-05", plan_lifecycle.FAILED),
            (other_date_id, "2026-08-06", plan_lifecycle.COMPLETE),
        ]:
            db.insert_generated_plan(
                plan_id,
                plan_date,
                status,
                {"plan_date": plan_date, "generation": {"plan_id": plan_id, "status": status}},
            )
        db._TEST_CONN.execute(
            "UPDATE generated_plans SET created_at='2026-08-05T10:00:00' WHERE plan_id=?",
            (first_id,),
        )
        db._TEST_CONN.execute(
            "UPDATE generated_plans SET created_at='2026-08-05T11:00:00' WHERE plan_id=?",
            (latest_id,),
        )
        db._TEST_CONN.commit()

        latest = plan_lifecycle.get_latest_plan_status("2026-08-05")

        self.assertEqual(latest["plan_id"], latest_id)
        self.assertEqual(latest["status"], plan_lifecycle.FAILED)
        self.assertEqual(latest["plan_date"], "2026-08-05")
        self.assertNotEqual(latest["plan_id"], other_date_id)


class TestPlanLifecycleConcurrency(unittest.TestCase):
    def test_concurrent_starts_reserve_only_one_running_generation_for_a_plan_date(self):
        original_path = db.DB_PATH
        original_connection = db._TEST_CONN
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                db._TEST_CONN = None
                db.DB_PATH = Path(temp_dir) / "plan_lifecycle.db"
                db.init_db()
                start_barrier = threading.Barrier(2)

                def start_generation(plan_id: str) -> bool:
                    plan = {
                        "plan_date": "2026-08-05",
                        "generation": {
                            "plan_id": plan_id,
                            "plan_date": "2026-08-05",
                            "status": plan_lifecycle.DRAFT,
                            "stages": [],
                        },
                    }
                    start_barrier.wait()
                    return plan_lifecycle.start(plan_id, "2026-08-05", plan)

                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(
                        executor.map(
                            start_generation,
                            ["plan-2026-08-05-00000001", "plan-2026-08-05-00000002"],
                        )
                    )

                self.assertEqual(outcomes.count(True), 1)
                self.assertEqual(outcomes.count(False), 1)
                self.assertEqual(
                    plan_lifecycle.get_latest_plan_status("2026-08-05")["status"],
                    plan_lifecycle.RUNNING,
                )
        finally:
            db.DB_PATH = original_path
            db._TEST_CONN = original_connection
