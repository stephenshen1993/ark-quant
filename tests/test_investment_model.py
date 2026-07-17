import unittest

from investment_model import domestic_target_current_amounts, public_investment_model
from rebalance import build_transfer_plan


class TestInvestmentModel(unittest.TestCase):
    def test_current_amounts_follow_target_definitions(self):
        amounts = domestic_target_current_amounts({
            "stock_total": 100,
            "bond_total": 200,
            "changqian_total": 300,
            "cash_pool": 400,
            "overseas_total": 500,
        })
        self.assertEqual(amounts, {
            "stock": 100.0,
            "bond": 200.0,
            "changqian": 300.0,
            "cash_pool": 400.0,
        })

    def test_public_model_keeps_targets_as_strategy_amount_rules(self):
        model = public_investment_model()
        self.assertEqual(model["concepts"], ("资产类别", "策略", "账户"))
        targets = {item["id"]: item for item in model["domestic_rebalance_targets"]}
        self.assertEqual(targets["bond"]["account_ids"], ["cb"])
        self.assertEqual(targets["bond"]["strategy_id"], "multifactor_convertible_bond")

    def test_transfer_plan_uses_target_definitions_for_routes(self):
        steps = build_transfer_plan({
            "stock": 2000,
            "bond": -3000,
            "changqian": 0,
            "cash_pool": 1000,
        })
        self.assertEqual(len(steps), 2)
        self.assertIn("华泰多因子可转债 →[银证转出]→ 浦发现金账户", steps[0])
        self.assertIn("浦发现金账户 →[银证转入]→ 广发小市值股票", steps[1])
