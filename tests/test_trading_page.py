import unittest
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


class TestTradingPageInteraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_regular_flow_has_one_complete_plan_action(self):
        self.assertEqual(self.html.count(">生成完整计划<"), 1)
        self.assertNotIn("保存并生成计划", self.html)
        self.assertNotIn("运行策略", self.html)
        self.assertIn('@click="generateCompletePlan()"', self.html)

    def test_plan_scenarios_use_plain_language_and_hide_exceptions(self):
        self.assertIn("日常计划（主动组合内部）", self.html)
        self.assertIn("新增资金分配", self.html)
        self.assertIn("季度组合再平衡", self.html)
        self.assertIn("海外长钱恢复申购", self.html)
        self.assertIn("临时专项检查", self.html)
        self.assertIn("特殊情况", self.html)
        self.assertNotIn(">A 内部检查<", self.html)
        self.assertNotIn(">B 申购恢复<", self.html)

    def test_account_facts_use_a_visible_date_that_is_not_reset_by_old_context(self):
        self.assertIn("账户事实日", self.html)
        self.assertIn('x-model="form.snapshot_date"', self.html)
        self.assertIn("事实日 ", self.html)
        self.assertNotIn(
            "this.form.snapshot_date = context.snapshot_date || this.form.snapshot_date",
            self.html,
        )

    def test_default_dates_use_browser_local_date_instead_of_utc_date(self):
        self.assertIn("function localDate()", self.html)
        self.assertIn("snapshot_date: localDate()", self.html)
        self.assertIn("tDate: localDate()", self.html)

    def test_inputs_use_progressive_disclosure_and_explicit_b_status(self):
        self.assertIn('x-show="needsContribution()"', self.html)
        self.assertIn('x-show="needsBCheck()"', self.html)
        self.assertIn('x-model="funding.b_purchase_status"', self.html)
        self.assertIn('value="unchecked"', self.html)
        self.assertIn('value="unavailable"', self.html)
        self.assertIn('value="available"', self.html)
        self.assertIn('x-show="funding.b_purchase_status === \'available\'"', self.html)

    def test_complete_plan_is_generated_by_one_server_endpoint(self):
        self.assertIn("async generateCompletePlan()", self.html)
        start = self.html.index("async generateCompletePlan()")
        end = self.html.index("async loadPlan(", start)
        body = self.html[start:end]
        self.assertIn("await this.saveFundingContext()", body)
        self.assertIn("fetch('/api/plan/generate', { method: 'POST' })", body)
        self.assertNotIn("await this.ensureRanking(strategy)", body)
        self.assertNotIn("await this.generateOrders(strategy)", body)
        self.assertIn("componentErrors: { cb: '', stock: '' }", self.html)

    def test_missing_trade_errors_trigger_strategy_run(self):
        self.assertIn("async ensureRanking(strategy)", self.html)
        self.assertIn("(this.plan?.trade_errors || []).some(item => item.input === strategy)", self.html)
        self.assertIn("await this.generateRanking(strategy)", self.html)
        self.assertIn("fetch(`/api/rankings/${strategy}/run`, { method: 'POST' })", self.html)

    def test_plan_context_is_rebased_to_the_loaded_plan_date(self):
        self.assertIn("this.funding.snapshot_date = this.tDate", self.html)

    def test_result_cards_are_stable_and_have_unambiguous_status(self):
        self.assertIn("资金调拨", self.html)
        self.assertIn("转债计划", self.html)
        self.assertIn("股票计划", self.html)
        self.assertIn("transferStatusText()", self.html)
        self.assertIn("strategyStatus('cb')", self.html)
        self.assertIn("strategyStatus('stock')", self.html)
        self.assertIn("账户间资金调拨尚未检查", self.html)
        self.assertIn("需调拨 ${this.fundingActions().length} 笔", self.html)
        self.assertIn("高级操作", self.html)
        self.assertNotIn("① 资金调拨", self.html)
        self.assertNotIn("② 转债调仓", self.html)
        self.assertNotIn("③ 股票调仓", self.html)

    def test_funding_card_prioritizes_lightweight_execution_cards(self):
        self.assertIn("本次请执行", self.html)
        self.assertIn("fundingActionSentence(action)", self.html)
        self.assertIn("转入后仍差", self.html)
        self.assertIn("展开查看计算依据", self.html)
        self.assertIn("当前占比", self.html)
        self.assertIn("目标占比", self.html)
        self.assertIn("本次调拨", self.html)
        self.assertIn("调后状态", self.html)
        self.assertIn("华泰账户", self.html)
        self.assertIn("广发账户", self.html)
        self.assertIn("资金账户", self.html)
        self.assertIn("调后仍需转入", self.html)
        self.assertIn("fundingRows()", self.html)
        self.assertIn("fundingActions()", self.html)
        self.assertIn("fundingActionReason(action)", self.html)
        self.assertIn("action.sourceLabel", self.html)
        self.assertIn("action.targetLabel", self.html)
        self.assertNotIn("决策链路", self.html)
        self.assertNotIn("转出到", self.html)
        self.assertNotIn("账户目标占比", self.html)
        self.assertNotIn("本次执行调拨", self.html)
        self.assertNotIn("账户目标与调拨结果", self.html)
        self.assertNotIn("账户结果摘要", self.html)
        self.assertNotIn("fundingActionReasons(action)", self.html)
        self.assertNotIn("fundingDecisionSteps()", self.html)
        self.assertNotIn("现金池", self.html)
        self.assertNotIn("转债账户", self.html)
        self.assertNotIn("股票账户", self.html)
        self.assertNotIn('<template x-for="(step, i) in transferSteps()">', self.html)

    def test_order_cash_summary_labels_show_transfer_sequence(self):
        self.assertIn("下单后现金", self.html)
        self.assertIn("当日入金", self.html)
        self.assertIn("订单净额", self.html)
        self.assertIn("次日回流", self.html)
        self.assertIn("cashSummary(strategy)", self.html)
        self.assertNotIn("sell_proceeds", self.html)
        self.assertNotIn("buy_cost", self.html)
        self.assertNotIn(">卖出回款<", self.html)
        self.assertNotIn(">买入占用<", self.html)
        self.assertNotIn(">下单现金口径<", self.html)
        self.assertNotIn(">订单净影响<", self.html)
        self.assertNotIn(">订单后可用<", self.html)
        self.assertNotIn('x-show="plan?.cb?.summary?.sizing_cash != null"', self.html)
        self.assertNotIn('x-show="plan?.stock?.summary?.sizing_cash != null"', self.html)
        self.assertNotIn(">账面余额<", self.html)
        self.assertNotIn(">实际可用<", self.html)
        self.assertNotIn(">待转入(调拨)<", self.html)
        self.assertNotIn(">当前可用现金<", self.html)
        self.assertNotIn(">资金调拨<", self.html)
        self.assertNotIn(">交易净影响<", self.html)
        self.assertNotIn(">预计剩余现金<", self.html)

    def test_execution_copy_keeps_same_day_inflow_and_next_day_return_separate(self):
        self.assertIn("当日从资金账户转入", self.html)
        self.assertIn("次交易日回流资金账户", self.html)
        self.assertIn("fundingActionTiming(action)", self.html)
        self.assertNotIn("先卖出或减仓释放资金", self.html)
        self.assertNotIn("再通过现金池调拨", self.html)

    def test_order_cash_summary_is_a_footer_not_a_grid_card(self):
        self.assertEqual(self.html.count("order-cash-summary-footer"), 2)
        self.assertNotIn(
            'class="mt-3 pt-3 border-t border-gray-100 text-xs space-y-1"',
            self.html,
        )

    def test_trade_controls_have_visible_keyboard_focus(self):
        self.assertIn("focus-visible:outline-gray-900", self.html)
        self.assertIn("focus-visible:ring-gray-900", self.html)

    def test_plan_conditions_collapse_after_plan_exists(self):
        self.assertIn(':open="!plan"', self.html)
        self.assertIn("计划条件", self.html)


if __name__ == "__main__":
    unittest.main()
