import unittest
from app.cash_reserve import reconcile_cash_reserves
from app.fund_orders import add_migration_redemption
from tests.test_stage_allocation import facts
from stage_allocation import build_stage_plan


class AccountRebalanceTests(unittest.TestCase):
    def test_exact_accounts_do_not_transfer_for_cash_reserves(self):
        p = build_stage_plan(facts())
        self.assertEqual(p['actions'], [])
        self.assertEqual(p['cash']['remaining'], 100000)

    def test_account_cash_is_not_cash_account_configuration(self):
        a = facts()
        a.update(stock_cash=10000, stock_available_cash=10000)
        p = build_stage_plan(a)
        self.assertEqual(p['allocation']['current']['stock'], 350000)
        self.assertEqual(p['allocation']['current']['cash_pool'], 100000)
        self.assertEqual(p['actions'], [])

    def test_insufficient_cash_is_shared_across_accounts_not_funds(self):
        a = facts(stock=500000, bond=200000, nasdaq=180000, technology=90000, cash=30000)
        p = build_stage_plan(a)
        self.assertEqual(p['account_allocations'], {'bond': 18750, 'pingan': 11250})
        self.assertEqual(p['cash']['remaining'], 0)
        self.assertEqual(p['account_reductions']['stock'], 150000)

    def test_pingan_internal_gap_does_not_request_external_cash(self):
        p = build_stage_plan(facts(nasdaq=250000, technology=50000))
        self.assertFalse(p['actions'])
        self.assertGreater(p['sell_budgets']['nasdaq'], 0)
        self.assertEqual(p['buy_budgets']['technology'], 0)

    def test_advisers_return_all_remaining_without_matching_purchase(self):
        a = facts()
        a.update(changqian_total=100000, changqian_pending=40000, overseas_total=50000)
        p = build_stage_plan(a)
        self.assertEqual(p['account_reductions']['changqian'], 60000)
        self.assertEqual(p['account_reductions']['overseas'], 50000)
        self.assertEqual(add_migration_redemption(a, p, []), p)

    def test_pending_incoming_prevents_duplicate_and_is_not_spendable(self):
        a = facts(stock=340000, cash=110000)
        a['pending_transfers'] = [dict(source='cash', target='stock', amount=10000, debited=False)]
        p = build_stage_plan(a)
        self.assertEqual(p['actions'], [])
        self.assertEqual(p['cash']['available'], 100000)
        self.assertEqual(p['strategy_cash']['stock'], -300)

    def test_debited_pending_counted_once(self):
        a = facts(stock=340000, cash=110000)  # 资金含已扣账在途1万，银行实存10万。
        a['pending_transfers'] = [dict(source='cash', target='stock', amount=10000, debited=True)]
        p = build_stage_plan(a)
        self.assertEqual(p['allocation']['total'], 1000000)
        self.assertEqual(p['allocation']['current']['pending'], 10000)
        self.assertEqual(p['cash']['available'], 100000)
        self.assertEqual(p['actions'], [])

    def test_unavailable_cash_does_not_enter_budget(self):
        a = facts(stock=400000, bond=230000, nasdaq=180000, technology=90000, cash=100000)
        a['cash_unavailable'] = 98000
        p = build_stage_plan(a)
        self.assertLessEqual(p['cash']['immediate_outflow'], 2000)
        self.assertEqual(p['account_allocations'], {'bond': 0, 'pingan': 1200})

    def test_rounding_residue_does_not_create_return(self):
        a = facts()
        a.update(stock_cash=10000, stock_available_cash=10000)
        p = build_stage_plan(a)
        out = reconcile_cash_reserves(a, p, {'stock': []})
        self.assertEqual(out['actions'], [])

    def test_transfer_orders_do_not_exceed_realized_return_capacity(self):
        a = facts(stock=400000, bond=200000)
        p = build_stage_plan(a)
        out = reconcile_cash_reserves(a, p, {'stock': [dict(delta_shares=-1, amount=40000)]})
        action = next(x for x in out['actions'] if x['source']=='stock')
        self.assertEqual(action['amount'], 39700)
        self.assertEqual(action['remaining_amount'], 10300)
        row = next(x for x in out['allocation']['rows'] if x['id'] == 'stock')
        self.assertEqual(row['remaining_gap'], -10300)
        self.assertAlmostEqual(sum(out['allocation']['planned_deltas'].values()), 0)
        self.assertEqual(reconcile_cash_reserves(a, out, {'stock': [dict(delta_shares=-1, amount=40000)]}), out)

    def test_pending_returns_are_forecast_once_but_never_current_budget(self):
        a = facts(stock=360000, cash=90000)
        a.update(stock_cash=10000, stock_available_cash=10000)
        a['pending_transfers'] = [dict(source='stock', target='cash', amount=10000, debited=False)]
        p = reconcile_cash_reserves(a, build_stage_plan(a), {'stock': []})
        self.assertFalse(p['actions'])
        self.assertEqual(p['cash']['available'], 90000)
        self.assertEqual(p['cash']['expected_returns'], 10000)
        self.assertEqual(p['cash']['terminal_estimate'], 100000)

    def test_minimum_boundary_is_inclusive(self):
        for gap, expected in ((999.99, 0), (1000, 1000)):
            with self.subTest(gap=gap):
                p = build_stage_plan(facts(stock=350000-gap, cash=100000+gap))
                self.assertEqual(p['cash']['immediate_outflow'], expected)
