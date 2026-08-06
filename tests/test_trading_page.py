import unittest
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


class TestTradingPageInteraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_navigation_only_exposes_available_destinations(self):
        self.assertIn('aria-label="账户"', self.html)
        self.assertIn('aria-label="今日计划"', self.html)
        self.assertIn('aria-label="更新日志"', self.html)
        self.assertNotIn('aria-label="关于，暂未开放"', self.html)
        self.assertNotIn('aria-label="反馈，暂未开放"', self.html)
        self.assertNotIn("nav-item-disabled", self.html)

    def test_shared_shell_uses_product_identity_and_direct_plan_first_links(self):
        self.assertIn("<title>方舟计划 · 个人投资系统</title>", self.html)
        self.assertIn("方舟计划", self.html)
        self.assertIn("个人投资系统", self.html)
        self.assertIn('href="#trading"', self.html)
        self.assertIn('href="#account"', self.html)
        self.assertIn('href="#changelog"', self.html)
        self.assertLess(self.html.index('href="#trading"'), self.html.index('href="#account"'))
        self.assertIn("return ['account', 'trading', 'changelog'].includes(page) ? page : 'trading'", self.html)
        self.assertIn("所有计划均需人工复核并在外部券商执行", self.html)
        self.assertNotIn("quant journal", self.html)

    def test_shared_shell_uses_the_selected_balanced_geometry_and_tokens(self):
        self.assertIn("--ark-bg: #f3f5f6", self.html)
        self.assertIn("--ark-text: #17212b", self.html)
        self.assertIn("--ark-accent: #0d716f", self.html)
        self.assertIn("--ark-nav-width: 220px", self.html)
        self.assertIn("--ark-workspace-width: 1140px", self.html)
        self.assertIn("--ark-reading-width: 880px", self.html)
        self.assertIn("max-width: var(--ark-workspace-width)", self.html)
        self.assertIn("padding: 54px 46px 96px", self.html)
        self.assertIn("padding: 30px 18px 72px", self.html)

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
        self.assertIn(':value="factDateInputValue"', self.html)
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

    def test_account_date_switch_loads_a_single_confirmed_fact_identity_before_save(self):
        self.assertIn('@change="requestFactDate($event.target.value)"', self.html)
        self.assertIn('factDateInputValue: localDate()', self.html)
        self.assertIn('x-ref="factDateInput"', self.html)
        self.assertIn("async loadFactDate(factDate)", self.html)
        self.assertIn("/api/account/summary?date=${encodeURIComponent(selectedDate)}", self.html)
        self.assertIn("/api/positions/${strategy}?date=${encodeURIComponent(factDate)}", self.html)
        self.assertIn("/api/positions/${strategy}/quotes?date=${encodeURIComponent(factDate)}", self.html)
        self.assertIn("loadedFactDate: ''", self.html)
        self.assertIn("canSaveFacts()", self.html)
        self.assertIn(":disabled=\"!canSaveFacts() || saveInProgress\"", self.html)
        self.assertIn("snapshot_date: this.loadedFactDate", self.html)
        self.assertIn("保存前请先完成该事实日的数据读取", self.html)
        self.assertIn("discardPendingFactDate()", self.html)
        self.assertIn("this.factDateInputValue = this.form.snapshot_date", self.html)
        self.assertIn("this.$refs.factDateInput.value = this.form.snapshot_date", self.html)
        self.assertIn("有未保存编辑；切换事实日前请先保存或明确放弃这些编辑。", self.html)
        self.assertIn("账户事实已保存，并已重新读取账户事实与计划就绪度。", self.html)

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
        self.assertEqual(self.html.count('class="order-security-col"'), 2)
        self.assertEqual(self.html.count('class="order-direction-col"'), 2)
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

    def test_mobile_order_entries_keep_all_execution_facts_without_horizontal_scrolling(self):
        self.assertEqual(self.html.count('class="order-plan-row'), 2)
        self.assertEqual(self.html.count('data-label="证券"'), 2)
        self.assertEqual(self.html.count('data-label="方向"'), 2)
        self.assertGreaterEqual(self.html.count('data-label="数量"'), 2)
        self.assertGreaterEqual(self.html.count('data-label="参考价"'), 2)
        self.assertGreaterEqual(self.html.count('data-label="估算金额"'), 2)
        self.assertIn('.plan-order-table-scroll { overflow: visible; }', self.html)
        self.assertIn('.order-plan-row {', self.html)
        self.assertIn('grid-template-columns: 72px minmax(0, 1fr)', self.html)

    def test_no_action_does_not_render_an_empty_action_summary(self):
        self.assertIn('<template x-if="planState() === \'action\' && hasExecutionPlans()">', self.html)
        self.assertNotIn('x-show="[\'action\', \'no-action\'].includes(planState())" class="ark-panel plan-action-summary', self.html)

    def test_execution_copy_keeps_same_day_inflow_and_next_day_return_separate(self):
        self.assertIn("fundingPlan().groups", self.html)
        self.assertIn("group.label", self.html)
        self.assertIn("action.source_account_name", self.html)
        self.assertIn("action.target_account_name", self.html)
        self.assertNotIn("端到端", self.html)

    def test_plan_page_uses_execution_plans_instead_of_global_workload(self):
        self.assertIn("executionReadModel()", self.html)
        self.assertIn("fundingPlan()", self.html)
        self.assertIn("hasExecutionPlans()", self.html)
        self.assertIn("资金调拨计划", self.html)
        self.assertNotIn("actionItems()", self.html)
        self.assertNotIn("个步骤", self.html)
        self.assertNotIn("今日动作摘要", self.html)

    def test_execution_plans_show_server_supplied_funding_dates(self):
        self.assertIn("group.display_date", self.html)
        self.assertIn("action.display_date", self.html)
        self.assertIn("funding.display_date", self.html)

    def test_running_state_precedes_readiness_errors(self):
        start = self.html.index("planState() {")
        end = self.html.index("planStateTitle() {", start)
        plan_state = self.html[start:end]
        self.assertLess(
            plan_state.index("this.generatingPlan"),
            plan_state.index("this.readinessError"),
        )

    def test_funding_state_copy_and_style_share_one_descriptor(self):
        self.assertIn("fundingStateDescriptor(funding)", self.html)
        self.assertIn("return this.fundingStateDescriptor(funding).text", self.html)
        self.assertIn("return this.fundingStateDescriptor(funding).className", self.html)

    def test_account_trading_plans_use_real_accounts_and_complete_cash_equation(self):
        self.assertIn("账户交易计划", self.html)
        self.assertIn('x-for="plan in accountTradingPlans()"', self.html)
        self.assertIn("plan.account_name", self.html)
        self.assertIn("plan.portfolio_name", self.html)
        self.assertIn("plan.strategy_name", self.html)
        self.assertIn("fundingStateText(plan.funding)", self.html)
        self.assertIn("plan.trade_summary.sell_count", self.html)
        self.assertIn("plan.trade_summary.buy_count", self.html)
        self.assertIn("plan.cash.starting_available", self.html)
        self.assertIn("plan.cash.transfer_in", self.html)
        self.assertIn("plan.cash.transfer_out", self.html)
        self.assertIn("plan.cash.expected_sell", self.html)
        self.assertIn("plan.cash.expected_buy", self.html)
        self.assertIn("plan.cash.expected_ending", self.html)
        self.assertIn("计划后预计资金余额", self.html)
        self.assertIn('class="plan-account-card"', self.html)
        self.assertNotIn('class="ark-panel plan-transfer-panel', self.html)
        self.assertNotIn('class="ark-panel plan-order-panel', self.html)

    def test_funding_plan_precedes_collapsed_account_plans(self):
        funding = self.html.index('aria-label="资金调拨计划"')
        accounts = self.html.index('aria-label="账户交易计划"')
        self.assertLess(funding, accounts)
        self.assertIn('<details class="plan-account-card"', self.html)
        self.assertNotIn('<details class="plan-account-card" open', self.html)

    def test_account_plan_details_keep_trades_inside_sell_and_buy_phases(self):
        self.assertIn('x-for="phase in plan.phases"', self.html)
        self.assertIn("phaseLabel(phase.phase)", self.html)
        self.assertIn('x-for="order in phase.orders"', self.html)
        self.assertIn("orderActionText(order.action)", self.html)
        self.assertIn("order.code", self.html)
        self.assertIn("order.name", self.html)
        self.assertIn("order.quantity", self.html)
        self.assertIn("order.unit", self.html)
        self.assertIn("order.reference_price", self.html)
        self.assertIn("order.estimated_amount", self.html)
        self.assertIn(">动作<", self.html)
        self.assertIn(">证券代码与名称<", self.html)
        self.assertIn(">数量<", self.html)
        self.assertIn(">参考价<", self.html)
        self.assertIn(">估算金额<", self.html)

    def test_account_plan_summary_exposes_the_trade_detail_toggle(self):
        self.assertIn("plan.account_name + '交易计划'", self.html)
        self.assertIn('class="plan-account-detail-toggle"', self.html)
        self.assertIn("展开证券交易明细", self.html)
        self.assertIn("收起证券交易明细", self.html)
        self.assertIn('.plan-account-card[open] .plan-account-toggle-open', self.html)

    def test_new_account_trade_rows_become_two_line_mobile_records(self):
        self.assertIn('class="plan-trade-row"', self.html)
        self.assertIn('grid-template-areas:', self.html)
        self.assertIn('"security security action"', self.html)
        self.assertIn('"quantity price amount"', self.html)
        self.assertIn('.plan-trade-table-scroll { overflow: visible; }', self.html)

    def test_order_cash_summary_is_a_footer_not_a_grid_card(self):
        self.assertEqual(self.html.count("order-cash-summary-footer"), 2)
        self.assertNotIn(
            'class="mt-3 pt-3 border-t border-gray-100 text-xs space-y-1"',
            self.html,
        )

    def test_plan_conditions_collapse_after_plan_exists(self):
        self.assertIn(':open="!plan"', self.html)
        self.assertIn("计划条件", self.html)

    def test_plan_page_uses_server_states_and_puts_actions_before_readiness(self):
        self.assertIn("fetch('/api/plan/readiness')", self.html)
        self.assertIn("fetch('/api/plan/generated')", self.html)
        self.assertIn("fetch(`/api/plan/generated/${this.latestGeneration.plan_id}`)", self.html)
        self.assertIn("hasCurrentGeneratedPlan()", self.html)
        self.assertIn("if (!this.hasCurrentGeneratedPlan()) return null", self.html)
        self.assertIn("planState()", self.html)
        self.assertIn("planStateTitle()", self.html)
        self.assertIn("今日需要操作", self.html)
        self.assertIn("今日无需操作", self.html)
        self.assertIn("今日计划待生成", self.html)
        self.assertIn("无法确认计划状态", self.html)
        self.assertIn("无法确认账户事实", self.html)
        self.assertIn("readinessError: ''", self.html)
        self.assertIn("待补充账户事实", self.html)
        self.assertIn("计划已失效", self.html)
        self.assertIn("计划生成失败", self.html)
        self.assertIn("executionReadModel()", self.html)
        self.assertIn("fundingPlan()", self.html)
        self.assertIn("hasExecutionPlans()", self.html)
        self.assertIn("aria-live=\"polite\"", self.html)
        self.assertIn("<template x-if=\"planState() === 'action' && hasExecutionPlans()\">", self.html)
        self.assertIn("if (!this.hasCurrentGeneratedPlan()) return null", self.html)
        self.assertIn('x-show="hasCurrentGeneratedPlan() && executionDate()"', self.html)
        self.assertIn('class="plan-execution-badge', self.html)
        decision = self.html.index('class="plan-decision-hero')
        actions = self.html.index('class="plan-execution-dossier')
        readiness = self.html.index('class="ark-panel plan-readiness-panel')
        self.assertLess(decision, actions)
        self.assertLess(actions, readiness)
        self.assertIn("正在生成完整计划…", self.html)
        self.assertNotIn("generationStage + '…'", self.html)

    def test_plan_generation_gate_guides_each_server_state_without_duplicate_submission(self):
        self.assertIn("canGenerateCompletePlan()", self.html)
        self.assertIn(':disabled="!canGenerateCompletePlan()"', self.html)
        self.assertIn("if (!this.canGenerateCompletePlan()) return", self.html)
        self.assertIn("async retryPlanReadiness()", self.html)
        self.assertIn("重新读取计划输入就绪度", self.html)
        self.assertIn("goToMissingFactAccount()", self.html)
        self.assertIn("navigateToAccountFact(accountId)", self.html)
        self.assertIn("focusAccountFact(accountId)", self.html)
        self.assertIn("account-fact-", self.html)
        self.assertIn("requestAnimationFrame(() => input?.focus())", self.html)

    def test_account_maintenance_controls_are_keyboard_reachable(self):
        self.assertIn(":role=\"acc.posType ? 'button' : null\"", self.html)
        self.assertIn(":tabindex=\"acc.posType ? 0 : null\"", self.html)
        self.assertIn("@keydown.enter.prevent=\"acc.posType ? toggleExpand(acc.id) : null\"", self.html)
        self.assertIn("@keydown.space.prevent=\"acc.posType ? toggleExpand(acc.id) : null\"", self.html)
        self.assertIn('aria-label="编辑账户金额"', self.html)
        self.assertIn("@keydown.enter.prevent.stop=\"editingSimple = acc.id\"", self.html)
        self.assertIn("@keydown.space.prevent.stop=\"editingSimple = acc.id\"", self.html)
        self.assertIn("window.matchMedia('(prefers-reduced-motion: reduce)').matches", self.html)

    def test_account_page_prioritizes_fact_readiness_and_portfolio_structure(self):
        self.assertIn("fetch('/api/plan/readiness')", self.html)
        self.assertIn("accountReadinessTitle()", self.html)
        self.assertIn("事实已就绪", self.html)
        self.assertIn("项事实需要更新", self.html)
        self.assertIn("查看并更新这些账户", self.html)
        self.assertIn('id="account-facts"', self.html)
        self.assertIn("portfolioSections()", self.html)
        self.assertIn("summary?.read_model?.portfolios", self.html)
        self.assertIn("summary?.read_model?.accounts", self.html)
        readiness = self.html.index('class="account-readiness-hero')
        structure = self.html.index('class="ark-panel account-portfolio-structure')
        maintenance = self.html.index('id="account-facts"')
        self.assertLess(readiness, structure)
        self.assertLess(structure, maintenance)
        self.assertIn("saveError: ''", self.html)
        self.assertIn('role="alert"', self.html)
        self.assertNotIn("alert(err.message", self.html)

    def test_account_loading_keeps_failures_empty_accounts_and_loaded_facts_distinct(self):
        self.assertIn("accountFactState: 'idle'", self.html)
        self.assertIn("accountFactError: ''", self.html)
        self.assertIn("async fetchAccountFact(url, label)", self.html)
        self.assertIn("accountFactsFailed()", self.html)
        self.assertIn("accountFactsEmpty()", self.html)
        self.assertIn("accountFactsUsable()", self.html)
        self.assertIn("async retryAccountFacts()", self.html)
        self.assertIn("账户事实读取失败", self.html)
        self.assertIn("重新读取账户事实", self.html)
        self.assertIn("尚未录入账户事实", self.html)
        self.assertIn("读取失败期间不显示资产数值或事实结论", self.html)
        self.assertIn('x-show="accountFactsUsable()"', self.html)
        self.assertIn("if (!this.accountFactsUsable()) return null", self.html)
        self.assertIn('x-show="accountFactsUsable() || accountFactsEmpty()"', self.html)
        self.assertIn("accountFactDisplayValue(acc.totalKey)", self.html)

if __name__ == "__main__":
    unittest.main()
