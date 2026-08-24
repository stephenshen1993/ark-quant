import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from datasource.derived_store import (
    prepare_derived_factors,
    prepare_strategy_ranking,
)


class DerivedStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store_path = Path(self.temp_dir.name) / "derived.sqlite3"
        self.inputs = pd.DataFrame([
            {"stock_code": "600001", "raw_close": 10.0},
            {"stock_code": "600002", "raw_close": 20.0},
        ])

    def test_unchanged_factor_identity_reuses_values(self) -> None:
        calls: list[tuple[str, ...]] = []

        def compute(frame: pd.DataFrame) -> pd.DataFrame:
            calls.append(tuple(frame["stock_code"]))
            return pd.DataFrame({
                "stock_code": frame["stock_code"],
                "momentum_20d": frame["raw_close"] / 10,
            })

        first = prepare_derived_factors(
            self.inputs,
            symbol_field="stock_code",
            effective_date=date(2026, 8, 18),
            factor_name="momentum_20d",
            algorithm_version="v1",
            input_fingerprint="raw-a",
            compute=compute,
            store_path=self.store_path,
        )
        second = prepare_derived_factors(
            self.inputs,
            symbol_field="stock_code",
            effective_date=date(2026, 8, 18),
            factor_name="momentum_20d",
            algorithm_version="v1",
            input_fingerprint="raw-a",
            compute=lambda _frame: self.fail("相同因子身份不应重算"),
            store_path=self.store_path,
        )

        self.assertEqual(calls, [("600001", "600002")])
        self.assertEqual(first.metadata.mode, "cold_build")
        self.assertEqual(second.metadata.mode, "cache_hit")

    def test_algorithm_version_recomputes_factor_without_refetching_raw_input(self) -> None:
        raw_fetches = 0

        def raw_input() -> pd.DataFrame:
            nonlocal raw_fetches
            raw_fetches += 1
            return self.inputs

        frame = raw_input()
        prepare_derived_factors(
            frame,
            symbol_field="stock_code",
            effective_date=date(2026, 8, 18),
            factor_name="momentum_20d",
            algorithm_version="v1",
            input_fingerprint="raw-a",
            compute=lambda value: pd.DataFrame({
                "stock_code": value["stock_code"], "momentum_20d": [1.0, 2.0]
            }),
            store_path=self.store_path,
        )
        prepare_derived_factors(
            frame,
            symbol_field="stock_code",
            effective_date=date(2026, 8, 18),
            factor_name="momentum_20d",
            algorithm_version="v2",
            input_fingerprint="raw-a",
            compute=lambda value: pd.DataFrame({
                "stock_code": value["stock_code"], "momentum_20d": [1.1, 2.1]
            }),
            store_path=self.store_path,
        )

        self.assertEqual(raw_fetches, 1)

    def test_partial_factor_mode_keeps_symbols_with_sufficient_history(self) -> None:
        result = prepare_derived_factors(
            self.inputs,
            symbol_field="stock_code",
            effective_date=date(2026, 8, 18),
            factor_name="momentum_20d",
            algorithm_version="v1",
            input_fingerprint="raw-partial",
            compute=lambda _frame: pd.DataFrame([
                {"stock_code": "600001", "momentum_20d": 1.0},
            ]),
            store_path=self.store_path,
            allow_partial=True,
        )

        self.assertEqual(result.frame.to_dict("records"), [
            {"stock_code": "600001", "momentum_20d": 1.0},
        ])
        self.assertEqual(result.metadata.refreshed_records, 1)

    def test_config_change_recomputes_ranking_locally(self) -> None:
        computes = 0
        remote_calls = 0

        def compute() -> pd.DataFrame:
            nonlocal computes
            computes += 1
            return pd.DataFrame([{"stock_code": "600001", "rank": 1}])

        first = prepare_strategy_ranking(
            strategy="stock",
            effective_date=date(2026, 8, 18),
            strategy_version="smallcap-v1",
            config_fingerprint="config-a",
            input_fingerprint="raw-a",
            compute=compute,
            store_path=self.store_path,
        )
        second = prepare_strategy_ranking(
            strategy="stock",
            effective_date=date(2026, 8, 18),
            strategy_version="smallcap-v1",
            config_fingerprint="config-b",
            input_fingerprint="raw-a",
            compute=compute,
            store_path=self.store_path,
        )
        cached = prepare_strategy_ranking(
            strategy="stock",
            effective_date=date(2026, 8, 18),
            strategy_version="smallcap-v1",
            config_fingerprint="config-b",
            input_fingerprint="raw-a",
            compute=lambda: self.fail("相同榜单身份不应重排"),
            store_path=self.store_path,
        )

        self.assertEqual(computes, 2)
        self.assertEqual(remote_calls, 0)
        self.assertEqual(first.metadata.mode, "cold_build")
        self.assertEqual(second.metadata.mode, "cold_build")
        self.assertEqual(cached.metadata.mode, "cache_hit")


if __name__ == "__main__":
    unittest.main()
