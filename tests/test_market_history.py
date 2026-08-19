import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from datasource.market_data_bundle import DataRequirements
from datasource.market_history import (
    DataPreparationError,
    MarketFetchResult,
    prepare_market_history,
)


class IncrementalMarketHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store_path = Path(self.temp_dir.name) / "history.sqlite3"
        self.requirements = DataRequirements(
            strategy="stock",
            dataset_fields={},
            symbol_field="stock_code",
            source="full-market-daily",
            source_version="v1",
            algorithm_version="raw-bars-v1",
            lookback_trading_days=3,
            market_fields=("raw_close", "volume", "amount", "adjustment_factor"),
        )
        self.calendar = [
            date(2026, 8, 10),
            date(2026, 8, 11),
            date(2026, 8, 12),
            date(2026, 8, 13),
            date(2026, 8, 14),
            date(2026, 8, 17),
            date(2026, 8, 18),
        ]

    @staticmethod
    def _rows(days, symbols=("600001", "600002")) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "stock_code": symbol,
                "trade_date": day.isoformat(),
                "raw_close": float(day.day),
                "volume": 100.0,
                "amount": 1000.0,
                "adjustment_factor": 1.0,
            }
            for day in days
            for symbol in symbols
        ])

    def test_cross_week_run_only_batches_missing_trading_days(self) -> None:
        batches: list[list[date]] = []

        def batch_fetch(days) -> pd.DataFrame:
            requested = list(days)
            batches.append(requested)
            return self._rows(requested)

        first = prepare_market_history(
            self.requirements,
            date(2026, 8, 14),
            ("600001", "600002"),
            self.calendar,
            batch_fetch,
            store_path=self.store_path,
        )
        second = prepare_market_history(
            self.requirements,
            date(2026, 8, 18),
            ("600001", "600002"),
            self.calendar,
            batch_fetch,
            store_path=self.store_path,
        )

        self.assertEqual(batches[0], [date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14)])
        self.assertEqual(batches[1], [date(2026, 8, 17), date(2026, 8, 18)])
        self.assertEqual(first.metadata.mode, "cold_build")
        self.assertEqual(second.metadata.mode, "incremental")
        self.assertEqual(second.metadata.missing_trading_days, ("2026-08-17", "2026-08-18"))
        self.assertEqual(second.metadata.batch_requests, 1)
        self.assertEqual(len(second.frame), 6)

    def test_batch_gap_uses_fallback_only_for_missing_symbol(self) -> None:
        fallback_calls: list[tuple[str, date, date]] = []

        def batch_fetch(days) -> MarketFetchResult:
            requested = list(days)
            return MarketFetchResult(
                self._rows(requested, symbols=("600001",)),
                external_calls=2,
            )

        def fallback_fetch(symbol: str, start: date, end: date) -> MarketFetchResult:
            fallback_calls.append((symbol, start, end))
            days = [day for day in self.calendar if start <= day <= end]
            return MarketFetchResult(
                self._rows(days, symbols=(symbol,)),
                external_calls=3,
            )

        result = prepare_market_history(
            self.requirements,
            date(2026, 8, 12),
            ("600001", "600002"),
            self.calendar,
            batch_fetch,
            fallback_fetcher=fallback_fetch,
            store_path=self.store_path,
            fallback_workers=2,
        )

        self.assertEqual(
            fallback_calls,
            [
                ("600002", date(2026, 8, 10), date(2026, 8, 12)),
            ],
        )
        self.assertEqual(result.metadata.fallback_symbols, 1)
        self.assertEqual(result.metadata.external_calls, 5)
        self.assertEqual(len(result.frame), 6)

    def test_duplicate_batch_rows_are_idempotent_and_weekends_are_never_requested(self) -> None:
        batches: list[list[date]] = []

        def batch_fetch(days) -> pd.DataFrame:
            requested = list(days)
            batches.append(requested)
            rows = self._rows(requested)
            return pd.concat([rows, rows], ignore_index=True)

        result = prepare_market_history(
            self.requirements,
            date(2026, 8, 17),
            ("600001", "600002"),
            self.calendar,
            batch_fetch,
            store_path=self.store_path,
        )

        self.assertEqual(batches, [[date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 17)]])
        self.assertEqual(len(result.frame), 6)
        self.assertNotIn(date(2026, 8, 15), batches[0])
        self.assertNotIn(date(2026, 8, 16), batches[0])

    def test_partial_failure_keeps_verified_rows_and_resumes_only_remaining_gaps(self) -> None:
        first_requests: list[list[date]] = []

        def partial_batch(days) -> pd.DataFrame:
            requested = list(days)
            first_requests.append(requested)
            return self._rows(requested[:1])

        with self.assertRaises(DataPreparationError) as caught:
            prepare_market_history(
                self.requirements,
                date(2026, 8, 12),
                ("600001", "600002"),
                self.calendar,
                partial_batch,
                store_path=self.store_path,
            )

        resumed_requests: list[list[date]] = []

        def resume_batch(days) -> pd.DataFrame:
            requested = list(days)
            resumed_requests.append(requested)
            return self._rows(requested)

        result = prepare_market_history(
            self.requirements,
            date(2026, 8, 12),
            ("600001", "600002"),
            self.calendar,
            resume_batch,
            store_path=self.store_path,
        )

        self.assertEqual(first_requests, [[date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)]])
        self.assertEqual(resumed_requests, [[date(2026, 8, 11), date(2026, 8, 12)]])
        self.assertEqual(result.metadata.mode, "incremental")
        self.assertEqual(caught.exception.detail["effective_date"], "2026-08-12")
        self.assertEqual(caught.exception.detail["stage"], "market_history")
        self.assertTrue(caught.exception.detail["missing"])

    def test_source_failure_is_structured_but_complete_cache_still_runs_offline(self) -> None:
        prepare_market_history(
            self.requirements,
            date(2026, 8, 12),
            ("600001", "600002"),
            self.calendar,
            lambda days: self._rows(days),
            store_path=self.store_path,
        )
        cached = prepare_market_history(
            self.requirements,
            date(2026, 8, 12),
            ("600001", "600002"),
            self.calendar,
            lambda _days: self.fail("完整缓存离线运行不应请求数据源"),
            store_path=self.store_path,
        )
        self.assertEqual(cached.metadata.mode, "cache_hit")

        with self.assertRaises(DataPreparationError) as caught:
            prepare_market_history(
                self.requirements,
                date(2026, 8, 13),
                ("600001", "600002"),
                self.calendar,
                lambda _days: (_ for _ in ()).throw(RuntimeError("source down")),
                store_path=self.store_path,
            )

        self.assertEqual(caught.exception.detail["source"], "full-market-daily")
        self.assertEqual(caught.exception.detail["stage"], "market_history_batch")


if __name__ == "__main__":
    unittest.main()
