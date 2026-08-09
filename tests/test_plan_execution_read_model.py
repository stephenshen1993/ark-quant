import json
from pathlib import Path
import unittest

from app.plan_execution_read_model import build_execution_read_model


class TestPlanExecutionReadModel(unittest.TestCase):
    def setUp(self):
        self.account_read_model = {
            "accounts": [
                {
                    "id": "stock",
                    "name": "广发账户",
                    "portfolio_id": "A",
                    "strategy_names": ["小市值股票策略"],
                    "available_cash": 1000.0,
                    "legacy": {"strategy": "stock"},
                },
                {
                    "id": "cb",
                    "name": "华泰账户",
                    "portfolio_id": "A",
                    "strategy_names": ["多因子可转债策略"],
                    "available_cash": 500.0,
                    "legacy": {"strategy": "cb"},
                },
                {
                    "id": "cash",
                    "name": "资金账户",
                    "portfolio_id": "A",
                    "strategy_names": [],
                    "available_cash": None,
                    "legacy": {"strategy": "cash_pool"},
                },
                {
                    "id": "overseas",
                    "name": "海外长钱",
                    "portfolio_id": "B",
                    "strategy_names": [],
                    "available_cash": None,
                    "legacy": {"strategy": "overseas"},
                },
            ],
            "portfolios": [
                {"id": "A", "name": "主动组合", "account_ids": ["stock", "cb", "cash"]},
                {"id": "B", "name": "海外长钱", "account_ids": ["overseas"]},
            ],
            "legacy_adapter": {
                "strategy_to_account_id": {
                    "stock": "stock",
                    "cb": "cb",
                    "cash_pool": "cash",
                    "overseas": "overseas",
                }
            },
        }

    def test_groups_single_leg_funding_actions_by_availability(self):
        fund_transfer = {
            "top_level": {
                "executed_actions": [
                    {
                        "source": "cash_pool",
                        "target": "B",
                        "amount": 3000.0,
                        "reason": "half_band_repair",
                        "available_on": "deferred",
                        "cash_effect": "deferred_cash_in",
                    },
                    {
                        "source": "cash_pool",
                        "target": "A",
                        "amount": 9000.0,
                        "reason": "half_band_repair",
                        "available_on": "deferred",
                        "cash_effect": "deferred_cash_in",
                    },
                ],
                "outflows": [],
            },
            "a_internal": {
                "actions": [
                    {
                        "source": "cash_pool",
                        "target": "bond",
                        "amount": 1200.0,
                        "reason": "a_internal_rebalance",
                        "available_on": "same_day",
                        "cash_effect": "immediate_cash_in",
                    },
                    {
                        "source": "stock",
                        "target": "cash_pool",
                        "amount": 800.0,
                        "reason": "a_internal_rebalance",
                        "available_on": "next_trading_day",
                        "cash_effect": "deferred_cash_return",
                    },
                    {
                        "source": "cash_pool",
                        "target": "stock",
                        "amount": 0.0,
                        "reason": "a_internal_rebalance",
                        "available_on": "same_day",
                        "cash_effect": "immediate_cash_in",
                    },
                ]
            },
        }

        result = build_execution_read_model(
            plan_date="2026-08-05",
            account_read_model=self.account_read_model,
            fund_transfer=fund_transfer,
            cb={"trade_date": "2026-08-06", "orders": [], "summary": None},
            stock={"trade_date": "2026-08-06", "orders": [], "summary": None},
        )

        self.assertEqual(
            [group["availability"] for group in result["funding_plan"]["groups"]],
            ["same_day", "next_trading_day", "deferred"],
        )
        self.assertEqual(
            [group["label"] for group in result["funding_plan"]["groups"]],
            ["当日可用", "下一交易日可用", "等待未来资金可用"],
        )
        self.assertEqual(
            [group["available_date"] for group in result["funding_plan"]["groups"]],
            ["2026-08-06", "2026-08-07", None],
        )
        self.assertEqual(
            [group["display_date"] for group in result["funding_plan"]["groups"]],
            ["2026-08-06", "2026-08-07", "日期待确认"],
        )
        actions = [
            action
            for group in result["funding_plan"]["groups"]
            for action in group["actions"]
        ]
        self.assertEqual(
            [
                (
                    action["source_account_id"],
                    action["source_account_name"],
                    action["target_account_id"],
                    action["target_account_name"],
                    action["amount"],
                )
                for action in actions
            ],
            [
                ("cash", "资金账户", "cb", "华泰账户", 1200.0),
                ("stock", "广发账户", "cash", "资金账户", 800.0),
                ("cash", "资金账户", "overseas", "海外长钱", 3000.0),
            ],
        )
        self.assertEqual(
            [action["reason_label"] for action in actions],
            ["主动组合内部再平衡", "主动组合内部再平衡", "组合偏离修复"],
        )
        self.assertEqual(
            [action["available_date"] for action in actions],
            ["2026-08-06", "2026-08-07", None],
        )
        self.assertEqual(
            [action["display_date"] for action in actions],
            ["2026-08-06", "2026-08-07", "日期待确认"],
        )
        self.assertNotIn("path", actions[0])
        self.assertEqual(result["account_trading_plans"], [])

    def test_omits_funding_plan_when_there_are_no_actions(self):
        result = build_execution_read_model(
            plan_date="2026-08-05",
            account_read_model=self.account_read_model,
            fund_transfer={
                "top_level": {"executed_actions": [], "outflows": []},
                "a_internal": {"actions": []},
            },
            cb={"trade_date": "2026-08-06", "orders": [], "summary": None},
            stock={"trade_date": "2026-08-06", "orders": [], "summary": None},
        )

        self.assertIsNone(result["funding_plan"])

    def test_account_orders_are_sorted_by_execution_priority(self):
        result = build_execution_read_model(
            plan_date="2026-08-05",
            account_read_model=self.account_read_model,
            fund_transfer={"top_level": {"executed_actions": [], "outflows": []}},
            cb={"trade_date": "2026-08-06", "orders": [], "summary": None},
            stock={
                "trade_date": "2026-08-06",
                "summary": {"starting_cash": 1000.0},
                "orders": [
                    {
                        "action": "ADD",
                        "stock_code": "600004",
                        "stock_name": "加仓股票",
                        "current_shares": 100,
                        "target_shares": 200,
                        "shares": 100,
                        "price": 8.0,
                        "amount": 800.0,
                    },
                    {
                        "action": "BUY",
                        "stock_code": "600003",
                        "stock_name": "建仓股票",
                        "current_shares": 0,
                        "target_shares": 100,
                        "shares": 100,
                        "price": 7.0,
                        "amount": 700.0,
                    },
                    {
                        "action": "TRIM",
                        "stock_code": "600002",
                        "stock_name": "减仓股票",
                        "current_shares": 200,
                        "target_shares": 100,
                        "shares": -100,
                        "price": 6.0,
                        "amount": 600.0,
                    },
                    {
                        "action": "SELL",
                        "stock_code": "600001",
                        "stock_name": "清仓股票",
                        "current_shares": 100,
                        "target_shares": 0,
                        "shares": -100,
                        "price": 5.0,
                        "amount": 500.0,
                    },
                ],
            },
        )

        plan = result["account_trading_plans"][0]
        orders = [order for phase in plan["phases"] for order in phase["orders"]]
        self.assertEqual(
            [order["action"] for order in orders],
            ["SELL", "TRIM", "BUY", "ADD"],
        )
        self.assertEqual(
            [order["execution_priority"] for order in orders],
            [0, 1, 2, 3],
        )

    def test_builds_account_plans_in_funding_prerequisite_order(self):
        fund_transfer = {
            "top_level": {"executed_actions": [], "outflows": []},
            "a_internal": {
                "actions": [
                    {
                        "source": "cash_pool",
                        "target": "stock",
                        "amount": 600.0,
                        "reason": "a_internal_rebalance",
                        "available_on": "same_day",
                        "cash_effect": "immediate_cash_in",
                    },
                    {
                        "source": "bond",
                        "target": "cash_pool",
                        "amount": 200.0,
                        "reason": "a_internal_rebalance",
                        "available_on": "next_trading_day",
                        "cash_effect": "deferred_cash_return",
                    },
                ]
            },
        }
        result = build_execution_read_model(
            plan_date="2026-08-05",
            account_read_model=self.account_read_model,
            fund_transfer=fund_transfer,
            cb={
                "trade_date": "2026-08-06",
                "summary": {"starting_cash": 500.0},
                "orders": [
                    {
                        "action": "SELL",
                        "bond_code": "110001",
                        "bond_name": "测试转债甲",
                        "current_shares": 10,
                        "target_shares": 0,
                        "delta_shares": -10,
                        "price": 50.0,
                        "amount": 500.0,
                    },
                    {
                        "action": "BUY",
                        "bond_code": "110002",
                        "bond_name": "测试转债乙",
                        "current_shares": 0,
                        "target_shares": 1,
                        "delta_shares": 1,
                        "price": 100.0,
                        "amount": 100.0,
                    },
                ],
            },
            stock={
                "trade_date": "2026-08-06",
                "summary": {"starting_cash": 1000.0},
                "orders": [
                    {
                        "action": "TRIM",
                        "stock_code": "600001",
                        "stock_name": "测试股票甲",
                        "current_shares": 200,
                        "target_shares": 100,
                        "shares": -100,
                        "price": 5.0,
                        "amount": 500.0,
                    },
                    {
                        "action": "ADD",
                        "stock_code": "600002",
                        "stock_name": "测试股票乙",
                        "current_shares": 100,
                        "target_shares": 200,
                        "shares": 100,
                        "price": 9.0,
                        "amount": 900.0,
                    },
                    {
                        "action": "HOLD",
                        "stock_code": "600003",
                        "stock_name": "测试股票丙",
                        "shares": 0,
                        "price": 8.0,
                        "amount": 0.0,
                    },
                ],
            },
        )

        plans = result["account_trading_plans"]
        self.assertEqual([plan["account_id"] for plan in plans], ["cb", "stock"])
        self.assertEqual(
            [plan["funding"]["state"] for plan in plans],
            ["ready", "needs_same_day_transfer"],
        )
        cb_plan, stock_plan = plans
        self.assertEqual(stock_plan["funding"]["available_date"], "2026-08-06")
        self.assertEqual(stock_plan["funding"]["display_date"], "2026-08-06")
        self.assertEqual(
            stock_plan["funding"]["incoming_actions"],
            [{
                "source_account_id": "cash",
                "source_account_name": "资金账户",
                "target_account_id": "stock",
                "target_account_name": "广发账户",
                "amount": 600.0,
                "availability": "same_day",
                "available_date": "2026-08-06",
                "display_date": "2026-08-06",
            }],
        )
        self.assertEqual(
            (cb_plan["account_name"], cb_plan["portfolio_name"], cb_plan["strategy_name"]),
            ("华泰账户", "主动组合", "多因子可转债策略"),
        )
        self.assertEqual(
            cb_plan["cash"],
            {
                "starting_available": 500.0,
                "transfer_in": 0.0,
                "transfer_out": 200.0,
                "expected_sell": 500.0,
                "expected_buy": 100.0,
                "expected_ending": 700.0,
            },
        )
        self.assertEqual(
            stock_plan["cash"],
            {
                "starting_available": 1000.0,
                "transfer_in": 600.0,
                "transfer_out": 0.0,
                "expected_sell": 500.0,
                "expected_buy": 900.0,
                "expected_ending": 1200.0,
            },
        )
        self.assertEqual(
            stock_plan["trade_summary"],
            {
                "sell_count": 1,
                "sell_estimated_amount": 500.0,
                "buy_count": 1,
                "buy_estimated_amount": 900.0,
            },
        )
        self.assertEqual(
            [phase["phase"] for phase in stock_plan["phases"]],
            ["sell", "buy"],
        )
        self.assertEqual(
            [phase["orders"][0]["action"] for phase in stock_plan["phases"]],
            ["TRIM", "ADD"],
        )
        self.assertEqual(cb_plan["strategy_id"], "cb")
        self.assertEqual(stock_plan["strategy_id"], "stock")
        self.assertEqual(
            cb_plan["execution_guardrails"]["price_basis_date"],
            "2026-08-05",
        )
        self.assertFalse(
            cb_plan["execution_guardrails"]["reference_price_is_limit"]
        )
        cb_rule = cb_plan["execution_guardrails"]["rules"][0]
        config = json.loads(
            (Path(__file__).parents[1] / "config" / "cb_rotation.json").read_text()
        )
        self.assertEqual(cb_rule["kind"], "buy_price_ceiling")
        self.assertEqual(cb_rule["comparison"], "strictly_below")
        self.assertEqual(cb_rule["max_price"], config["filters"]["max_cb_price"])
        stock_rule = stock_plan["execution_guardrails"]["rules"][0]
        self.assertEqual(stock_rule["kind"], "single_position_cap")
        self.assertEqual(stock_rule["max_weight"], 0.10)
        self.assertEqual(stock_rule["check"], "validated_at_generation")
        cb_sell, cb_buy = [
            order
            for phase in cb_plan["phases"]
            for order in phase["orders"]
        ]
        self.assertEqual(
            (cb_sell["current_quantity"], cb_sell["target_quantity"]),
            (10, 0),
        )
        self.assertIsNone(cb_sell["max_execution_price"])
        self.assertEqual(
            (cb_buy["current_quantity"], cb_buy["target_quantity"]),
            (0, 1),
        )
        self.assertEqual(cb_buy["max_execution_price"], cb_rule["max_price"])
        stock_sell, stock_buy = [
            order
            for phase in stock_plan["phases"]
            for order in phase["orders"]
        ]
        self.assertEqual(
            (stock_sell["current_quantity"], stock_sell["target_quantity"]),
            (200, 100),
        )
        self.assertEqual(
            (stock_buy["current_quantity"], stock_buy["target_quantity"]),
            (100, 200),
        )

    def test_future_funding_and_insufficient_cash_are_structured_states(self):
        fund_transfer = {
            "top_level": {"executed_actions": [], "outflows": []},
            "a_internal": {
                "actions": [
                    {
                        "source": "cash_pool",
                        "target": "stock",
                        "amount": 500.0,
                        "reason": "a_internal_rebalance",
                        "available_on": "deferred",
                        "cash_effect": "deferred_cash_in",
                    }
                ]
            },
        }
        stock = {
            "trade_date": "2026-08-06",
            "summary": {"starting_cash": 600.0},
            "orders": [
                {
                    "action": "BUY",
                    "stock_code": "600001",
                    "stock_name": "测试股票",
                    "shares": 100,
                    "price": 10.0,
                    "amount": 1000.0,
                }
            ],
        }

        waiting = build_execution_read_model(
            plan_date="2026-08-05",
            account_read_model=self.account_read_model,
            fund_transfer=fund_transfer,
            cb={"trade_date": "2026-08-06", "orders": [], "summary": None},
            stock=stock,
        )["account_trading_plans"][0]
        self.assertEqual(waiting["funding"]["state"], "waits_for_funds")
        self.assertEqual(waiting["funding"]["available_on"], "deferred")
        self.assertIsNone(waiting["funding"]["available_date"])
        self.assertEqual(waiting["funding"]["display_date"], "日期待确认")
        self.assertEqual(waiting["cash"]["expected_ending"], 100.0)

        stock["orders"][0]["amount"] = 1200.0
        blocked = build_execution_read_model(
            plan_date="2026-08-05",
            account_read_model=self.account_read_model,
            fund_transfer=fund_transfer,
            cb={"trade_date": "2026-08-06", "orders": [], "summary": None},
            stock=stock,
        )["account_trading_plans"][0]
        self.assertEqual(blocked["funding"]["state"], "blocked")
        self.assertEqual(blocked["funding"]["blocked_reason"], "计划后预计资金余额不足")
        self.assertIsNone(blocked["funding"]["available_date"])
        self.assertEqual(blocked["funding"]["display_date"], "尚不可用")
        self.assertEqual(blocked["cash"]["expected_ending"], -100.0)

    def test_omits_accounts_and_phases_without_executable_orders(self):
        result = build_execution_read_model(
            plan_date="2026-08-05",
            account_read_model=self.account_read_model,
            fund_transfer={
                "top_level": {"executed_actions": [], "outflows": []},
                "a_internal": {"actions": []},
            },
            cb={
                "trade_date": "2026-08-06",
                "summary": {"starting_cash": 500.0},
                "orders": [
                    {
                        "action": "HOLD",
                        "bond_code": "110001",
                        "bond_name": "测试转债",
                        "shares": 0,
                        "price": 100.0,
                        "amount": 0.0,
                    }
                ],
            },
            stock={"trade_date": "2026-08-06", "orders": [], "summary": None},
        )

        self.assertEqual(result["account_trading_plans"], [])


if __name__ == "__main__":
    unittest.main()
