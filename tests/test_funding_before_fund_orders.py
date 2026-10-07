import unittest

from app.account_read_model import build_account_read_model
from app.fund_orders import build_fund_orders, reconcile_fund_return
from app.plan_service import build_fund_transfer
from tests.test_stage_allocation import facts


class TestFundingBeforeFundOrders(unittest.TestCase):
    def account(self):
        account = facts(stock=288133, bond=191827.64, nasdaq=0, technology=0, cash=65863.32)
        account.update(bond_total=192023.21, bond_cash=195.57, bond_available_cash=195.57,
                       changqian_total=112958.51, overseas_total=93526.38,
                       plan_date='2026-09-28', fund_terms=[])
        return account

    def test_public_quote_conditions_do_not_cancel_pingan_cash_allocation(self):
        account = self.account()
        transfer = build_fund_transfer(account, qualified_cb_count=20)
        funds = build_fund_orders(account, transfer, '2026-09-28')
        result = reconcile_fund_return(account, transfer, funds)
        inbound = sum(a['amount'] for a in result['actions'] if a['target'] == 'pingan')
        self.assertGreater(inbound, 0)
        self.assertLessEqual(result['cash']['immediate_outflow'], account['cash_pool'])
        self.assertEqual(funds['orders'], [])
        self.assertGreater(funds['pending_buy_budget'], 0)
        self.assertAlmostEqual(inbound, funds['pending_buy_budget'] + 300, places=2)

    def test_overview_names_pingan_for_both_growth_directions_even_empty(self):
        model = build_account_read_model(self.account())
        rows = {row['id']: row for row in model['allocation']}
        for key in ('nasdaq', 'technology'):
            self.assertEqual(rows[key]['account_ids'], ['pingan'])
            self.assertEqual(rows[key]['account_names'], ['平安账户'])
            self.assertEqual(rows[key]['current_amount'], 0)

    def test_existing_cash_phase_does_not_order_target_reductions(self):
        transfer = build_fund_transfer(self.account(), qualified_cb_count=20)
        self.assertEqual(transfer['sell_budgets'], {})
        self.assertGreater(transfer['deferred_reductions']['changqian'], 0)
        self.assertGreater(transfer['deferred_reductions']['overseas'], 0)
        self.assertGreater(transfer['deferred_reductions']['stock'], 0)
        self.assertFalse(any(a['source'] in ('changqian','overseas') for a in transfer['actions']))
        self.assertEqual(transfer['strategy_cash']['stock'], 0)
        self.assertEqual(transfer['strategy_cash']['cb'], 0)

    def test_migration_keeps_broker_cash_in_strategy_budget(self):
        account = self.account()
        account.update(stock_total=293831.23, stock_cash=16008.23,
                       stock_available_cash=16008.23, bond_total=193779.80,
                       bond_cash=897.62, bond_available_cash=897.62)
        transfer = build_fund_transfer(account, qualified_cb_count=20)
        self.assertEqual(transfer['strategy_cash']['stock'], 15708.23)
        self.assertEqual(transfer['strategy_cash']['cb'], 597.62)
        self.assertFalse(any(a['source'] in ('stock', 'cb') for a in transfer['actions']))
        from app.cash_reserve import reconcile_cash_reserves
        result = reconcile_cash_reserves(account, transfer, {'stock': [], 'cb': []})
        self.assertFalse(any(a['source'] in ('stock', 'cb') for a in result['actions']))
        self.assertEqual(result['transfer_deltas']['stock'], 0)
