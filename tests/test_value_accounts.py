from __future__ import annotations

import unittest

import value_accounts


class ComputeMarketValueTests(unittest.TestCase):
    def test_sums_shares_times_price(self) -> None:
        mv = value_accounts.compute_market_value(
            {"002486": 4700, "003008": 600},
            {"002486": 2.5, "003008": 30.0},
        )
        self.assertAlmostEqual(mv, 4700 * 2.5 + 600 * 30.0)

    def test_empty_holdings_is_zero(self) -> None:
        self.assertEqual(value_accounts.compute_market_value({}, {}), 0.0)

    def test_raises_on_missing_price(self) -> None:
        with self.assertRaises(ValueError):
            value_accounts.compute_market_value({"002486": 100}, {"003008": 5.0})


if __name__ == "__main__":
    unittest.main()
