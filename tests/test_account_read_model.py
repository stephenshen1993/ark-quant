import unittest

from app.account_read_model import build_account_read_model


class TestAccountReadModel(unittest.TestCase):
    def test_exposes_accounts_strategies_and_portfolios_without_http(self):
        read_model = build_account_read_model({
            "snapshot_date": "2026-06-29",
            "temperature": 45.0,
            "check_type": "quarterly",
            "new_contribution": 5000,
            "stock_total": 10000,
            "stock_cash": 1000,
            "stock_available_cash": 750,
            "stock_frozen_cash": 250,
            "bond_total": 20000,
            "bond_cash": 300,
            "bond_available_cash": 300,
            "bond_frozen_cash": 0,
            "cash_pool": 40000,
            "overseas_total": 50000,
            "changqian_total": 30000,
            "total_assets": 150000,
            "account_snapshot_dates": {
                "stock": "2026-06-29",
                "cb": "2026-06-29",
                "cash": "2026-06-29",
                "overseas": "2026-06-29",
                "changqian": "2026-06-29",
            },
            "account_updated_at": {
                "stock": "2026-06-29T15:30:00",
            },
        })

        accounts = {item["id"]: item for item in read_model["accounts"]}
        self.assertEqual(accounts["stock"]["name"], "广发账户")
        self.assertEqual(accounts["stock"]["kind"], "account")
        self.assertEqual(accounts["stock"]["available_cash"], 750)
        self.assertEqual(accounts["stock"]["frozen_cash"], 250)
        self.assertEqual(accounts["stock"]["strategy_ids"], ["smallcap_stock"])
        self.assertEqual(accounts["stock"]["portfolio_id"], "A")
        self.assertEqual(accounts["cash"]["role"], "A 组合现金池承载账户")

        strategies = {item["id"]: item for item in read_model["strategies"]}
        self.assertEqual(strategies["smallcap_stock"]["account_ids"], ["stock"])
        self.assertEqual(
            strategies["multifactor_convertible_bond"]["account_names"],
            ["华泰账户"],
        )

        portfolios = {item["id"]: item for item in read_model["portfolios"]}
        self.assertEqual(portfolios["A"]["current_amount"], 70000)
        self.assertEqual(portfolios["A"]["account_ids"], ["stock", "cb", "cash"])
        self.assertEqual(portfolios["B"]["account_ids"], ["overseas"])
        self.assertEqual(portfolios["C"]["account_ids"], ["changqian"])

        nodes = {item["id"]: item for item in read_model["portfolio_nodes"]}
        self.assertEqual(nodes["a_smallcap_stock"]["account_ids"], ["stock"])
        self.assertEqual(nodes["a_convertible_bond"]["account_ids"], ["cb"])
        self.assertEqual(nodes["a_cash_pool"]["account_ids"], ["cash"])

        self.assertEqual(
            read_model["legacy_adapter"]["strategy_to_account_id"]["stock"],
            "stock",
        )
        self.assertIn("legacy", accounts["stock"])

    def test_returns_none_when_account_summary_is_missing(self):
        self.assertIsNone(build_account_read_model(None))
