import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from datasource.fundamental_store import (
    FundamentalRequirements,
    prepare_fundamentals,
)


class FundamentalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store_path = Path(self.temp_dir.name) / "fundamentals.sqlite3"
        self.requirements = FundamentalRequirements(
            symbol_field="stock_code",
            metrics=("roe_pct",),
            caliber="deducted-profit-ttm/annual-net-assets",
            source="ths",
            source_version="v1",
        )

    @staticmethod
    def _rows(symbols, report_period: str) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "stock_code": symbol,
                "report_period": report_period,
                "roe_pct": 5.0,
            }
            for symbol in symbols
        ])

    def test_same_report_period_reuses_all_metrics_without_fetching(self) -> None:
        calls: list[tuple[tuple[str, ...], str]] = []

        def fetch(symbols, report_period, _metrics) -> pd.DataFrame:
            calls.append((tuple(symbols), report_period))
            return self._rows(symbols, report_period)

        first = prepare_fundamentals(
            self.requirements,
            ("600001", "600002"),
            "2026-06-30",
            fetch,
            effective_date=date(2026, 8, 18),
            store_path=self.store_path,
        )
        second = prepare_fundamentals(
            self.requirements,
            ("600001", "600002"),
            "2026-06-30",
            lambda *_: self.fail("同报告期完整缓存不应访问外部数据源"),
            effective_date=date(2026, 8, 25),
            store_path=self.store_path,
        )

        self.assertEqual(calls, [(("600001", "600002"), "2026-06-30")])
        self.assertEqual(first.metadata.refreshed_fundamentals, 2)
        self.assertEqual(second.metadata.reused_fundamentals, 2)
        self.assertEqual(second.metadata.external_calls, 0)
        self.assertEqual(second.metadata.mode, "cache_hit")

    def test_new_report_period_only_fetches_new_period(self) -> None:
        requested: list[str] = []

        def fetch(symbols, report_period, _metrics) -> pd.DataFrame:
            requested.append(report_period)
            return self._rows(symbols, report_period)

        prepare_fundamentals(
            self.requirements,
            ("600001",),
            "2026-03-31",
            fetch,
            effective_date=date(2026, 5, 10),
            store_path=self.store_path,
        )
        result = prepare_fundamentals(
            self.requirements,
            ("600001",),
            "2026-06-30",
            fetch,
            effective_date=date(2026, 9, 1),
            store_path=self.store_path,
        )

        self.assertEqual(requested, ["2026-03-31", "2026-06-30"])
        self.assertEqual(result.frame["report_period"].unique().tolist(), ["2026-06-30"])

    def test_missing_critical_metric_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "600002.*roe_pct"):
            prepare_fundamentals(
                self.requirements,
                ("600001", "600002"),
                "2026-06-30",
                lambda symbols, report_period, _metrics: self._rows(symbols[:1], report_period),
                effective_date=date(2026, 8, 18),
                store_path=self.store_path,
            )

    def test_caliber_or_source_version_never_share_cache(self) -> None:
        prepare_fundamentals(
            self.requirements,
            ("600001",),
            "2026-06-30",
            lambda symbols, period, _metrics: self._rows(symbols, period),
            effective_date=date(2026, 8, 18),
            store_path=self.store_path,
        )
        changed = FundamentalRequirements(
            symbol_field="stock_code",
            metrics=("roe_pct",),
            caliber="deducted-profit-ttm/average-equity",
            source="ths",
            source_version="v2",
        )
        calls = 0

        def fetch(symbols, period, _metrics) -> pd.DataFrame:
            nonlocal calls
            calls += 1
            return self._rows(symbols, period)

        prepare_fundamentals(
            changed,
            ("600001",),
            "2026-06-30",
            fetch,
            effective_date=date(2026, 8, 18),
            store_path=self.store_path,
        )

        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
