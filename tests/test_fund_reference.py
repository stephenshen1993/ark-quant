import unittest
from unittest.mock import Mock, patch

from app.fund_reference import fetch_reference_quotes, with_reference_terms


class TestReferenceQuotes(unittest.TestCase):
    def quote(self, stamp, last='4.7', previous='4.726'):
        fields = [''] * 31
        fields[2], fields[3], fields[4], fields[30] = '161130', last, previous, stamp
        return 'v_sz161130="' + '~'.join(fields) + '";'

    @patch('app.fund_reference.requests.get')
    def test_execution_day_uses_previous_close_and_rejects_stale_dates(self, get):
        get.return_value = Mock(text=self.quote('20260929093100'))
        result = fetch_reference_quotes(['161130'], '2026-09-28', '2026-09-29')
        self.assertEqual(result['161130']['price'], 4.726)
        get.return_value.text = self.quote('20260925150000')
        self.assertEqual(fetch_reference_quotes(['161130'], '2026-09-28', '2026-09-29'), {})

    @patch('app.fund_reference.requests.get')
    def test_plan_day_intraday_price_is_not_a_close(self, get):
        get.return_value = Mock(text=self.quote('20260928143000'))
        self.assertEqual(fetch_reference_quotes(['161130'], '2026-09-28', '2026-09-29'), {})
        get.return_value.text = self.quote('20260928150000')
        self.assertEqual(fetch_reference_quotes(['161130'], '2026-09-28', '2026-09-29')['161130']['price'], 4.7)

    @patch('app.fund_reference.fetch_reference_quotes')
    def test_reference_never_claims_cost_approval_or_mutates_account(self, fetch):
        fetch.return_value = {'161130':dict(price=4.726,price_date='2026-09-28')}
        account = {'fund_terms':[]}
        result = with_reference_terms(account, '2026-09-28', '2026-09-29')
        self.assertEqual(account['fund_terms'], [])
        term = result['fund_terms'][0]
        self.assertTrue(term['reference_only'])
        self.assertFalse(term['cost_reviewed'])
        self.assertEqual(term['lot_size'], 100)

    def test_reference_orders_have_security_names_and_keep_budget_quantities(self):
        from app.fund_orders import build_fund_orders
        account = {'fund_terms': [dict(direction='nasdaq', code='161130',
            limit_price=4.796, lot_size=100, price_date='2026-09-30', reference_only=True)],
            'fund_positions': [dict(code='161130', quantity=8600)]}
        funds = build_fund_orders(account, {'buy_budgets': {'nasdaq': 46041.60},
            'sell_budgets': {}, 'actions': []}, '2026-09-30')
        order = funds['orders'][0]
        self.assertEqual(order['name'], '纳斯达克100LOF')
        self.assertEqual((order['current_quantity'], order['target_quantity']), (8600, 18200))
        self.assertEqual(order['price_date'], '2026-09-30')
        self.assertIn('未核验', funds['review_required'])
