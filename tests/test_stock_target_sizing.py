import unittest
from fractions import Fraction
from itertools import product

import pandas as pd

from strategies.discrete_target_sizing import score_targets, stock_fee_estimate
from strategies.stock_smallcap.target_sizing import SizingError, size_target_state
from strategies.stock_smallcap import size_orders
from strategies.stock_smallcap.milp_target_sizing import solve_stock_targets


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


def exhaustive_equal_weight_truth(
    *,
    target_codes,
    holdings,
    prices_by_code,
    cash,
    lot,
    max_single_weight,
    ordinary_order_threshold,
):
    """Small exact oracle that does not reuse production scoring or fees."""
    total_value = Fraction(str(cash)) + sum(
        Fraction(shares) * Fraction(str(prices_by_code[code]))
        for code, shares in holdings.items()
    )
    cap_value = total_value * Fraction(str(max_single_weight))
    states_by_code = []
    for code in target_codes:
        current = holdings.get(code, 0)
        price = Fraction(str(prices_by_code[code]))
        states = []
        if current:
            states.append(current)
        maximum_shares = int(cap_value // price // lot) * lot
        for shares in range(current + lot, maximum_shares + 1, lot):
            if (shares - current) * price >= Fraction(str(ordinary_order_threshold)):
                states.append(shares)
        states_by_code.append(states)

    candidates = []
    target_weight = Fraction(1, len(target_codes))
    for selected_shares in product(*states_by_code):
        targets = dict(zip(target_codes, selected_shares))
        order_count = sum(
            targets[code] != holdings.get(code, 0)
            for code in target_codes
        )
        fee = Fraction(5 * order_count)
        denominator = total_value - fee
        values = {
            code: Fraction(targets[code]) * Fraction(str(prices_by_code[code]))
            for code in target_codes
        }
        cash_left = denominator - sum(values.values())
        if cash_left < 0:
            continue
        deviations = [
            abs(values[code] / denominator - target_weight)
            for code in target_codes
        ]
        score = (
            sum(
                (values[code] / denominator - target_weight) ** 2
                for code in target_codes
            ),
            max(*deviations, cash_left / denominator),
            sum(deviations) + cash_left / denominator,
            cash_left,
            fee,
            order_count,
            tuple(-targets[code] for code in target_codes),
        )
        candidates.append((score, targets))
    return min(candidates, key=lambda candidate: candidate[0])[1]


class TargetStateSizingTests(unittest.TestCase):
    def test_milp_matches_independent_cross_order_count_truth(self):
        cap_weights = {2: 0.70, 3: 0.45, 4: 0.35, 5: 0.30}
        for target_count in range(2, 6):
            for variant in range(4):
                with self.subTest(target_count=target_count, variant=variant):
                    target_codes = [
                        f"{600100 + index:06d}"
                        for index in range(target_count)
                    ]
                    price_map = {
                        code: float(8 + ((index * 3 + variant) % 7))
                        for index, code in enumerate(target_codes)
                    }
                    held = {
                        code: 100
                        for index, code in enumerate(target_codes)
                        if (index + variant) % 2
                    }
                    budget = 4_000.0 + variant * 137.0
                    cap_weight = cap_weights[target_count]
                    threshold = 500.0
                    expected = exhaustive_equal_weight_truth(
                        target_codes=target_codes,
                        holdings=held,
                        prices_by_code=price_map,
                        cash=budget,
                        lot=100,
                        max_single_weight=cap_weight,
                        ordinary_order_threshold=threshold,
                    )

                    actual = solve_stock_targets(
                        target_codes=target_codes,
                        holdings=held,
                        prices=price_map,
                        cash=budget,
                        lot=100,
                        max_single_weight=cap_weight,
                        ordinary_order_threshold=threshold,
                    )

                    self.assertEqual(actual.targets, expected)

    def test_milp_matches_exhaustive_truth_on_a_small_discrete_problem(self):
        target_codes = ["600001", "600002", "600003"]
        price_map = {"600001": 17.706, "600002": 16.929, "600003": 24.107}
        budget = 19_118.21
        threshold = 1_000.0
        cap_value = budget * 0.60
        candidates = []
        for first_shares in range(100, 700, 100):
            for second_shares in range(100, 700, 100):
                for third_shares in range(100, 500, 100):
                    targets = dict(
                        zip(
                            target_codes,
                            [first_shares, second_shares, third_shares],
                        )
                    )
                    if any(
                        shares * price_map[code] < threshold
                        or shares * price_map[code] > cap_value
                        for code, shares in targets.items()
                    ):
                        continue
                    fee = stock_fee_estimate(targets, {}, target_codes, price_map)
                    cash_left = budget - sum(
                        shares * price_map[code] for code, shares in targets.items()
                    ) - fee
                    if cash_left < 0:
                        continue
                    overall_score = score_targets(
                        targets,
                        target_codes=target_codes,
                        prices=price_map,
                        cash_left=cash_left,
                    )
                    post_fee_value = budget - fee
                    tracking_error = sum(
                        (
                            targets[code] * price_map[code] / post_fee_value
                            - 1 / len(target_codes)
                        )
                        ** 2
                        for code in target_codes
                    )
                    candidates.append(
                        (
                            (
                                tracking_error,
                                overall_score[0],
                                overall_score[1],
                                overall_score[2],
                                fee,
                                3,
                                tuple(-targets[code] for code in target_codes),
                            ),
                            targets,
                        )
                    )
        expected = min(candidates, key=lambda candidate: candidate[0])[1]

        actual = solve_stock_targets(
            target_codes=target_codes,
            holdings={},
            prices=price_map,
            cash=budget,
            lot=100,
            max_single_weight=0.60,
            ordinary_order_threshold=threshold,
        )

        self.assertEqual(
            expected,
            {"600001": 400, "600002": 400, "600003": 200},
        )
        self.assertEqual(actual.targets, expected)
        self.assertEqual(actual.summary["solver_status"], "OPTIMAL")

    def test_keeps_equal_weight_ahead_of_cash_and_order_costs(self):
        target_codes = [f"{600001 + index:06d}" for index in range(20)]
        price_values = [
            23.94,
            6.00,
            5.51,
            17.71,
            25.91,
            8.66,
            5.35,
            7.56,
            4.74,
            9.01,
            7.97,
            4.86,
            15.55,
            13.66,
            18.14,
            17.56,
            10.82,
            6.18,
            16.58,
            10.94,
        ]
        share_values = [
            600,
            2_200,
            2_400,
            700,
            700,
            1_500,
            2_500,
            1_800,
            2_800,
            1_600,
            1_700,
            2_800,
            900,
            1_000,
            700,
            800,
            0,
            2_200,
            800,
            1_200,
        ]
        price_map = dict(zip(target_codes, price_values))
        held = {
            code: shares
            for code, shares in zip(target_codes, share_values)
            if shares
        }
        held["699999"] = 900
        price_map["699999"] = 15.69
        cash = 5_117.84
        total_value = sum(
            shares * price_map[code] for code, shares in held.items()
        ) + cash

        actual = solve_stock_targets(
            target_codes=target_codes,
            holdings=held,
            prices=price_map,
            cash=cash,
            lot=100,
            max_single_weight=0.10,
            ordinary_order_threshold=max(1_000.0, total_value / 20 * 0.10),
        )

        new_position = target_codes[16]
        existing_position = target_codes[3]
        self.assertEqual(actual.targets[new_position], 1_300)
        self.assertEqual(actual.targets[existing_position], 800)
        self.assertEqual(actual.targets[target_codes[14]], 800)
        self.assertEqual(actual.targets[target_codes[8]], 3_100)
        self.assertEqual(actual.summary["cash_left"], 132.37)
        self.assertEqual(actual.summary["estimated_fees"], 33.47)
        self.assertIn("target_tracking_error", actual.summary["solver_objectives"])

    def test_never_sells_an_ordinary_holding_to_improve_allocation(self):
        target_codes = ["600001", "600002"]
        price_map = {"600001": 16.241, "600002": 39.324}
        held = {"600001": 900, "600002": 0}

        actual = solve_stock_targets(
            target_codes=target_codes,
            holdings=held,
            prices=price_map,
            cash=4_794.73,
            lot=100,
            max_single_weight=1.0,
            ordinary_order_threshold=1_000.0,
        )

        self.assertGreaterEqual(actual.targets["600001"], held["600001"])
        self.assertEqual(actual.targets["600002"], 100)
        self.assertLess(actual.summary["cash_left"], price_map["600001"] * 100 + 5)

    def test_uses_an_affordable_lot_when_it_improves_equal_weight(self):
        target_codes = ["600001", "600002", "600003"]
        price_map = {"600001": 46.149, "600002": 21.190, "600003": 21.023}
        held = {"600001": 900, "600002": 400, "600003": 300}

        actual = solve_stock_targets(
            target_codes=target_codes,
            holdings=held,
            prices=price_map,
            cash=3_328.48,
            lot=100,
            max_single_weight=1.0,
            ordinary_order_threshold=1_000.0,
        )

        self.assertEqual(actual.targets["600002"], 400)
        self.assertEqual(actual.targets["600003"], 400)
        self.assertEqual(
            actual.summary["cash_residual_reason"],
            "insufficient_for_next_feasible_state",
        )
        self.assertLess(actual.summary["cash_left"], price_map["600003"] * 100)

    def test_presolve_result_is_rechecked_against_exact_cash_constraints(self):
        actual = solve_stock_targets(
            target_codes=["600001", "600002", "600003"],
            holdings={"600001": 400, "600002": 0, "600003": 200},
            prices={"600001": 32.383, "600002": 31.480, "600003": 19.881},
            cash=5_866.68,
            lot=100,
            max_single_weight=1.0,
            ordinary_order_threshold=1_000.0,
        )

        self.assertEqual(actual.targets["600003"], 300)
        self.assertEqual(actual.summary["cash_left"], 720.58)
        self.assertEqual(actual.summary["solver_status"], "OPTIMAL")

    def test_official_rounded_fee_never_leaves_negative_cash(self):
        target_codes = [f"{600000 + index:06d}" for index in range(20)]
        held = {code: 100 for code in target_codes}
        held[target_codes[0]] = 201
        price_map = {code: 10.0 for code in target_codes}
        price_map[target_codes[0]] = 10.007
        cash = 2_009.90

        actual = solve_stock_targets(
            target_codes=target_codes,
            holdings=held,
            prices=price_map,
            cash=cash,
            lot=100,
            max_single_weight=0.10,
            ordinary_order_threshold=1_000.0,
        )

        total_value = sum(held[code] * price_map[code] for code in target_codes) + cash
        target_value = sum(
            actual.targets[code] * price_map[code] for code in target_codes
        )
        official_fee = stock_fee_estimate(
            actual.targets,
            held,
            target_codes,
            price_map,
        )
        self.assertGreaterEqual(total_value - target_value - official_fee, 0)
        self.assertGreaterEqual(actual.summary["cash_left"], 0)

    def test_equal_weight_compares_market_value_instead_of_share_count(self):
        actual = solve_stock_targets(
            target_codes=["600001", "600002"],
            holdings={},
            prices={"600001": 2.0, "600002": 1.0},
            cash=610.0,
            lot=100,
            max_single_weight=1.0,
            ordinary_order_threshold=0.0,
        )

        self.assertEqual(actual.targets, {"600001": 100, "600002": 300})

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

    def test_missing_quote_remains_a_structured_sizing_error(self):
        target = rankings()
        price_map = prices(target)
        missing_code = target.iloc[-1]["stock_code"]
        del price_map[missing_code]

        with self.assertRaises(SizingError) as raised:
            size_target_state(target, positions(), 100_000, price_map)

        self.assertEqual(raised.exception.code, "MISSING_QUOTE")
        self.assertEqual(raised.exception.details["codes"], [missing_code])

    def test_required_exit_bypasses_ordinary_order_threshold(self):
        target = rankings()
        held = positions([{"stock_code": "999999", "stock_name": "退出股", "shares": 100}])
        price_map = prices(target, extra={"999999": 5.0})

        sheet, summary = size_target_state(target, held, 40_000, price_map)

        exit_row = sheet.loc[sheet["stock_code"] == "999999"].iloc[0]
        self.assertEqual(exit_row["action"], "SELL")
        self.assertEqual(exit_row["delta_shares"], -100)
        self.assertEqual(exit_row["ideal_target_shares"], 0)
        self.assertEqual(exit_row["executable_target_shares"], 0)
        self.assertEqual(exit_row["residual_shares"], 0)
        self.assertEqual(exit_row["execution_reason"], "mandatory_exit")
        self.assertEqual(summary["ordinary_order_threshold"], 1_000)

    def test_uses_dynamic_threshold_and_never_overspends_cash(self):
        target = rankings()
        sheet, summary = size_target_state(target, positions(), 400_000, prices(target))

        self.assertEqual(summary["ordinary_order_threshold"], 2_000)
        self.assertTrue((sheet["target_shares"] % 100 == 0).all())
        self.assertGreaterEqual(summary["cash_left"], 0)

    def test_keeps_cash_when_another_lot_would_break_equal_weight(self):
        target = rankings()
        sheet, summary = size_target_state(target, positions(), 21_500, prices(target))

        self.assertEqual(summary["cash_left"], 1_400)
        self.assertEqual(summary["cash_residual_reason"], "equal_weight_priority")
        self.assertEqual(sheet["target_shares"].sum(), 2_000)

    def test_considers_multiple_lots_when_one_lot_is_below_the_order_threshold(self):
        target = rankings()
        expensive_code = target.iloc[-2]["stock_code"]
        cheap_code = target.iloc[-1]["stock_code"]
        price_map = prices(target)
        price_map[expensive_code] = 24.0
        price_map[cheap_code] = 6.0
        held = positions(
            [
                {"stock_code": row.stock_code, "stock_name": row.stock_name, "shares": 1_300}
                for row in target.iloc[:-2].itertuples()
            ]
            + [
                {"stock_code": expensive_code, "stock_name": "高价股", "shares": 500},
                {"stock_code": cheap_code, "stock_name": "低价股", "shares": 1_700},
            ]
        )

        sheet, summary = size_target_state(target, held, 6_000, price_map)

        cheap = sheet.loc[sheet["stock_code"] == cheap_code].iloc[0]
        expensive = sheet.loc[sheet["stock_code"] == expensive_code].iloc[0]
        self.assertGreaterEqual(cheap["delta_shares"], 300)
        self.assertLessEqual(expensive["target_shares"], 600)
        self.assertEqual(summary["cash_residual_reason"], "equal_weight_priority")
        self.assertLess(summary["target_tracking_error"], 3e-5)

    def test_reports_proven_optimal_solver_evidence(self):
        target = rankings()

        _, summary = size_target_state(target, positions(), 21_500, prices(target))

        self.assertEqual(summary["allocation_method"], "equal_weight_lexicographic_milp")
        self.assertEqual(summary["solver"], "scipy.optimize.milp/HiGHS")
        self.assertEqual(summary["solver_status"], "OPTIMAL")
        self.assertLessEqual(summary["solver_mip_gap"], 1e-8)
        self.assertEqual(summary["fee_schedule"], "stock_a_share")
        self.assertFalse(summary["allow_target_sells"])
        self.assertIn(
            summary["cash_residual_reason"],
            {
                "insufficient_for_next_feasible_state",
                "fees",
                "at_cap_or_no_feasible_buy",
                "equal_weight_priority",
            },
        )

    def test_negative_cash_without_a_required_sale_is_a_capacity_conflict(self):
        target = rankings()
        held = positions([
            {"stock_code": row.stock_code, "stock_name": row.stock_name, "shares": 1_000}
            for row in target.itertuples()
        ])

        with self.assertRaisesRegex(SizingError, "CAPACITY_CONFLICT"):
            size_target_state(target, held, -9_000, prices(target))

    def test_cash_shortage_never_authorizes_an_ordinary_sale(self):
        target = rankings()
        held = positions(
            [{"stock_code": target.iloc[0]["stock_code"], "stock_name": "股票0", "shares": 2_100}]
            + [
                {"stock_code": row.stock_code, "stock_name": row.stock_name, "shares": 2_000}
                for row in target.iloc[2:].itertuples()
            ]
        )

        sheet, summary = size_target_state(target, held, 9_500, prices(target, price=5.0))

        self.assertGreaterEqual(summary["cash_left"], 0)
        ordinary_holding = sheet.loc[
            sheet["stock_code"] == target.iloc[0]["stock_code"]
        ].iloc[0]
        self.assertGreaterEqual(ordinary_holding["delta_shares"], 0)

    def test_risk_reduction_returns_to_equal_weight_in_round_lots(self):
        target = rankings()
        held = positions(
            [
                {
                    "stock_code": row.stock_code,
                    "stock_name": row.stock_name,
                    "shares": 3_000 if row.Index == 0 else 1_000,
                }
                for row in target.itertuples()
            ]
        )

        sheet, _ = size_target_state(target, held, 0, prices(target))

        reduction = sheet.loc[
            sheet["stock_code"] == target.iloc[0]["stock_code"]
        ].iloc[0]
        self.assertEqual(reduction["target_shares"], 1_100)
        self.assertEqual(reduction["delta_shares"] % 100, 0)
        self.assertEqual(reduction["execution_reason"], "mandatory_risk_reduction")

    def test_risk_reduction_preserves_an_odd_lot_remainder(self):
        target = rankings()
        held = positions(
            [
                {
                    "stock_code": row.stock_code,
                    "stock_name": row.stock_name,
                    "shares": 3_001 if row.Index == 0 else 1_000,
                }
                for row in target.itertuples()
            ]
        )
        price_map = prices(target)
        price_map[target.iloc[0]["stock_code"]] = 10.007

        sheet, _ = size_target_state(target, held, 0, price_map)

        reduction = sheet.loc[
            sheet["stock_code"] == target.iloc[0]["stock_code"]
        ].iloc[0]
        self.assertEqual(reduction["target_shares"], 1_101)
        self.assertEqual(reduction["delta_shares"], -1_900)
        self.assertEqual(abs(reduction["delta_shares"]) % 100, 0)

    def test_ten_percent_cap_uses_the_frozen_pre_fee_strategy_budget(self):
        target = rankings()
        price_map = prices(target, price=9.0)
        expensive_code = target.iloc[0]["stock_code"]
        price_map[expensive_code] = 20.0
        held = positions(
            [
                {
                    "stock_code": row.stock_code,
                    "stock_name": row.stock_name,
                    "shares": 100,
                }
                for row in target.iloc[1:].itertuples()
            ]
        )

        sheet, summary = size_target_state(
            target,
            held,
            2_900.0,
            price_map,
        )

        expensive = sheet.loc[sheet["stock_code"] == expensive_code].iloc[0]
        self.assertEqual(expensive["target_shares"], 100)
        self.assertEqual(summary["risk_cap_basis"], "pre_fee_strategy_budget")
        self.assertEqual(summary["risk_cap_value"], 2_000.0)

    def test_rejects_a_target_set_that_is_not_top_twenty(self):
        target = rankings(3)

        with self.assertRaisesRegex(SizingError, "CAPACITY_CONFLICT"):
            size_target_state(target, positions(), 30_000, prices(target))

    def test_cli_adapter_uses_the_shared_target_state_result(self):
        target = rankings()
        expected_sheet, expected_summary = size_target_state(target, positions(), 400_000, prices(target))

        actual_sheet, actual_summary = size_orders.size_rebalance(target, positions(), 400_000, prices(target))

        pd.testing.assert_frame_equal(actual_sheet, expected_sheet)
        self.assertEqual(actual_summary["ordinary_order_threshold"], expected_summary["ordinary_order_threshold"])


if __name__ == "__main__":
    unittest.main()
