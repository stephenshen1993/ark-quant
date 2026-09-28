import unittest

from app.execution_guard import evaluate


class TestExecutionGuard(unittest.TestCase):
    def test_fund_buy_checks_limit_fees_and_shared_account_cash(self):
        orders = [dict(action='BUY', code=code, delta_shares=100, price=10,
                       commission_rate=.0001, minimum_fee=5) for code in ('161130', '501312')]
        plan = {'funds': {'orders': orders}}
        quotes = {'pingan': {code: {'price': 10} for code in ('161130', '501312')}}
        result = evaluate(plan, {'quotes': quotes, 'available_cash': {'pingan': 2000}})
        self.assertEqual(result['decisions'][0]['cash_cost'], 1005)
        self.assertEqual(result['decisions'][1]['reason'], 'INSUFFICIENT_AVAILABLE_CASH')
        quotes['pingan']['161130']['price'] = 10.01
        result = evaluate(plan, {'quotes': quotes, 'available_cash': {'pingan': 10000}})
        self.assertEqual(result['decisions'][0]['reason'], 'FUND_LIMIT_PRICE')

    def test_fund_unfilled_sell_recommendation_cannot_fund_a_buy(self):
        plan = {'funds': {'orders': [
            dict(action=action, code=code, delta_shares=shares, price=10,
                 commission_rate=.0001, minimum_fee=5)
            for action, code, shares in [('TRIM', '161130', -200), ('BUY', '501312', 100)]
        ]}}
        quotes = {'pingan': {code: {'price': 10} for code in ('161130', '501312')}}
        result = evaluate(plan, {'quotes': quotes, 'available_cash': {'pingan': 0}})
        self.assertEqual(result['decisions'][0]['decision'], 'execute')
        self.assertEqual(result['decisions'][1]['reason'], 'INSUFFICIENT_AVAILABLE_CASH')

    def test_skips_only_affected_orders_without_replanning(self):
        plan = {
            "stock": {"orders": [
                {"action": "BUY", "stock_code": "600001", "delta_shares": 100},
                {"action": "BUY", "stock_code": "600002", "delta_shares": 100},
            ]},
            "cb": {"orders": []},
        }
        original = {**plan, "stock": {"orders": [*plan["stock"]["orders"]]}}

        result = evaluate(plan, {
            "available_cash": {"stock": 2_000},
            "quotes": {"stock": {
                "600001": {"price": 10.0, "limit_up": 10.0},
                "600002": {"price": 10.0, "limit_up": 11.0},
            }},
        })

        self.assertFalse(result["recalculated"])
        self.assertEqual(result["replacement_orders"], [])
        self.assertEqual(result["decisions"][0]["reason"], "LIMIT_UP")
        self.assertEqual(result["decisions"][1]["decision"], "execute")
        self.assertEqual(plan, original)

    def test_uses_same_strategy_sale_proceeds_before_frozen_buys(self):
        result = evaluate({"stock": {"orders": [
            {"action": "BUY", "stock_code": "600001", "delta_shares": 100},
            {"action": "SELL", "stock_code": "600002", "delta_shares": -100},
        ]}, "cb": {"orders": []}}, {
            "available_cash": {"stock": 0},
            "quotes": {"stock": {
                "600001": {"price": 10.0, "limit_up": 11.0},
                "600002": {"price": 10.0, "limit_down": 9.0},
            }},
        })

        self.assertEqual([item["code"] for item in result["decisions"]], ["600002", "600001"])
        self.assertTrue(all(item["decision"] == "execute" for item in result["decisions"]))
