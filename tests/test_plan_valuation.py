import unittest
from copy import deepcopy

from app.plan_valuation import revalue_plan_account
from portfolio_rebalance import PlanValidationError
from stage_allocation import allocation_amounts
from tests.test_stage_allocation import facts


class PlanValuationTests(unittest.TestCase):
    def setUp(self):
        self.account = facts()
        self.account.update(
            bond_cash=100, bond_available_cash=100,
            pingan_cash=50, pingan_available_cash=50,
            account_positions={
                'stock': [dict(code='600001', quantity=200)],
                'cb': [dict(code='110001', quantity=10)],
                'pingan': [dict(code='161130', quantity=100)],
            },
            fund_terms=[dict(code='161130', direction='nasdaq', limit_price=99)],
        )
        self.prices = dict(data_date='2026-10-23', stock={'600001': 10},
                           cb={'110001': 120}, funds={'161130': 5})

    def test_uses_only_frozen_prices_and_preserves_raw_facts(self):
        before = deepcopy(self.account)
        result = revalue_plan_account(self.account, self.prices, '2026-10-23')
        self.assertEqual(self.account, before)
        self.assertEqual(result['stock_total'], 2000)
        self.assertEqual(result['bond_total'], 1300)
        self.assertEqual(result['pingan_total'], 550)
        self.assertEqual(result['nasdaq_total'], 500)
        self.assertEqual(result['technology_total'], 0)
        self.assertEqual(result['total_assets'], 103850)
        self.account.update(stock_total=999999, bond_total=1, pingan_total=999)
        self.assertEqual(revalue_plan_account(self.account, self.prices, '2026-10-23'), result)

    def test_debited_pending_is_counted_once_and_not_reinvested(self):
        self.account['pending_transfers'] = [
            dict(source='stock', target='cash', amount=150, debited=True)]
        result = revalue_plan_account(self.account, self.prices, '2026-10-23')
        self.assertEqual(result['stock_total'], 2150)
        allocation = allocation_amounts(result)
        self.assertEqual(allocation['stock'], 2000)
        self.assertEqual(allocation['pending'], 150)
        self.assertEqual(sum(allocation.values()), 104000)

    def test_frozen_cash_is_in_account_total_but_not_available_cash(self):
        self.account.update(stock_cash=300, stock_frozen_cash=300)
        result = revalue_plan_account(self.account, self.prices, '2026-10-23')
        self.assertEqual(result['stock_total'], 2300)
        self.assertEqual(result['stock_available_cash'], 0)

    def test_missing_prices_fail_even_with_cached_values_or_an_explicit_limit(self):
        for group, code in [('stock', '600001'), ('cb', '110001'), ('funds', '161130')]:
            with self.subTest(group=group):
                prices = deepcopy(self.prices)
                del prices[group][code]
                with self.assertRaises(PlanValidationError) as caught:
                    revalue_plan_account(self.account, prices, '2026-10-23')
                self.assertEqual(caught.exception.code, 'PLAN_PRICE_SNAPSHOT_INCOMPLETE')

    def test_rejects_wrong_reference_date(self):
        with self.assertRaises(PlanValidationError) as caught:
            revalue_plan_account(self.account, self.prices, '2026-10-22')
        self.assertEqual(caught.exception.code, 'PLAN_PRICE_DATE_MISMATCH')

    def test_invalid_price_never_falls_back_to_cached_account_total(self):
        for price in (None, 0, -1, float('nan'), float('inf'), 'invalid'):
            with self.subTest(price=price):
                self.prices['stock']['600001'] = price
                with self.assertRaises(PlanValidationError):
                    revalue_plan_account(self.account, self.prices, '2026-10-23')
