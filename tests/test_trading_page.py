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
        self.assertIn("日常轮动", self.html)
        self.assertIn("组合再平衡", self.html)
        self.assertIn("海外长钱申购额度", self.html)
        self.assertIn("不能正常申购", self.html)
        self.assertIn("可以正常申购", self.html)
        self.assertNotIn("新增资金分配", self.html)
        self.assertNotIn("海外长钱恢复申购", self.html)
        self.assertNotIn("临时专项检查", self.html)
        self.assertNotIn("本次新增本金", self.html)
        self.assertNotIn("海外长钱额度核验时间", self.html)
        self.assertNotIn("海外长钱额度核验来源", self.html)
        self.assertNotIn("b_purchase_checked_at", self.html)
        self.assertNotIn("b_purchase_source", self.html)
        self.assertNotIn("needsContribution()", self.html)
        self.assertNotIn(">A 内部检查<", self.html)
        self.assertNotIn(">B 申购恢复<", self.html)

    def test_account_facts_use_a_visible_date_that_is_not_reset_by_old_context(self):
        self.assertIn("账户事实日", self.html)
        self.assertIn('x-model="form.snapshot_date"', self.html)
        self.assertIn("事实日 ", self.html)
        self.assertIn("accountRole(acc)", self.html)
        self.assertIn("summary.read_model?.accounts", self.html)
        self.assertIn("acc.readModel?.role || ''", self.html)
        self.assertNotIn("role: 'A 组合", self.html)
        self.assertNotIn("role: 'B 组合", self.html)
        self.assertNotIn("role: 'C 组合", self.html)
        self.assertNotIn(
            "this.form.snapshot_date = context.snapshot_date || this.form.snapshot_date",
            self.html,
        )

    def test_default_dates_use_browser_local_date_instead_of_utc_date(self):
        self.assertIn("function localDate()", self.html)
        self.assertIn("snapshot_date: localDate()", self.html)
        self.assertIn("tDate: localDate()", self.html)

    def test_inputs_use_progressive_disclosure_and_explicit_b_status(self):
        self.assertIn('x-show="needsBCheck()"', self.html)
        self.assertIn('x-model="funding.b_purchase_status"', self.html)
        self.assertIn('value="unchecked"', self.html)
        self.assertIn('value="unavailable"', self.html)
        self.assertIn('value="available"', self.html)
        self.assertIn('x-show="funding.b_purchase_status === \'available\'"', self.html)
        start = self.html.index("needsBCheck()")
        end = self.html.index("async loadTransferPlan", start)
        self.assertIn("this.funding.check_type === 'quarterly'", self.html[start:end])
        self.assertNotIn("monthly_contribution", self.html[start:end])
        self.assertNotIn("b_recovery", self.html[start:end])
        self.assertNotIn("ad_hoc", self.html[start:end])

    def test_complete_plan_prefers_server_endpoint_and_falls_back_for_legacy_backend(self):
        self.assertIn("async generateCompletePlan()", self.html)
        start = self.html.index("async generateCompletePlan()")
        end = self.html.index("async loadPlan(", start)
        body = self.html[start:end]
        self.assertIn("await this.saveFundingContext()", body)
        self.assertIn("fetch('/api/plan/generate', { method: 'POST' })", body)
        self.assertIn("response.status === 404", body)
        self.assertIn("await this.generateCompletePlanLegacy()", body)
        self.assertIn("componentErrors: { cb: '', stock: '' }", self.html)
        self.assertIn("async generateCompletePlanLegacy()", self.html)
        self.assertIn("await this.ensureRanking(strategy)", self.html)
        self.assertIn("await this.generateOrders(strategy)", self.html)

    def test_context_save_handles_legacy_backend_and_validation_details(self):
        self.assertIn("async postFundingContext(payload)", self.html)
        self.assertIn("delete legacyPayload.b_purchase_status", self.html)
        self.assertIn("extra_forbidden", self.html)
        self.assertIn("formatApiError(body, '保存本次计划条件失败')", self.html)
        self.assertIn("item.loc?.join('.')", self.html)

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
        start = self.html.index("账户间资金调拨")
        end = self.html.index("转债计划", start)
        funding_card = self.html[start:end]
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
        self.assertIn("accountReadModel: null", self.html)
        self.assertIn("this.accountReadModel = summary.read_model || this.accountReadModel", self.html)
        self.assertIn("this.transferPlan?.account_read_model || this.accountReadModel", self.html)
        self.assertIn("this.plan?.account_read_model || this.accountReadModel", self.html)
        self.assertIn("readModelAccountById(id)", self.html)
        self.assertIn("readModelPortfolioById(id)", self.html)
        self.assertIn("action.source !== 'A' && action.target !== 'A'", self.html)
        self.assertNotIn("决策链路", self.html)
        self.assertNotIn("转出到", self.html)
        self.assertNotIn("账户目标占比", self.html)
        self.assertNotIn("本次执行调拨", self.html)
        self.assertNotIn("账户目标与调拨结果", self.html)
        self.assertNotIn("账户结果摘要", self.html)
        self.assertNotIn("fundingActionReasons(action)", self.html)
        self.assertNotIn("fundingDecisionSteps()", self.html)
        self.assertNotIn("现金池", funding_card)
        self.assertNotIn("转债账户", self.html)
        self.assertNotIn("股票账户", self.html)
        self.assertNotIn('<template x-for="(step, i) in transferSteps()">', self.html)

    def test_funding_card_surfaces_top_level_rebalance_check_for_long_money(self):
        self.assertIn("组合再平衡检查", self.html)
        self.assertIn("topLevelSummaryRows()", self.html)
        self.assertIn("主动组合", self.html)
        self.assertIn("海外长钱", self.html)
        self.assertIn("国内长钱", self.html)
        self.assertIn("topLevelRowStatus(row)", self.html)

    def test_funding_basis_nests_accounts_under_active_portfolio(self):
        self.assertIn("basisExpanded: { A: true }", self.html)
        self.assertIn("fundingBasisRows()", self.html)
        self.assertIn("item.kind === 'child'", self.html)
        self.assertIn("toggleFundingBasis(item.row.key)", self.html)
        self.assertIn("主动组合可展开查看其下属账户", self.html)
        self.assertIn("含 3 个账户", self.html)
        self.assertIn("展开主动组合下属账户", self.html)
        self.assertIn("收起主动组合下属账户", self.html)
        self.assertNotIn('colspan="7"', self.html)
        self.assertNotIn("item.kind === 'children'", self.html)
        self.assertNotIn("fundingBasisSections()", self.html)
        self.assertNotIn("主动组合内部账户计算依据", self.html)
        self.assertNotIn('x-for="row in fundingRows()"', self.html)

    def test_order_cash_summary_labels_show_transfer_sequence(self):
        self.assertIn("下单后现金", self.html)
        self.assertIn("当日入金", self.html)
        self.assertIn("订单净额", self.html)
        self.assertIn("次日回流", self.html)
        self.assertIn("cashSummary(strategy)", self.html)
        self.assertNotIn("sell_proceeds", self.html)
        self.assertNotIn("buy_cost", self.html)

    def test_order_quantity_display_uses_backend_delta_shares_field(self):
        self.assertIn("orderShares(order)", self.html)
        self.assertIn("orderQuantityText(r, '张')", self.html)
        self.assertIn("orderQuantityText(r, '股')", self.html)
        self.assertIn("orderQuantityClass(r)", self.html)
        self.assertIn("orders.filter(r => r.action === action && this.orderShares(r) !== 0)", self.html)

    def test_order_cards_show_reference_price_and_estimated_amount_without_fee_column(self):
        self.assertIn(">估算金额<", self.html)
        self.assertIn(">参考价<", self.html)
        self.assertIn("orderReferencePrice(r)", self.html)
        self.assertIn(".order-plan-table", self.html)
        self.assertIn("table-layout: fixed", self.html)
        self.assertIn(".order-group-grid", self.html)
        self.assertIn("align-items: start", self.html)
        self.assertIn("orderGroupColumns(groups)", self.html)
        self.assertIn("orderGroupColumns(cbGroups())", self.html)
        self.assertIn("orderGroupColumns(stockGroups())", self.html)
        self.assertIn("['SELL', 'BUY']", self.html)
        self.assertIn("['TRIM', 'ADD']", self.html)
        self.assertEqual(self.html.count('class="order-group-grid'), 2)
        self.assertEqual(self.html.count('class="order-plan-table'), 2)
        self.assertEqual(self.html.count('class="order-code-col"'), 2)
        self.assertEqual(self.html.count('class="order-name-col"'), 2)
        self.assertEqual(self.html.count('class="order-quantity-col"'), 2)
        self.assertEqual(self.html.count('class="order-price-col"'), 2)
        self.assertEqual(self.html.count('class="order-amount-col"'), 2)
        self.assertNotIn(">费用<", self.html)
        self.assertNotIn(">下单价<", self.html)
        self.assertNotIn(">委托价<", self.html)
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

    def test_changelog_page_uses_static_aihot_style_stream(self):
        self.assertIn("data-lucide=\"bar-chart-3\"", self.html)
        self.assertIn("data-lucide=\"square\"", self.html)
        self.assertIn("data-lucide=\"heart\"", self.html)
        self.assertIn("data-lucide=\"history\"", self.html)
        self.assertIn("data-lucide=\"message-circle\"", self.html)
        self.assertIn("lucide@0.468.0", self.html)
        self.assertIn("rel=\"preconnect\" href=\"https://unpkg.com\"", self.html)
        self.assertIn("工作台", self.html)
        self.assertIn("更多", self.html)
        self.assertIn(">关于<", self.html)
        self.assertIn(">反馈<", self.html)
        self.assertIn("aria-disabled=\"true\"", self.html)
        self.assertIn("disabled", self.html)
        self.assertIn("nav-item-disabled", self.html)
        self.assertIn("aria-current", self.html)
        self.assertIn("setActivePage($event.detail)", self.html)
        self.assertIn("pageFromLocation()", self.html)
        self.assertIn("hashchange", self.html)
        self.assertIn("main.scrollTo({ top: 0, left: 0 })", self.html)
        self.assertLess(self.html.index(">账户<"), self.html.index(">计划<"))
        self.assertLess(self.html.index(">计划<"), self.html.index(">关于<"))
        self.assertLess(self.html.index(">关于<"), self.html.index(">更新日志<"))
        self.assertLess(self.html.index(">更新日志<"), self.html.index(">反馈<"))
        self.assertIn("$store.page === 'changelog'", self.html)
        self.assertIn('x-data="changelogPage()"', self.html)
        self.assertIn("fetch('/static/changelog.json')", self.html)
        self.assertIn("最近发生了什么", self.html)
        self.assertIn("CHANGELOG", self.html)
        self.assertIn("formatGroupDate(group.date)", self.html)
        self.assertIn("weekdayText(group.date)", self.html)
        self.assertIn("formatToParts", self.html)
        self.assertIn(":datetime=\"group.date\"", self.html)
        self.assertIn(":datetime=\"entry.date + 'T' + entry.time\"", self.html)
        self.assertIn("changelog-entry-grid", self.html)
        self.assertIn("class=\"app-nav", self.html)
        self.assertIn("app-nav-section", self.html)
        self.assertIn("width: 240px", self.html)
        self.assertIn("width: 100%", self.html)
        self.assertIn("workspace-shell", self.html)
        self.assertIn("max-width: 1128px", self.html)
        self.assertIn("grid-template-columns: 150px minmax(0, 1fr)", self.html)
        self.assertIn(".changelog-date-header", self.html)
        self.assertIn("--ark-line: #e5e7eb", self.html)
        self.assertIn("border-bottom: 1px solid var(--ark-line)", self.html)
        self.assertIn("font-size: 48px", self.html)
        self.assertIn("font-size: 34px", self.html)
        self.assertIn("text-wrap: balance", self.html)
        self.assertIn("-webkit-tap-highlight-color: transparent", self.html)
        self.assertIn("groupedEntries()", self.html)
        self.assertIn("entry.time", self.html)
        self.assertIn("entry.type", self.html)
        self.assertIn("entry.title", self.html)
        self.assertIn("entry.body", self.html)
        self.assertNotIn("absolute -left-[5px]", self.html)
        self.assertNotIn("relative pl-5 pb-5", self.html)
        self.assertNotIn("entry.issue", self.html)
        self.assertNotIn("entry.pr", self.html)
        self.assertNotIn("entry.commit", self.html)
        self.assertNotIn("entry.hash", self.html)


if __name__ == "__main__":
    unittest.main()
