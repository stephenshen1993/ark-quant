import unittest
from copy import deepcopy

from app.fund_orders import add_migration_redemption
from tests import test_funding_before_fund_orders as fixtures
from app.plan_service import build_fund_transfer


class TestMigrationRedemption(unittest.TestCase):
    def setUp(self):
        self.account = fixtures.TestFundingBeforeFundOrders().account()
        self.transfer = build_fund_transfer(self.account, qualified_cb_count=20)

    def apply(self, buy=64504.30, sell=0):
        orders = [dict(delta_shares=100, amount=buy)] if buy else []
        if sell:
            orders.append(dict(delta_shares=-100, amount=sell))
        return add_migration_redemption(self.account, self.transfer, orders)

    def test_domestic_first_matched_to_net_purchase_not_full_holding(self):
        before = deepcopy(self.transfer)
        result = self.apply()
        action = result['actions'][-1]
        self.assertEqual(action['source'], 'changqian')
        self.assertEqual(action['amount'], 64504.30)
        self.assertFalse(action['immediate'])
        self.assertTrue(action['conditional'])
        self.assertEqual(result['cash'], self.transfer['cash'])
        self.assertEqual(result['buy_budgets'], self.transfer['buy_budgets'])
        self.assertEqual(result['deferred_reductions']['changqian'], 48454.21)
        self.assertEqual(before, self.transfer)

    def test_partial_or_missing_purchase_reduces_or_removes_redemption(self):
        self.assertEqual(self.apply(10000)['migration_redemption']['reference_amount'], 10000)
        self.assertFalse(any(a.get('conditional') for a in self.apply(0)['actions']))
        self.assertEqual(self.apply(10000, 10000)['migration_redemption']['reference_amount'], 0)

    def test_pending_is_not_requested_again(self):
        self.account['changqian_pending'] = 50000
        self.assertEqual(self.apply()['migration_redemption']['reference_amount'], 14504.30)

    def test_domestic_remainder_then_overseas_same_batch(self):
        self.account['changqian_total'] = 5000
        result = self.apply()
        self.assertEqual(result['migration_redemption']['reference_amount'], 64504.30)
        self.assertEqual([(a['source'], a['amount']) for a in result['actions'] if a.get('conditional')], [('changqian', 5000), ('overseas', 59504.30)])

    def test_overseas_only_after_domestic_exits(self):
        self.account['changqian_total'] = 0
        self.assertEqual(self.apply()['actions'][-1]['source'], 'overseas')

    def test_repeated_render_does_not_duplicate_action_or_reduce_target_twice(self):
        first = self.apply()
        second = add_migration_redemption(self.account, first, [dict(delta_shares=100, amount=64504.30)])
        self.assertEqual(first, second)

    def test_broker_returns_reduce_redemption_without_becoming_buying_power(self):
        self.transfer['actions'].append(dict(source='stock', target='cash_pool',
            amount=15000, immediate=False))
        result = self.apply()
        self.assertEqual(result['migration_redemption']['reference_amount'], 49504.30)
        self.assertEqual(result['cash'], self.transfer['cash'])
