import unittest

from strategies.discrete_target_sizing import cb_fee_estimate, score_targets, size_discrete_targets


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
            fee_estimator=lambda candidate: cb_fee_estimate(candidate, holdings, target_codes, prices),
        )

        target_values = [targets[code] * prices[code] for code in target_codes]
        self.assertLessEqual(targets["127056"], 90)
        self.assertLess(max(target_values) - min(target_values), 1_500)
        self.assertLess(summary["max_overall_deviation"], 0.01)

    def test_uses_residual_cash_when_another_lot_improves_the_whole_plan(self):
        target_codes = codes()
        prices = {code: 100.0 for code in target_codes}

        targets, summary = size_discrete_targets(
            target_codes=target_codes,
            holdings={},
            prices=prices,
            cash=21_500,
            lot=10,
        )

        self.assertEqual(sum(targets.values()), 210)
        self.assertLess(summary["cash_left"], 1_000)
        self.assertLess(summary["max_overall_deviation"], 0.05)

    def test_expensive_lot_is_judged_against_cash_as_a_virtual_position(self):
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

        self.assertGreater(sum(targets.values()), sum(holdings.values()))
        self.assertLess(summary["cash_left"], 1_300)
        self.assertLess(summary["max_overall_deviation"], baseline[0])

    def test_estimated_fee_is_a_cash_hard_constraint(self):
        target_codes = codes()
        prices = {code: 100.0 for code in target_codes}

        targets, summary = size_discrete_targets(
            target_codes=target_codes,
            holdings={},
            prices=prices,
            cash=20_100,
            lot=10,
            fee_estimator=lambda candidate: cb_fee_estimate(candidate, {}, target_codes, prices),
        )

        self.assertEqual(sum(targets.values()), 200)
        self.assertEqual(summary["estimated_fees"], 10.0)
        self.assertEqual(summary["cash_left"], 90.0)


if __name__ == "__main__":
    unittest.main()
