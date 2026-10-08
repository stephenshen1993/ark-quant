import unittest
from copy import deepcopy
from app.fund_orders import add_migration_redemption
from tests.test_funding_before_fund_orders import TestFundingBeforeFundOrders
from app.plan_service import build_fund_transfer


class TestMigrationRedemption(unittest.TestCase):
    def test_advisers_share_current_return_need_regardless_of_orders(self):
        account = TestFundingBeforeFundOrders().account()
        account['changqian_pending'] = 50000
        transfer = build_fund_transfer(account, qualified_cb_count=20)
        before = deepcopy(transfer)
        for orders in ([], [dict(delta_shares=100,amount=10000)], [dict(delta_shares=-100,amount=10000)]):
            result = add_migration_redemption(account, transfer, orders)
            self.assertEqual(result, before)
            returns = {a['source']:a['amount'] for a in result['actions'] if a['target']=='cash_pool'}
            self.assertGreater(returns['changqian'], 0)
            self.assertLess(returns['changqian'], 62958.51)
            self.assertGreater(returns['overseas'], 0)
            self.assertLess(returns['overseas'], 93526.38)
            self.assertLessEqual(sum(returns.values()), result['cash']['return_limit'])
            self.assertLessEqual(result['cash']['terminal_estimate'], result['allocation']['targets']['cash_pool'])
            self.assertFalse(any(a.get('conditional') for a in result['actions']))
        self.assertEqual(transfer, before)

    def test_repeated_render_does_not_duplicate_return(self):
        account = TestFundingBeforeFundOrders().account()
        transfer = build_fund_transfer(account, qualified_cb_count=20)
        once = add_migration_redemption(account, transfer, [])
        self.assertEqual(once, add_migration_redemption(account, once, []))
