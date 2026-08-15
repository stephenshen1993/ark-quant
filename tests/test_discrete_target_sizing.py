import unittest
from itertools import product

from strategies.discrete_target_sizing import (
    CB_FEE_SCHEDULE,
    cb_fee_estimate,
    score_targets,
    size_discrete_targets,
)
from strategies.equal_weight_milp import solve_equal_weight_targets


def codes():
    return [f"{110000 + index:06d}" for index in range(20)]


class DiscreteTargetSizingTests(unittest.TestCase):
    def test_residual_cash_cannot_concentrate_on_one_expensive_target(self):
        """Regression: frozen 2026-08-10 CB input once made 127056 120 lots."""
        target_codes = [
            "127061", "110093", "113056", "118031", "127089", "113054", "113067", "111015", "127056", "110086",
            "113062", "113052", "113070", "110100", "127102", "118034", "123264", "113053", "127045", "111014",
        ]
        holdings = {
            "110086": 70, "110087": 80, "110093": 80, "110100": 80, "111014": 0, "111015": 0,
            "111018": 70, "113052": 80, "113053": 70, "113054": 80, "113056": 80, "113062": 0,
            "113067": 0, "113070": 80, "113682": 80, "118010": 70, "118031": 70, "118034": 70,
            "123264": 70, "127045": 70, "127056": 0, "127061": 90, "127062": 70, "127089": 70,
            "127102": 0, "127108": 70,
        }
        prices = {
            "110086": 124.301, "110087": 126.278, "110093": 125.713, "110100": 125.666,
            "111014": 125.851, "111015": 129.690, "111018": 128.617, "113052": 116.506,
            "113053": 122.674, "113054": 125.605, "113056": 128.188, "113062": 129.288,
            "113067": 129.780, "113070": 129.433, "113682": 130.113, "118010": 126.216,
            "118031": 124.638, "118034": 117.210, "123264": 126.913, "127045": 123.600,
            "127056": 129.961, "127061": 109.000, "127062": 126.746, "127089": 124.400,
            "127102": 123.556, "127108": 127.348,
        }

        targets, summary = size_discrete_targets(
            target_codes=target_codes,
            holdings=holdings,
            prices=prices,
            cash=4_486.75,
            lot=10,
            fee_schedule=CB_FEE_SCHEDULE,
        )

        target_values = [targets[code] * prices[code] for code in target_codes]
        self.assertLessEqual(targets["127056"], 90)
        self.assertLess(max(target_values) - min(target_values), 1_500)
        self.assertLess(summary["max_overall_deviation"], 0.01)

    def test_keeps_equal_weight_ahead_of_residual_cash(self):
        target_codes = codes()
        prices = {code: 100.0 for code in target_codes}

        targets, summary = size_discrete_targets(
            target_codes=target_codes,
            holdings={},
            prices=prices,
            cash=21_500,
            lot=10,
        )

        self.assertEqual(sum(targets.values()), 200)
        self.assertEqual(summary["cash_left"], 1_490.0)
        self.assertEqual(len(set(targets.values())), 1)

    def test_equal_target_values_are_not_sacrificed_only_to_reduce_cash(self):
        target_codes = codes()
        prices = {code: 130.0 for code in target_codes}
        holdings = {code: 70 for code in target_codes}
        total_value = sum(holdings[code] * prices[code] for code in target_codes) + 12_000
        baseline = score_targets(
            holdings,
            target_codes=target_codes,
            prices=prices,
            cash_left=12_000,
            total_value=total_value,
        )

        targets, summary = size_discrete_targets(
            target_codes=target_codes,
            holdings=holdings,
            prices=prices,
            cash=12_000,
            lot=10,
        )

        self.assertEqual(targets, holdings)
        self.assertEqual(summary["cash_left"], 12_000.0)
        self.assertGreater(summary["max_overall_deviation"], baseline[0] - 1e-12)

    def test_estimated_fee_is_a_cash_hard_constraint(self):
        target_codes = codes()
        prices = {code: 100.0 for code in target_codes}

        targets, summary = size_discrete_targets(
            target_codes=target_codes,
            holdings={},
            prices=prices,
            cash=20_100,
            lot=10,
            fee_schedule=CB_FEE_SCHEDULE,
        )

        self.assertEqual(sum(targets.values()), 200)
        self.assertEqual(summary["estimated_fees"], 10.0)
        self.assertEqual(summary["cash_left"], 90.0)

    def test_cb_uses_the_shared_proven_optimal_solver(self):
        target_codes = ["110001", "110002", "110003"]
        prices = {"110001": 100.0, "110002": 115.0, "110003": 83.0}
        holdings = {"110001": 20, "110002": 10, "110003": 30}
        cash = 1_500.0
        total_value = sum(
            holdings[code] * prices[code]
            for code in target_codes
        ) + cash
        candidates = []
        ranges = [
            range(10, int(total_value / prices[code] // 10) * 10 + 1, 10)
            for code in target_codes
        ]
        for quantities in product(*ranges):
            targets = dict(zip(target_codes, quantities))
            fee = cb_fee_estimate(targets, holdings, target_codes, prices)
            cash_left = total_value - sum(
                targets[code] * prices[code]
                for code in target_codes
            ) - fee
            if cash_left < 0:
                continue
            tracking_error = sum(
                (
                    targets[code] * prices[code] / total_value
                    - 1 / len(target_codes)
                )
                ** 2
                for code in target_codes
            )
            overall = score_targets(
                targets,
                target_codes=target_codes,
                prices=prices,
                cash_left=cash_left,
                total_value=total_value,
            )
            order_count = sum(
                targets[code] != holdings.get(code, 0)
                for code in target_codes
            )
            candidates.append(
                (
                    (
                        tracking_error,
                        overall[0],
                        overall[1],
                        cash_left,
                        fee,
                        order_count,
                        tuple(-targets[code] for code in target_codes),
                    ),
                    targets,
                )
            )

        actual = solve_equal_weight_targets(
            target_codes=target_codes,
            holdings=holdings,
            prices=prices,
            cash=cash,
            lot=10,
            max_single_weight=1.0,
            allow_target_sells=True,
            fee_schedule=CB_FEE_SCHEDULE,
        )

        self.assertEqual(actual.targets, min(candidates, key=lambda item: item[0])[1])
        self.assertEqual(actual.summary["solver_status"], "OPTIMAL")
        self.assertEqual(
            actual.summary["allocation_method"],
            "equal_weight_lexicographic_milp",
        )
        self.assertEqual(actual.summary["fee_schedule"], "convertible_bond")


if __name__ == "__main__":
    unittest.main()
