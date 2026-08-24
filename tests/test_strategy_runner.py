import sqlite3
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from datasource import db


class TestStrategyRunner(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_ensure_rankings_reuses_existing_complete_run(self):
        from app import strategy_runner

        run_id = db.create_complete_strategy_run(
            "cb",
            date(2026, 6, 29),
            date(2026, 6, 30),
            pd.DataFrame([{"bond_code": "113062", "bond_name": "常银转债", "score": 0.9}]),
        )

        with patch("app.strategy_runner._run_strategy_impl") as run_impl:
            result = strategy_runner.ensure_rankings("cb", "2026-06-29")

        run_impl.assert_not_called()
        self.assertFalse(result.generated)
        self.assertEqual(result.strategy, "cb")
        self.assertEqual(result.data_date, "2026-06-29")
        self.assertEqual(result.trade_date, "2026-06-30")
        self.assertEqual(result.run_id, run_id)
        self.assertEqual(result.item_count, 1)
        self.assertEqual(result.preparation["ranking"]["mode"], "cache_hit")
        self.assertEqual(result.preparation["ranking"]["mode_label"], "完整缓存命中")
        self.assertEqual(result.preparation["ranking"]["external_calls"], 0)
        self.assertEqual(result.preparation["ranking"]["reused_records"], 1)
        self.assertIn("total", result.preparation["ranking"]["stage_timings_ms"])

    def test_ensure_rankings_generates_missing_rankings(self):
        from app import strategy_runner

        def generate_rankings(strategy: str, task):
            self.assertEqual(strategy, "stock")
            self.assertEqual(task.effective_date, date(2026, 6, 29))
            generated_id = db.create_complete_strategy_run(
                "stock",
                date(2026, 6, 29),
                date(2026, 6, 30),
                pd.DataFrame([{
                    "rank": 1,
                    "stock_code": "600051",
                    "stock_name": "宁波联合",
                    "total_mv_yuan": 1000000000,
                }]),
            )
            return SimpleNamespace(run_id=generated_id)

        with patch(
            "app.strategy_runner._run_strategy_impl",
            side_effect=generate_rankings,
        ):
            result = strategy_runner.ensure_rankings("stock", "2026-06-29")

        self.assertTrue(result.generated)
        self.assertEqual(result.item_count, 1)

    def test_run_task_freezes_started_at_and_effective_date(self):
        from app import strategy_runner

        task = strategy_runner.freeze_strategy_run_task(
            effective_date="2026-06-29",
            now=datetime(2026, 6, 30, 9, 24, 59),
        )

        self.assertEqual(task.started_at, datetime(2026, 6, 30, 9, 24, 59))
        self.assertEqual(task.effective_date, date(2026, 6, 29))
        self.assertEqual(task.effective_date_iso, "2026-06-29")

    def test_strategy_adapter_forwards_one_frozen_task_to_strategy(self):
        from app import strategy_runner
        from strategies.stock_smallcap.run import DEFAULT_CONFIG, DEFAULT_POSITIONS

        task = strategy_runner.StrategyRunTask(
            started_at=datetime(2026, 6, 30, 9, 24, 59),
            effective_date=date(2026, 6, 29),
        )
        artifacts = SimpleNamespace(run_id=7)

        with patch("strategies.stock_smallcap.run.run", return_value=artifacts) as run_impl:
            result = strategy_runner._run_strategy_impl("stock", task)

        self.assertIs(result, artifacts)
        run_impl.assert_called_once_with(
            DEFAULT_CONFIG,
            DEFAULT_POSITIONS,
            effective_date=date(2026, 6, 29),
            started_at=datetime(2026, 6, 30, 9, 24, 59),
        )

    def test_cb_adapter_forwards_the_same_market_data_task_contract(self):
        from app import strategy_runner
        from strategies.cb_rotation.run import DEFAULT_CONFIG, DEFAULT_POSITIONS

        task = strategy_runner.StrategyRunTask(
            started_at=datetime(2026, 6, 30, 15, 11),
            effective_date=date(2026, 6, 30),
        )
        artifacts = SimpleNamespace(run_id=8)

        with patch("strategies.cb_rotation.run.run", return_value=artifacts) as run_impl:
            result = strategy_runner._run_strategy_impl("cb", task)

        self.assertIs(result, artifacts)
        run_impl.assert_called_once_with(
            DEFAULT_CONFIG,
            DEFAULT_POSITIONS,
            effective_date=date(2026, 6, 30),
            started_at=datetime(2026, 6, 30, 15, 11),
        )

    def test_generated_ranking_response_exposes_preparation_status(self):
        from app import strategy_runner

        run_id = db.create_complete_strategy_run(
            "stock",
            date(2026, 6, 29),
            date(2026, 6, 30),
            pd.DataFrame([{
                "rank": 1,
                "stock_code": "600051",
                "stock_name": "宁波联合",
                "total_mv_yuan": 1000000000,
            }]),
        )
        preparation = {
            "market": {
                "effective_date": "2026-06-29",
                "mode": "cache_hit",
                "mode_label": "完整缓存命中",
                "external_calls": 0,
            }
        }

        with patch(
            "app.strategy_runner._run_strategy_impl",
            return_value=SimpleNamespace(run_id=run_id, preparation=preparation),
        ):
            result = strategy_runner.run_strategy("stock")

        self.assertEqual(result.to_ranking_response()["preparation"], preparation)

    def test_ensure_rankings_rejects_generated_date_mismatch(self):
        from app import strategy_runner

        generated_id = db.create_complete_strategy_run(
            "cb",
            date(2026, 6, 28),
            date(2026, 6, 29),
            pd.DataFrame([{"bond_code": "113062", "bond_name": "常银转债", "score": 0.9}]),
        )

        with patch(
            "app.strategy_runner._run_strategy_impl",
            return_value=SimpleNamespace(run_id=generated_id),
        ):
            with self.assertRaises(strategy_runner.StrategyRunnerError) as caught:
                strategy_runner.ensure_rankings("cb", "2026-06-29")

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "STRATEGY_RANKING_DATE_MISMATCH")
        self.assertEqual(caught.exception.detail["data_date"], "2026-06-28")

    def test_running_strategy_has_structured_error(self):
        from app import strategy_runner

        lock = strategy_runner._RUN_LOCKS["cb"]
        self.assertTrue(lock.acquire(blocking=False))
        self.addCleanup(lock.release)

        with self.assertRaises(strategy_runner.StrategyRunnerError) as caught:
            strategy_runner.run_strategy("cb")

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "STRATEGY_RUNNING")

    def test_missing_persisted_run_is_classified(self):
        from app import strategy_runner

        with patch(
            "app.strategy_runner._run_strategy_impl",
            return_value=SimpleNamespace(run_id=999),
        ):
            with self.assertRaises(strategy_runner.StrategyRunnerError) as caught:
                strategy_runner.run_strategy("cb")

        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(caught.exception.detail["code"], "STRATEGY_PERSISTENCE_ERROR")

    def test_empty_persisted_run_is_classified(self):
        from app import strategy_runner

        empty_run_id = db.insert_strategy_run("cb", date(2026, 6, 29))
        db._TEST_CONN.execute(
            "UPDATE strategy_runs SET status='complete' WHERE id=?",
            (empty_run_id,),
        )
        db._TEST_CONN.commit()

        with patch(
            "app.strategy_runner._run_strategy_impl",
            return_value=SimpleNamespace(run_id=empty_run_id),
        ):
            with self.assertRaises(strategy_runner.StrategyRunnerError) as caught:
                strategy_runner.run_strategy("cb")

        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(caught.exception.detail["code"], "STRATEGY_PERSISTENCE_ERROR")

    def test_data_source_failure_is_classified(self):
        from app import strategy_runner

        with self.assertLogs("app.strategy_runner", level="ERROR") as logs:
            with patch(
                "app.strategy_runner._run_strategy_impl",
                side_effect=RuntimeError("数据源获取失败"),
            ):
                with self.assertRaises(strategy_runner.StrategyRunnerError) as caught:
                    strategy_runner.run_strategy("stock")

        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(caught.exception.detail["code"], "DATA_SOURCE_UNAVAILABLE")
        self.assertIn("strategy=stock", logs.output[0])
        self.assertIn("数据源获取失败", logs.output[0])


if __name__ == "__main__":
    unittest.main()
