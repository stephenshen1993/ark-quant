import copy
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


if __name__ == "__main__":
    unittest.main()
