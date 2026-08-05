import unittest

import pandas as pd

from strategies.stock_smallcap.target_sizing import SizingError, size_target_state
from strategies.stock_smallcap import size_orders


def rankings(count=20):
    return pd.DataFrame([
        {"stock_code": f"{600000 + index:06d}", "stock_name": f"股票{index}", "rank": index + 1}
        for index in range(count)
    ])


def positions(rows=None):
    return pd.DataFrame(rows or [], columns=["stock_code", "stock_name", "shares"])


def prices(frame, price=10.0, extra=None):
    result = {code: price for code in frame["stock_code"]}
    result.update(extra or {})
    return result


class TargetStateSizingTests(unittest.TestCase):
    def test_rejects_budget_that_cannot_buy_one_lot_of_every_top_twenty(self):
        target = rankings()

        with self.assertRaisesRegex(SizingError, "CAPACITY_CONFLICT"):
            size_target_state(target, positions(), 19_000, prices(target))

    def test_rejects_one_lot_that_exceeds_single_stock_hard_cap(self):
        target = rankings()
        price_map = prices(target)
        price_map[target.iloc[0]["stock_code"]] = 101.0

        with self.assertRaisesRegex(SizingError, "CAPACITY_CONFLICT"):
            size_target_state(target, positions(), 100_000, price_map)

    def test_required_exit_bypasses_ordinary_order_threshold(self):
        target = rankings()
        held = positions([{"stock_code": "999999", "stock_name": "退出股", "shares": 100}])
        price_map = prices(target, extra={"999999": 5.0})

        sheet, summary = size_target_state(target, held, 40_000, price_map)

        exit_row = sheet.loc[sheet["stock_code"] == "999999"].iloc[0]
        self.assertEqual(exit_row["action"], "SELL")
        self.assertEqual(exit_row["delta_shares"], -100)
        self.assertEqual(summary["ordinary_order_threshold"], 1_000)

    def test_uses_dynamic_threshold_and_never_overspends_cash(self):
        target = rankings()
        sheet, summary = size_target_state(target, positions(), 400_000, prices(target))

        self.assertEqual(summary["ordinary_order_threshold"], 2_000)
        self.assertTrue((sheet["target_shares"] % 100 == 0).all())
        self.assertGreaterEqual(summary["cash_left"], 0)

    def test_reinvests_residual_cash_when_another_lot_fits(self):
        target = rankings()
        sheet, summary = size_target_state(target, positions(), 21_500, prices(target))

        self.assertLess(summary["cash_left"], 1_000)
        self.assertEqual(sheet["target_shares"].sum(), 2_100)

    def test_negative_budget_releases_cash_by_trimming_existing_positions(self):
        target = rankings(3)
        held = positions([
            {"stock_code": "600000", "stock_name": "股票0", "shares": 1_000},
            {"stock_code": "600001", "stock_name": "股票1", "shares": 1_000},
            {"stock_code": "600002", "stock_name": "股票2", "shares": 1_000},
        ])

        sheet, summary = size_target_state(target, held, -9_000, prices(target))

        self.assertGreaterEqual(summary["cash_left"], 0)
        self.assertLess(sheet["target_shares"].sum(), 3_000)
        self.assertTrue((sheet["delta_shares"] < 0).any())

    def test_sizes_the_strategy_target_set_without_forcing_twenty_names(self):
        target = rankings(3)

        sheet, summary = size_target_state(target, positions(), 30_000, prices(target))

        self.assertEqual(summary["n_target"], 3)
        self.assertEqual(set(sheet["stock_code"]), set(target["stock_code"]))
        self.assertTrue((sheet["target_shares"] % 100 == 0).all())
        self.assertGreaterEqual(summary["cash_left"], 0)

    def test_cli_adapter_uses_the_shared_target_state_result(self):
        target = rankings()
        expected_sheet, expected_summary = size_target_state(target, positions(), 400_000, prices(target))

        actual_sheet, actual_summary = size_orders.size_rebalance(target, positions(), 400_000, prices(target))

        pd.testing.assert_frame_equal(actual_sheet, expected_sheet)
        self.assertEqual(actual_summary["ordinary_order_threshold"], expected_summary["ordinary_order_threshold"])


if __name__ == "__main__":
    unittest.main()
