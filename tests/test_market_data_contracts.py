import copy
import inspect
import unittest

from datasource.market_data_bundle import DataRequirements
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
        self.assertIn("enriched_universe", cb_requirements.dataset_fields)
        self.assertIn("merged", stock_requirements.dataset_fields)
        self.assertEqual(cb_requirements.expected_symbols_dataset, "cb_universe")
        self.assertEqual(cb_requirements.coverage_datasets, ("enriched_universe",))
        self.assertEqual(stock_requirements.expected_symbols_dataset, "universe")
        self.assertEqual(stock_requirements.coverage_datasets, ("merged",))

    def test_local_strategy_parameter_changes_do_not_invalidate_raw_inputs(self) -> None:
        stock_config = stock_run.load_config(stock_run.DEFAULT_CONFIG)
        changed_stock = copy.deepcopy(stock_config)
        changed_stock["selection"]["hold_n"] += 1
        changed_stock["filters"]["min_amount_yuan"] += 1

        cb_config = cb_run.load_config(cb_run.DEFAULT_CONFIG)
        changed_cb = copy.deepcopy(cb_config)
        changed_cb["top_n"] += 1
        changed_cb["filters"]["max_cb_price"] += 1

        self.assertEqual(
            stock_run.market_data_requirements(stock_config).fingerprint,
            stock_run.market_data_requirements(changed_stock).fingerprint,
        )
        self.assertEqual(
            cb_run.market_data_requirements(cb_config).fingerprint,
            cb_run.market_data_requirements(changed_cb).fingerprint,
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
