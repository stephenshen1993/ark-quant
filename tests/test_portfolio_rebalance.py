import unittest

from portfolio_rebalance import _constrained_hard_inflows, build_fund_transfer_plan


class TestFundTransferTargets(unittest.TestCase):
    def setUp(self):
        self.account = {
            "stock_total": 45_000,
            "bond_total": 30_000,
            "cash_pool": 10_000,
            "changqian_total": 15_000,
            "overseas_total": 20_000,
        }
        self.context = {
            "temperature": 50,
            "check_type": "a_internal",
            "cash_available": 10_000,
        }

    def test_neutral_targets_conserve_top_level_and_a_internal_amounts(self):
        result = build_fund_transfer_plan(self.account, self.context, qualified_cb_count=20)

        self.assertEqual(result["top_level"]["targets"], {
            "A": 78_000.0,
            "B": 24_000.0,
            "C": 18_000.0,
        })
        self.assertEqual(result["a_internal"]["a_current"], 85_000.0)
        self.assertEqual(result["a_internal"]["a_exec"], 85_000.0)
        self.assertEqual(result["a_internal"]["base_targets"], {
            "stock": 45_000.0,
            "bond": 30_000.0,
            "cash_pool": 10_000.0,
        })
        self.assertEqual(sum(result["a_internal"]["final_targets"].values()), 85_000.0)

    def test_constrained_hard_inflows_do_not_emit_subminimum_fragments(self):
        inflows = _constrained_hard_inflows(
            {"B": 10_000, "C": 1_000},
            b_limit=10_000,
            cash_available=1_500,
        )

        self.assertEqual(inflows, {"B": 1_500.0})

    def test_convertible_bond_candidate_shortage_is_a_capacity_conflict(self):
        result = build_fund_transfer_plan(self.account, self.context, qualified_cb_count=10)

        self.assertEqual(result["a_internal"]["status"], "capacity_conflict")
        self.assertEqual(result["a_internal"]["capacity_conflict"], "INSUFFICIENT_CB_CANDIDATES")
        self.assertEqual(result["a_internal"]["immediate_actions"], [])
        self.assertEqual(result["a_internal"]["final_targets"]["cash_pool"], 10_000.0)

    def test_transfer_layer_does_not_treat_lot_costs_as_a_cash_safety_valve(self):
        result = build_fund_transfer_plan(
            self.account,
            self.context,
            qualified_cb_count=20,
            qualified_cb_lot_costs=[1_200, 1_300, 1_400] + [9_000] * 17,
        )

        self.assertEqual(result["a_internal"]["executable_cb_count"], 20)
        self.assertEqual(result["a_internal"]["final_targets"], {
            "stock": 45_000.0,
            "bond": 30_000.0,
            "cash_pool": 10_000.0,
        })

    def test_missing_same_day_convertible_bond_universe_pauses_a_internal_cash_outflows(self):
        result = build_fund_transfer_plan(self.account, self.context, qualified_cb_count=None)

        self.assertEqual(result["a_internal"]["status"], "paused")
        self.assertEqual(result["a_internal"]["immediate_actions"], [])
        self.assertIn("可转债榜单", result["a_internal"]["pause_reason"])

    def test_a_internal_builds_thresholded_star_actions_and_strategy_deltas(self):
        result = build_fund_transfer_plan(
            {
                "stock_total": 40_000,
                "bond_total": 40_000,
                "cash_pool": 5_000,
                "changqian_total": 15_000,
                "overseas_total": 20_000,
            },
            {
                "temperature": 50,
                "check_type": "a_internal",
                "cash_available": 5_000,
            },
            qualified_cb_count=20,
        )

        internal = result["a_internal"]
        self.assertEqual(internal["status"], "ready")
        self.assertEqual(internal["planned_deltas"], {
            "stock": 5_000.0,
            "bond": -10_000.0,
            "cash_pool": 5_000.0,
        })
        self.assertEqual(
            [
                (
                    a["source"],
                    a["target"],
                    a["amount"],
                    a["immediate"],
                    a["available_on"],
                    a["cash_effect"],
                )
                for a in internal["actions"]
            ],
            [
                ("bond", "cash_pool", 10_000.0, False, "next_trading_day", "deferred_cash_return"),
                ("cash_pool", "stock", 5_000.0, True, "same_day", "immediate_cash_in"),
            ],
        )
        self.assertEqual(internal["deferred_gaps"]["cash_pool"], 0.0)

    def test_pure_fund_transfer_does_not_request_or_read_a_strategy_universe(self):
        result = build_fund_transfer_plan(
            self.account,
            self.context,
            include_a_internal=False,
        )

        self.assertEqual(result["top_level"]["status"], "ready")
        self.assertEqual(result["a_internal"]["status"], "not_requested")
        self.assertIn("不读取账户内榜单", result["a_internal"]["pause_reason"])


class TestTopLevelFundingTriggers(unittest.TestCase):
    def test_monthly_contribution_never_generates_old_holding_sales(self):
        result = build_fund_transfer_plan(
            {
                "stock_total": 70_000,
                "bond_total": 0,
                "cash_pool": 7_500,
                "changqian_total": 15_000,
                "overseas_total": 20_000,
            },
            {
                "temperature": 50,
                "check_type": "monthly_contribution",
                "cash_available": 7_500,
                "new_contribution": 7_500,
                "b_purchase_limit": 0,
            },
            qualified_cb_count=20,
        )

        actions = result["top_level"]["executed_actions"]
        self.assertTrue(actions)
        self.assertTrue(all(action["origin"] == "cash_pool" for action in actions))
        self.assertTrue(all(action["amount"] > 0 for action in actions))
        self.assertFalse(result["top_level"]["old_holding_sales_allowed"])

    def test_quarterly_b_unavailable_cancels_unfunded_outflows(self):
        result = build_fund_transfer_plan(
            {
                "stock_total": 85_000,
                "bond_total": 10_000,
                "cash_pool": 5_000,
                "changqian_total": 15_000,
                "overseas_total": 5_000,
            },
            {
                "temperature": 50,
                "check_type": "quarterly",
                "cash_available": 0,
                "b_purchase_limit": 0,
            },
            qualified_cb_count=20,
        )

        top = result["top_level"]
        self.assertTrue(top["hard_rebalance_triggered"])
        self.assertEqual(top["b_purchase_status"], "unavailable")
        self.assertFalse(any(action["target"] == "B" for action in top["executed_actions"]))
        self.assertEqual(top["executed_actions"], [])
        self.assertEqual(top["outflows"], [])
        self.assertEqual(top["executed_deltas"], {"A": 0.0, "B": 0.0, "C": 0.0})

    def test_real_quarterly_b_unavailable_does_not_liquidate_a_or_c_into_cash(self):
        result = build_fund_transfer_plan(
            {
                "stock_total": 274_650.00,
                "bond_total": 188_529.11,
                "cash_pool": 65_271.26,
                "changqian_total": 117_157.34,
                "overseas_total": 95_450.33,
            },
            {
                "temperature": 50,
                "check_type": "quarterly",
                "cash_available": 65_271.26,
                "b_purchase_limit": 0,
            },
            qualified_cb_count=20,
        )

        top = result["top_level"]
        self.assertTrue(top["hard_rebalance_triggered"])
        self.assertEqual(top["b_purchase_status"], "unavailable")
        self.assertEqual(top["executable_inflow"], 0.0)
        self.assertEqual(top["executed_actions"], [])
        self.assertEqual(top["outflows"], [])
        self.assertEqual(top["executed_deltas"], {"A": 0.0, "B": 0.0, "C": 0.0})

    def test_quarterly_b_unavailable_scales_outflows_to_other_executable_inflows(self):
        result = build_fund_transfer_plan(
            {
                "stock_total": 90_000,
                "bond_total": 10_000,
                "cash_pool": 10_000,
                "changqian_total": 5_000,
                "overseas_total": 5_000,
            },
            {
                "temperature": 50,
                "check_type": "quarterly",
                "cash_available": 10_000,
                "b_purchase_limit": 0,
            },
            qualified_cb_count=20,
        )

        top = result["top_level"]
        self.assertTrue(top["hard_rebalance_triggered"])
        self.assertEqual(top["b_purchase_status"], "unavailable")
        self.assertFalse(any(action["target"] == "B" for action in top["executed_actions"]))
        self.assertEqual(
            [(action["source"], action["target"], action["amount"]) for action in top["executed_actions"]],
            [("cash_pool", "C", 10_000.0)],
        )
        self.assertEqual(
            [(action["source"], action["target"], action["amount"]) for action in top["outflows"]],
            [("A", "cash_pool", 10_000.0)],
        )
        self.assertEqual(top["executed_deltas"], {"A": -10_000.0, "B": 0.0, "C": 10_000.0})

    def test_a_internal_execution_budget_only_deducts_approved_a_outflow(self):
        result = build_fund_transfer_plan(
            {
                "stock_total": 85_000,
                "bond_total": 10_000,
                "cash_pool": 5_000,
                "changqian_total": 15_000,
                "overseas_total": 5_000,
            },
            {
                "temperature": 50,
                "check_type": "quarterly",
                "cash_available": 5_000,
                "b_purchase_limit": 0,
            },
            qualified_cb_count=20,
        )

        approved_a_outflow = sum(
            action["amount"]
            for action in result["top_level"]["outflows"]
            if action["source"] == "A"
        )
        a_internal = result["a_internal"]
        self.assertEqual(a_internal["q_out"], approved_a_outflow)
        self.assertEqual(a_internal["a_exec"], a_internal["a_current"] - approved_a_outflow)

    def test_immediate_actions_never_spend_more_than_real_cash(self):
        result = build_fund_transfer_plan(
            {
                "stock_total": 70_000,
                "bond_total": 0,
                "cash_pool": 2_000,
                "changqian_total": 15_000,
                "overseas_total": 20_000,
            },
            {
                "temperature": 50,
                "check_type": "monthly_contribution",
                "cash_available": 2_000,
                "new_contribution": 9_000,
                "b_purchase_limit": 0,
            },
            qualified_cb_count=20,
        )

        self.assertLessEqual(result["cash"]["immediate_outflow"], 2_000)
        self.assertEqual(
            result["cash"]["remaining"],
            2_000 - result["cash"]["immediate_outflow"],
        )

    def test_quarterly_cross_account_outflows_scale_to_actual_funds_account_cash(self):
        result = build_fund_transfer_plan(
            {
                "stock_total": 98_000,
                "bond_total": 0,
                "cash_pool": 2_000,
                "changqian_total": 0,
                "overseas_total": 0,
            },
            {
                "temperature": 50,
                "check_type": "quarterly",
                "cash_available": 2_000,
                "b_purchase_limit": 100_000,
            },
            qualified_cb_count=20,
        )

        top = result["top_level"]
        spent = sum(action["amount"] for action in top["executed_actions"])
        self.assertTrue(top["hard_rebalance_triggered"])
        self.assertLessEqual(spent, 2_000)
        self.assertEqual(top["execution_budget"], 2_000.0)
        self.assertGreater(top["unfunded_ideal_inflow"], 0)
        ideal = {action["target"]: action["amount"] for action in top["ideal_actions"]}
        executable = {action["target"]: action["amount"] for action in top["executed_actions"]}
        self.assertTrue(all(amount >= 1_000 for amount in executable.values()))
        self.assertLessEqual(executable["B"], ideal["B"])
        self.assertEqual(result["cash"]["remaining"], 2_000 - result["cash"]["planned_outflow"])
