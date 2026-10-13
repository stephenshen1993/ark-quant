import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import strategy_runner as runner
from datasource.request_control import report_progress


class TestRankingProgress(unittest.TestCase):
    def test_running_progress_duplicate_and_completion(self):
        entered, finish = threading.Event(), threading.Event()
        errors = []

        def work(*args):
            report_progress('补齐正股历史行情', 3, 9)
            entered.set()
            if not finish.wait(3):
                raise RuntimeError('test timed out')
            return SimpleNamespace(run_id=7)

        def run():
            try:
                runner.run_strategy('cb')
            except Exception as exc:
                errors.append(exc)

        with patch.object(runner, '_run_strategy_impl', work), patch.object(
            runner.db, 'get_strategy_run', return_value={'id': 7, 'data_date': '2026-10-12'}
        ), patch.object(runner.db, 'get_rankings_by_run_id', return_value=[{'rank': 1}]):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                status = runner.get_run_status('cb')
                self.assertEqual((status['status'], status['completed'], status['total']), ('running', 3, 9))
                self.assertIn('elapsed_seconds', status)
                with self.assertRaises(runner.StrategyRunnerError) as exc:
                    runner.run_strategy('cb')
                self.assertEqual(exc.exception.detail['code'], 'STRATEGY_RUNNING')
            finally:
                finish.set()
                thread.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(runner.get_run_status('cb')['status'], 'complete')
        self.assertEqual(runner.get_run_status('cb')['data_date'], '2026-10-12')

    def test_failure_is_visible_and_next_run_can_start(self):
        with patch.object(runner, '_run_strategy_impl', side_effect=RuntimeError('source timeout')):
            for _ in range(2):
                with self.assertRaises(runner.StrategyRunnerError):
                    runner.run_strategy('stock')
                status = runner.get_run_status('stock')
                self.assertEqual(status['status'], 'failed')
                self.assertIn('source timeout', status['error'])
                self.assertFalse(runner._RUN_LOCKS['stock'].locked())
