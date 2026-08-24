import copy
import inspect
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from datasource.market_data_bundle import DataRequirements, prepare_market_data_bundle
from strategies.cb_rotation import run as cb_run
from strategies.stock_smallcap import run as stock_run


class StrategyMarketDataContractTests(unittest.TestCase):
    def test_all_strategies_declare_market_data_requirements(self) -> None:
        cb_requirements = cb_run.market_data_requirements(
            cb_run.load_config(cb_run.DEFAULT_CONFIG)
        )
        stock_requirements = stock_run.market_data_requirements(
            stock_run.load_config(stock_run.DEFAULT_CONFIG)
        )

        self.assertIsInstance(cb_requirements, DataRequirements)
        self.assertIsInstance(stock_requirements, DataRequirements)
        self.assertEqual(cb_requirements.strategy, "cb")
        self.assertEqual(stock_requirements.strategy, "stock")
        self.assertGreaterEqual(cb_requirements.lookback_trading_days, 20)
        self.assertIn("market_universe", cb_requirements.dataset_fields)
        self.assertIn("enriched_universe", cb_requirements.dataset_fields)
        self.assertIn("merged", stock_requirements.dataset_fields)
        self.assertEqual(cb_requirements.expected_symbols_dataset, "market_universe")
        self.assertEqual(cb_requirements.coverage_datasets, ("enriched_universe",))
        self.assertEqual(stock_requirements.expected_symbols_dataset, "universe")
        self.assertEqual(stock_requirements.coverage_datasets, ("merged",))

    def test_cb_bundle_uses_market_universe_as_coverage_contract(self) -> None:
        requirements = cb_run.market_data_requirements(
            cb_run.load_config(cb_run.DEFAULT_CONFIG)
        )
        frames = {
            "cb_universe": pd.DataFrame([
                {"bond_code": "113001", "stock_code": "600001", "cb_price": 110.0},
                {"bond_code": "113002", "stock_code": "600002", "cb_price": 180.0},
            ]),
            "market_universe": pd.DataFrame([
                {"bond_code": "113001", "stock_code": "600001", "cb_price": 110.0},
                {"bond_code": "113002", "stock_code": "600002", "cb_price": 180.0},
            ]),
            "enriched_universe": pd.DataFrame([
                {
                    "bond_code": "113001",
                    "stock_code": "600001",
                    "cb_price": 110.0,
                    "turnover_yuan": 12_000_000.0,
                    "turnover_trade_date": "2026-08-21",
                    "stock_momentum_20d": 0.05,
                    "stock_volatility_20d": 0.02,
                    "stock_factor_trade_date": "2026-08-21",
                    "market_cap": 5_000_000_000.0,
                    "market_cap_as_of_date": "2026-08-21",
                },
                {
                    "bond_code": "113002",
                    "stock_code": "600002",
                    "cb_price": 180.0,
                    "turnover_yuan": 13_000_000.0,
                    "turnover_trade_date": "2026-08-21",
                    "stock_momentum_20d": 0.04,
                    "stock_volatility_20d": 0.03,
                    "stock_factor_trade_date": "2026-08-21",
                    "market_cap": 6_000_000_000.0,
                    "market_cap_as_of_date": "2026-08-21",
                },
            ]),
        }

        with tempfile.TemporaryDirectory() as tmp:
            bundle = prepare_market_data_bundle(
                requirements,
                date(2026, 8, 21),
                lambda: frames,
                cache_root=Path(tmp),
            )

        self.assertEqual(bundle.manifest["expected_symbols"], ["113001", "113002"])
        self.assertEqual(
            bundle.manifest["dataset_actual_symbols"]["cb_universe"],
            ["113001", "113002"],
        )
        self.assertEqual(
            bundle.manifest["dataset_actual_symbols"]["enriched_universe"],
            ["113001", "113002"],
        )

    def test_local_strategy_parameter_changes_do_not_invalidate_raw_inputs(self) -> None:
        stock_config = stock_run.load_config(stock_run.DEFAULT_CONFIG)
        changed_stock = copy.deepcopy(stock_config)
        changed_stock["selection"]["hold_n"] += 1
        changed_stock["filters"]["min_amount_yuan"] += 1

        cb_config = cb_run.load_config(cb_run.DEFAULT_CONFIG)
        changed_cb = copy.deepcopy(cb_config)
        changed_cb["top_n"] += 1
        changed_cb["filters"]["min_turnover_yuan"] += 1

        self.assertEqual(
            stock_run.market_data_requirements(stock_config).fingerprint,
            stock_run.market_data_requirements(changed_stock).fingerprint,
        )
        self.assertEqual(
            cb_run.market_data_requirements(cb_config).fingerprint,
            cb_run.market_data_requirements(changed_cb).fingerprint,
        )

    def test_cb_strategy_filter_changes_do_not_invalidate_input_bundle(self) -> None:
        config = cb_run.load_config(cb_run.DEFAULT_CONFIG)
        changed = copy.deepcopy(config)
        changed["filters"]["max_cb_price"] += 1

        self.assertEqual(
            cb_run.market_data_requirements(config).fingerprint,
            cb_run.market_data_requirements(changed).fingerprint,
        )

    def test_stock_test_universe_uses_an_isolated_raw_bundle(self) -> None:
        config = stock_run.load_config(stock_run.DEFAULT_CONFIG)

        full = stock_run.market_data_requirements(config)
        limited = stock_run.market_data_requirements(config, max_universe=25)

        self.assertNotEqual(full.fingerprint, limited.fingerprint)

    def test_cb_production_run_uses_history_and_versioned_factor_adapter(self) -> None:
        run_source = inspect.getsource(cb_run.run)
        adapter_source = inspect.getsource(cb_run.prepare_cb_history_inputs)

        self.assertIn("prepare_cb_history_inputs(", run_source)
        self.assertNotIn("enrich_cb_with_daily_market_data(", run_source)
        self.assertNotIn("fetch_stock_factors_with_cache(", run_source)
        self.assertIn("prepare_market_history(", adapter_source)
        self.assertIn("reconcile_corporate_actions(", adapter_source)
        self.assertIn("prepare_derived_factors(", adapter_source)


if __name__ == "__main__":
    unittest.main()
