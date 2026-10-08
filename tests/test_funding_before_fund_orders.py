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
        for key in ('pingan',):
            self.assertEqual(rows[key]['account_ids'], ['pingan'])
            self.assertEqual(rows[key]['account_names'], ['平安账户'])
            self.assertEqual(rows[key]['current_amount'], 0)

    def test_all_overweight_accounts_propose_returns(self):
        transfer = build_fund_transfer(self.account(), qualified_cb_count=20)
        sources = {a['source'] for a in transfer['actions']}
        self.assertTrue({'changqian','overseas','stock'} <= sources)
        self.assertEqual(transfer['deferred_reductions'], {})
        self.assertLess(transfer['strategy_cash']['stock'], 0)

    def test_account_cash_does_not_trigger_an_unrelated_return(self):
        account = facts()
        account.update(stock_cash=16008.23, stock_available_cash=16008.23)
        transfer = build_fund_transfer(account, qualified_cb_count=20)
        self.assertEqual(transfer['strategy_cash']['stock'], 15708.23)
        from app.cash_reserve import reconcile_cash_reserves
        result = reconcile_cash_reserves(account, transfer, {'stock': []})
        self.assertEqual(result['actions'], [])
        self.assertEqual(result['transfer_deltas']['stock'], 0)
