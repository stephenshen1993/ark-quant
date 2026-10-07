import random
import sqlite3
import unittest
from unittest.mock import patch

import pandas as pd

from app import account_current_state
from app.fund_orders import blocked_directions, build_fund_orders, normalize_terms, reconcile_fund_return
from datasource import db
from portfolio_rebalance import PlanValidationError
from stage_allocation import allocation_amounts, build_stage_plan, targets_for


def facts(stock=350000, nasdaq=200000, technology=100000, bond=250000, cash=100000):
    return dict(stock_total=stock, stock_cash=0, stock_available_cash=0,
                bond_total=bond, bond_cash=0, bond_available_cash=0,
                pingan_total=nasdaq+technology, pingan_cash=0, pingan_available_cash=0,
                nasdaq_total=nasdaq, technology_total=technology, unclassified_total=0,
                cash_pool=cash, changqian_total=0, changqian_pending=0,
                overseas_total=0, overseas_pending=0)


def terms(direction='nasdaq', code='161130', price=10):
    # 虚构执行条件，仅验证计算，不代表真实产品条件。
    return dict(direction=direction, code=code, limit_price=price, lot_size=100,
                commission_rate=.0001, minimum_fee=5, price_date='2026-09-25', cost_reviewed=True)


class TestStageAllocation(unittest.TestCase):
    def test_at_target_only_funds_account_cash_reserves(self):
        plan = build_stage_plan(facts())
        self.assertEqual(sum(a['amount'] for a in plan['actions']), 900)
        self.assertTrue(all(a['source'] == 'cash_pool' for a in plan['actions']))
        self.assertEqual(plan['buy_budgets'], {})
        self.assertEqual(sum(plan['allocation']['targets'].values()), 1000000)

    def test_new_money_only_changes_facts_then_follows_unified_targets(self):
        plan = build_stage_plan(facts(cash=107500))
        self.assertEqual(plan['buy_budgets'], {'stock': 2625, 'nasdaq': 1500, 'bond': 1875})
        self.assertEqual(plan['cash']['immediate_outflow'], 6900)
        self.assertEqual(plan['cash']['terminal_estimate'], 101500)
        self.assertEqual(plan['allocation']['deltas']['technology'], 750)

    def test_broker_cash_is_counted_once_and_offsets_transfer(self):
        account = facts(cash=104500)
        account.update(stock_total=353000, stock_cash=3000, stock_available_cash=3000)
        plan = build_stage_plan(account)
        self.assertEqual(plan['allocation']['total'], 1007500)
        self.assertEqual(plan['allocation']['current']['stock'], 350000)
        self.assertEqual(plan['cash']['immediate_outflow'], 3975)
        self.assertEqual(plan['strategy_cash']['stock'], 2700)  # 本账户现金扣除订单预留
        self.assertEqual(plan['transfer_deltas']['stock'], -300)

    def test_pending_receivable_never_supplies_immediate_buying_power(self):
        account = facts(stock=400000, nasdaq=0, technology=0, bond=300000, cash=0)
        account.update(changqian_total=300000, changqian_pending=300000)
        with self.assertRaises(PlanValidationError):
            build_stage_plan(account)  # 在途不能支付缺失的账户现金预留
        account.update(changqian_total=0, changqian_pending=0, cash_pool=300000)
        self.assertEqual(build_stage_plan(account)['allocation']['total'], 1000000)

    def test_missing_pingan_does_not_mean_zero(self):
        account = facts()
        del account['pingan_total']
        with self.assertRaises(PlanValidationError):
            build_stage_plan(account)

    def test_unavailable_direction_retains_target_and_gap(self):
        plan = build_stage_plan(facts(nasdaq=0, cash=300000), blocked={'nasdaq': '条件未核实'})
        self.assertEqual(plan['allocation']['targets']['nasdaq'], 200000)
        self.assertEqual(plan['buy_budgets'], {})
        self.assertEqual(plan['allocation']['planned_deltas']['nasdaq'], 0)
        self.assertEqual(plan['allocation']['targets']['technology'], 100000)

    def test_two_funds_share_one_account_cash(self):
        account = facts(nasdaq=0, technology=0, cash=100000)
        account.update(pingan_total=10000, pingan_cash=10000, pingan_available_cash=10000)
        plan = build_stage_plan(account)
        fund_buys = sum(plan['buy_budgets'].get(k, 0) for k in ('nasdaq','technology'))
        transfer = sum(a['amount'] for a in plan['actions'] if a['target'] == 'pingan')
        self.assertAlmostEqual(transfer + 10000, fund_buys + 300, places=2)
        self.assertLessEqual(plan['cash']['immediate_outflow'], 100000)

    def test_opposing_pingan_orders_do_not_spend_unfilled_sell_proceeds(self):
        account = facts(nasdaq=250000, technology=50000)
        plan = build_stage_plan(account)
        self.assertIn({'source': 'cash_pool', 'target': 'pingan'},
                      [{k: a[k] for k in ('source', 'target')} for a in plan['actions']])
        self.assertEqual(plan['buy_budgets']['technology'], 50000)
        self.assertEqual(plan['cash']['immediate_outflow'], 50900)

    def test_randomized_shared_cash_and_target_conservation(self):
        rng = random.Random(87)
        for _ in range(250):
            account = facts(*(rng.randrange(0, 800000) for _ in range(5)))
            for prefix in ('stock', 'bond', 'pingan'):
                cash = rng.randrange(20000)
                account[prefix + '_cash'] = cash
                account[prefix + '_available_cash'] = rng.randrange(cash + 1)
                account[prefix + '_total'] += cash
            plan = build_stage_plan(account)
            self.assertAlmostEqual(sum(plan['allocation']['deltas'].values()), 0, places=2)
            self.assertAlmostEqual(sum(plan['allocation']['planned_deltas'].values()), 0, places=2)
            self.assertLessEqual(plan['cash']['immediate_outflow'], account['cash_pool'])
            self.assertGreaterEqual(plan['cash']['remaining'], 0)
            for carrier, keys, prefix in (('stock',['stock'],'stock'), ('cb',['bond'],'bond'), ('pingan',['nasdaq','technology'],'pingan')):
                incoming = sum(a['amount'] for a in plan['actions'] if a['target'] == carrier)
                self.assertLessEqual(sum(plan['buy_budgets'].get(k,0) for k in keys),
                                     incoming + account[prefix+'_available_cash'] + .01)

    def test_temperature_and_legacy_new_money_context_cannot_change_targets(self):
        account = facts()
        baseline = targets_for(allocation_amounts(account))
        for temperature in (None, 0, 50, 100):
            account.update(temperature=temperature, new_contribution=99999, check_type='quarterly')
            self.assertEqual(build_stage_plan(account)['allocation']['targets'], baseline)


class TestFundBudgetOrders(unittest.TestCase):
    def test_overweight_fund_is_deferred_without_a_current_sell(self):
        account = facts(nasdaq=255555, technology=44445)
        account.update(fund_terms=normalize_terms([terms(), terms('technology','501312',7)]),
                       fund_positions=[dict(code='161130', quantity=25555), dict(code='501312', quantity=6349)])
        transfer = build_stage_plan(account)
        funds = build_fund_orders(account, transfer, '2026-09-25')
        reconciled = reconcile_fund_return(account, transfer, funds)
        incoming = sum(a['amount'] for a in reconciled['actions'] if a['target'] == 'pingan')
        outgoing = sum(a['amount'] for a in reconciled['actions'] if a['source'] == 'pingan')
        net = sum((1 if o['shares'] < 0 else -1) * o['amount'] - o['estimated_fee'] for o in funds['orders'])
        self.assertEqual(outgoing, 0)
        self.assertGreater(transfer['deferred_reductions']['nasdaq'], 0)
        self.assertFalse(transfer['sell_budgets'])
        self.assertTrue(all(o['shares'] > 0 for o in funds['orders']))
        self.assertGreaterEqual(round(incoming + net - outgoing, 2), 0)
        self.assertLessEqual(incoming + net - outgoing, 1000)

    def test_lots_fees_and_two_orders_stay_inside_shared_budget(self):
        account = facts(nasdaq=0, technology=0, cash=400000)
        account.update(fund_terms=normalize_terms([terms(), terms('technology','501312',7)]), fund_positions=[])
        plan = build_stage_plan(account)
        result = build_fund_orders(account, plan, '2026-09-25')
        self.assertEqual(len(result['orders']), 2)
        self.assertLessEqual(result['summary']['buy_cost'], sum(plan['buy_budgets'].values()))
        for order in result['orders']:
            self.assertEqual(order['shares'] % 100, 0)
            self.assertLessEqual(order['amount'] + order['estimated_fee'], plan['buy_budgets'][order['direction']])

    def test_quote_cost_and_date_are_explicit_conditions(self):
        account = dict(fund_terms=[terms()])
        self.assertNotIn('nasdaq', blocked_directions(account, '2026-09-25'))
        self.assertIn('nasdaq', blocked_directions(account, '2026-09-24'))
        account['fund_terms'][0]['cost_reviewed'] = False
        self.assertIn('nasdaq', blocked_directions(account, '2026-09-25'))
        account['fund_terms'][0]['cost_reviewed'] = True
        account['fund_terms'][0]['minimum_fee'] = None
        self.assertNotIn('nasdaq', blocked_directions(account, '2026-09-25'))

    def test_wrong_direction_or_duplicate_code_rejected(self):
        with self.assertRaises(ValueError):
            normalize_terms([terms(code='501312')])
        with self.assertRaises(ValueError):
            normalize_terms([terms(), terms()])


class TestStageAccountFacts(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(':memory:', check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_pingan_raw_holdings_and_terms_survive_confirmation(self):
        quotes = pd.DataFrame([dict(stock_code='161130',price=10),dict(stock_code='501312',price=7)])
        with patch('datasource.market.fetch_tencent_snapshot', return_value=quotes):
            saved = account_current_state.update_current_account('pingan', expected_version=None,
                available_cash=3000, frozen_cash=500,
                positions=[dict(code='161130',quantity=1000), dict(code='501312',quantity=2000)],
                fund_terms=[terms(), terms('technology','501312',7)])
            confirmed = account_current_state.confirm_current_account('pingan', expected_version=saved['version'])
            summary = account_current_state.build_current_account_summary()
        self.assertEqual(saved['raw_data'], confirmed['raw_data'])
        self.assertEqual(summary['pingan_total'], 27500)
        self.assertEqual(summary['nasdaq_total'], 10000)
        self.assertEqual(summary['technology_total'], 14000)
        self.assertEqual(summary['pingan_cash'], 3500)

    def test_redemption_reclassification_preserves_total_without_inventing_cash(self):
        saved = account_current_state.update_current_account('changqian', expected_version=None, amount=10000)
        saved = account_current_state.update_current_account('changqian', expected_version=saved['version'], amount=3000, pending_amount=7000)
        self.assertEqual(saved['valuation']['total'], 10000)
        self.assertEqual(saved['raw_data']['amount'], 3000)
        self.assertEqual(saved['raw_data']['pending_amount'], 7000)

    def test_missing_quotes_cannot_zero_unclassified_holdings(self):
        with patch('datasource.market.fetch_tencent_snapshot', return_value=pd.DataFrame()):
            account_current_state.update_current_account('pingan', expected_version=None,
                available_cash=0, frozen_cash=0, positions=[dict(code='999999',quantity=100)])
            summary = account_current_state.build_current_account_summary()
        self.assertIsNone(summary['pingan_total'])
        self.assertIsNone(summary['unclassified_total'])

    def test_exchange_fund_fractional_quantity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '整数'):
            account_current_state.update_current_account('pingan', expected_version=None,
                available_cash=0, frozen_cash=0, positions=[dict(code='501312', quantity=1.5)])
