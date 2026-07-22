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

    def test_complete_plan_orchestrates_existing_independent_steps(self):
        self.assertIn("async generateCompletePlan()", self.html)
        self.assertIn("await this.saveFundingContext()", self.html)
        self.assertIn("await this.ensureRanking(strategy)", self.html)
        self.assertIn("await this.generateOrders(strategy)", self.html)
        self.assertIn("componentErrors: { cb: '', stock: '' }", self.html)
        self.assertEqual(self.html.count("for (const strategy of ['cb', 'stock'])"), 2)

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


if __name__ == "__main__":
    unittest.main()
