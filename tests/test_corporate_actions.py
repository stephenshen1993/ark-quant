import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from datasource.derived_store import (
    prepare_derived_factors,
    prepare_strategy_ranking,
)
from datasource.market_data_bundle import DataRequirements
from datasource.market_history import (
    prepare_market_history,
    reconcile_corporate_actions,
)


class CorporateActionInvalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.history_path = root / "history.sqlite3"
        self.derived_path = root / "derived.sqlite3"
        self.effective_date = date(2026, 8, 18)
        self.requirements = DataRequirements(
            strategy="stock",
            dataset_fields={},
            symbol_field="stock_code",
            source="full-market-daily",
            source_version="v1",
            algorithm_version="raw-bars-v1",
            market_fields=("raw_close", "volume", "amount", "adjustment_factor"),
        )

    def _market_rows(self, factor: float = 1.0) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "stock_code": symbol,
                "trade_date": self.effective_date.isoformat(),
                "raw_close": close,
                "volume": 100.0,
                "amount": 1000.0,
                "adjustment_factor": factor,
            }
            for symbol, close in (("600001", 10.0), ("600002", 20.0))
        ])

    def test_single_symbol_action_invalidates_only_its_factors_and_dependent_ranking(self) -> None:
        history = prepare_market_history(
            self.requirements,
            self.effective_date,
            ("600001", "600002"),
            (self.effective_date,),
            lambda _days: self._market_rows(),
            store_path=self.history_path,
        )
        factor_calls: list[tuple[str, ...]] = []

        def compute_factors(frame: pd.DataFrame) -> pd.DataFrame:
            factor_calls.append(tuple(frame["stock_code"]))
            return pd.DataFrame({
                "stock_code": frame["stock_code"],
                "adjusted_close": frame["raw_close"] * frame["adjustment_factor"],
            })

        prepare_derived_factors(
            history.frame,
            symbol_field="stock_code",
            effective_date=self.effective_date,
            factor_name="adjusted_close",
            algorithm_version="v1",
            input_fingerprint="history-v1",
            compute=compute_factors,
            store_path=self.derived_path,
        )
        ranking_calls = 0

        def compute_ranking() -> pd.DataFrame:
            nonlocal ranking_calls
            ranking_calls += 1
            return pd.DataFrame([{"stock_code": "600001", "rank": 1}])

        prepare_strategy_ranking(
            strategy="stock",
            effective_date=self.effective_date,
            strategy_version="v1",
            config_fingerprint="config-a",
            input_fingerprint="history-v1",
            compute=compute_ranking,
            store_path=self.derived_path,
        )

        invalidation = reconcile_corporate_actions(
            self.requirements,
            pd.DataFrame([{
                "stock_code": "600001",
                "trade_date": self.effective_date.isoformat(),
                "adjustment_factor": 1.2,
            }]),
            store_path=self.history_path,
            derived_store_path=self.derived_path,
        )

        refreshed_history = prepare_market_history(
            self.requirements,
            self.effective_date,
            ("600001", "600002"),
            (self.effective_date,),
            lambda _days: self.fail("复权更新后原始行情仍应完整命中"),
            store_path=self.history_path,
        )
        prepare_derived_factors(
            refreshed_history.frame,
            symbol_field="stock_code",
            effective_date=self.effective_date,
            factor_name="adjusted_close",
            algorithm_version="v1",
            input_fingerprint="history-v1",
            compute=compute_factors,
            store_path=self.derived_path,
        )
        prepare_strategy_ranking(
            strategy="stock",
            effective_date=self.effective_date,
            strategy_version="v1",
            config_fingerprint="config-a",
            input_fingerprint="history-v1",
            compute=compute_ranking,
            store_path=self.derived_path,
        )

        first = refreshed_history.frame.set_index("stock_code").loc["600001"]
        second = refreshed_history.frame.set_index("stock_code").loc["600002"]
        self.assertEqual(first["raw_close"], 10.0)
        self.assertEqual(first["adjustment_factor"], 1.2)
        self.assertEqual(second["raw_close"], 20.0)
        self.assertEqual(second["adjustment_factor"], 1.0)
        self.assertEqual(factor_calls, [("600001", "600002"), ("600001",)])
        self.assertEqual(ranking_calls, 2)
        self.assertEqual(invalidation.changed_symbols, ("600001",))
        self.assertEqual(invalidation.invalidated_factors, 1)
        self.assertEqual(invalidation.invalidated_rankings, 1)


if __name__ == "__main__":
    unittest.main()
