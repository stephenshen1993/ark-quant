import unittest
from copy import deepcopy

from app.cash_reserve import reconcile_cash_reserves
from app.fund_orders import blocked_directions, build_fund_orders, normalize_terms
from portfolio_rebalance import PlanValidationError
from stage_allocation import build_stage_plan
from tests.test_stage_allocation import facts, terms


class TestCashReserve(unittest.TestCase):
    def reconcile(self, balance, orders=(), bank=10000):
        account = facts(cash=bank)
        account.update(stock_available_cash=balance)
        transfer = dict(actions=[], transfer_deltas={}, cash_reserves={'stock': 300},
                        cash={'available': bank})
        return reconcile_cash_reserves(account, transfer, {'stock': list(orders)})

    def test_in_band_and_boundaries_do_not_add_transfers(self):
        for balance in (300, 500, 1000):
            self.assertEqual(self.reconcile(balance)['actions'], [])

    def test_lower_and_upper_adjust_cash_only(self):
        low = self.reconcile(195.57)['actions']
        self.assertEqual([(a['source'], a['amount']) for a in low], [('cash_pool', 104.43)])
        high = self.reconcile(1149.42)['actions']
        self.assertEqual([(a['target'], a['amount']) for a in high], [('cash_pool', 149.42)])

    def test_lot_residue_returns_without_an_extra_security_order(self):
        orders = [dict(delta_shares=-80, amount=8000), dict(delta_shares=50, amount=6500)]
        original = deepcopy(orders)
        result = self.reconcile(300, orders)
        self.assertEqual(orders, original)
        self.assertEqual(result['actions'][0]['amount'], 800)
        self.assertEqual(result['cash']['immediate_outflow'], 0)

    def test_reduce_return_before_requesting_new_cash(self):
        transfer = dict(actions=[dict(source='stock', target='cash_pool', amount=5000)],
                        transfer_deltas={}, cash_reserves={'stock': 300}, cash={'available': 0})
        account = {'stock_available_cash': 0}
        result = reconcile_cash_reserves(account, transfer, {'stock': [dict(delta_shares=-10, amount=5100)]})
        self.assertEqual(result['actions'][0]['amount'], 4800)
        self.assertEqual(result['cash']['immediate_outflow'], 0)

    def test_missing_cash_cannot_be_replaced_by_other_account_pending_return(self):
        with self.assertRaises(PlanValidationError):
            self.reconcile(0, bank=0)

    def test_fund_fee_fields_are_optional_but_price_review_is_required(self):
        term = terms()
        term.pop('minimum_fee')
        term.pop('commission_rate')
        account = facts(nasdaq=0, cash=300000)
        account.update(fund_terms=normalize_terms([term]), fund_positions=[])
        blocked = blocked_directions(account, '2026-09-25')
        self.assertNotIn('nasdaq', blocked)
        transfer = build_stage_plan(account, blocked=blocked)
        funds = build_fund_orders(account, transfer, '2026-09-25')
        self.assertEqual(funds['summary']['estimated_fees'], 0)
        result = reconcile_cash_reserves(account, transfer, {'pingan': funds['orders']})
        incoming = sum(a['amount'] for a in result['actions'] if a['target'] == 'pingan')
        outgoing = sum(a['amount'] for a in result['actions'] if a['source'] == 'pingan')
        ending = incoming - outgoing - funds['summary']['buy_cost']
        self.assertGreaterEqual(ending, 300)
        self.assertLessEqual(ending, 1000)
        account['fund_terms'][0]['cost_reviewed'] = False
        self.assertIn('nasdaq', blocked_directions(account, '2026-09-25'))

    def test_reserves_are_inside_total_cash_not_added_to_assets(self):
        plan = build_stage_plan(facts())
        self.assertEqual(plan['allocation']['total'], 1000000)
        self.assertEqual(plan['allocation']['targets']['cash_pool'], 100000)
        self.assertEqual(sum(plan['cash_reserves'].values()), 900)
        self.assertEqual(plan['cash']['remaining'], 99100)
