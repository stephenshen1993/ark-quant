import unittest
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


class TestTradingPageInteraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_navigation_only_exposes_available_destinations(self):
        self.assertIn('aria-label="概览"', self.html)
        self.assertIn('aria-label="计划"', self.html)
        self.assertIn('aria-label="榜单"', self.html)
        self.assertIn('aria-label="账户"', self.html)
        self.assertIn('aria-label="更新日志"', self.html)
        self.assertIn('aria-label="系统信息"', self.html)
        self.assertNotIn('aria-label="关于，暂未开放"', self.html)
        self.assertNotIn('aria-label="反馈，暂未开放"', self.html)
        self.assertNotIn("nav-item-disabled", self.html)

    def test_shared_shell_uses_product_identity_and_direct_plan_first_links(self):
        self.assertIn("<title>方舟计划 · 个人投资系统</title>", self.html)
        self.assertIn("方舟计划", self.html)
        self.assertIn("个人投资系统", self.html)
        self.assertIn('href="#overview"', self.html)
        self.assertIn('href="#plan"', self.html)
        self.assertIn('href="#rankings"', self.html)
        self.assertIn('href="#account"', self.html)
        self.assertIn('href="#changelog"', self.html)
        self.assertLess(self.html.index('href="#overview"'), self.html.index('href="#plan"'))
        self.assertLess(self.html.index('href="#plan"'), self.html.index('href="#rankings"'))
        self.assertLess(self.html.index('href="#rankings"'), self.html.index('href="#account"'))
        self.assertLess(self.html.index('href="#account"'), self.html.index('href="#changelog"'))
        self.assertIn("['trading', 'today', 'plan'].includes(page)", self.html)
        self.assertIn("return ['overview', 'rankings', 'account', 'changelog'].includes(page) ? page : 'overview'", self.html)
        self.assertIn("page === 'today' ? '#plan' : `#${page}`", self.html)
        self.assertIn("所有计划均需人工复核并在外部券商执行", self.html)
        self.assertNotIn("quant journal", self.html)

    def test_rankings_workspace_is_a_dedicated_plan_input_page(self):
        self.assertIn("function rankingsWorkspace()", self.html)
        self.assertIn('aria-label="策略榜单"', self.html)
        self.assertIn("榜单是计划输入，不是交易计划明细。", self.html)
        self.assertIn("转债榜单", self.html)
        self.assertIn("股票榜单", self.html)
        self.assertIn("fetch(`/api/rankings/${strategy}/dates`)", self.html)
        self.assertIn("fetch(`/api/rankings/${strategy}/${date}`)", self.html)
        self.assertIn("fetch(`/api/rankings/${strategy}/run`, { method: 'POST' })", self.html)
        self.assertIn("导出 Markdown", self.html)
        self.assertIn(".rankings-table {\n    width: 100%;\n    min-width: 680px;", self.html)

    def test_shared_shell_uses_the_selected_balanced_geometry_and_tokens(self):
        self.assertIn("--ark-bg: #f1f4f3", self.html)
        self.assertIn("--ark-text: #14202a", self.html)
        self.assertIn("--ark-accent: #087a78", self.html)
        self.assertIn("--ark-font-display: \"Songti SC\"", self.html)
        self.assertIn("--ark-nav-width: 220px", self.html)
        self.assertIn("--ark-workspace-width: 1140px", self.html)
        self.assertIn("--ark-reading-width: 880px", self.html)
        self.assertIn("max-width: var(--ark-workspace-width)", self.html)
        self.assertIn("padding: 46px 46px 96px", self.html)
        self.assertIn("padding: 30px 18px 72px", self.html)

    def test_overview_is_default_and_stays_independent_from_plan_status(self):
        self.assertIn("function overviewWorkspace()", self.html)
        self.assertIn("fetch('/api/account/latest')", self.html)
        self.assertIn("fetch('/api/plan/context')", self.html)
        self.assertIn('aria-label="投资概览"', self.html)
        self.assertIn("只看整体配置结构；下一步调拨和交易请进入计划。", self.html)
        self.assertIn("顶层组合", self.html)
        self.assertIn("A 组合内部", self.html)
        self.assertIn("B 申购能力", self.html)
        self.assertIn("openBEditor()", self.html)
        self.assertIn("saveBPurchaseStatus()", self.html)
        self.assertIn("fetch('/api/account/context'", self.html)
        self.assertIn("目标与偏离后续接入规则真源", self.html)
        overview_start = self.html.index("function overviewWorkspace()")
        overview_end = self.html.index("function changelogPage()", overview_start)
        overview_body = self.html[overview_start:overview_end]
        self.assertNotIn("/api/plan/readiness", overview_body)
        self.assertNotIn("/api/plan/generated", overview_body)
        self.assertNotIn("计划已生成", overview_body)
        self.assertIn("this.marketTemperature = body.market_temperature || null", overview_body)
        self.assertIn("const temperature = this.marketTemperature?.temperature", overview_body)

    def test_market_temperature_is_a_shared_plan_input_not_account_fact(self):
        self.assertNotIn("class=\"plan-masthead-temperature\"", self.html)
        self.assertIn('温度 <span class="text-gray-700 font-medium tabular-nums"', self.html)
        self.assertIn("currentMarketTemperature()", self.html)
        self.assertIn("const marketTemperature = this.currentMarketTemperature()", self.html)
        self.assertIn("Number(marketTemperature?.temperature ?? this.funding.temperature)", self.html)
        overview_start = self.html.index("function overviewWorkspace()")
        overview_end = self.html.index("function changelogPage()", overview_start)
        overview_body = self.html[overview_start:overview_end]
        self.assertIn("fetch('/api/plan/context')", overview_body)
        self.assertNotIn("const temperature = this.context().temperature", overview_body)

    def test_regular_flow_has_one_complete_plan_action(self):
        self.assertNotIn("保存并生成计划", self.html)
        self.assertNotIn("运行策略", self.html)
        self.assertIn('@click="generateCompletePlan()"', self.html)
        self.assertIn("generatePlanButtonText()", self.html)
        self.assertIn("hasCurrentGeneratedPlan() ? '重新生成计划' : '生成完整计划'", self.html)
        self.assertIn("当前已有可用计划。重新生成可能改变调拨和交易安排，确认继续？", self.html)
        self.assertIn("class=\"plan-generate-action", self.html)

    def test_plan_scenarios_use_plain_language_and_hide_exceptions(self):
        self.assertIn("默认只做 A 组合内轮动", self.html)
        self.assertIn("同时进行组合间再平衡", self.html)
        self.assertIn("本次范围：组合内轮动", self.html)
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

    def test_account_workspace_updates_one_prefilled_account_without_global_fact_dates(self):
        self.assertIn("打开一个账户，沿用现有数据，只更新发生变化的内容。", self.html)
        self.assertIn("function accountWorkspace()", self.html)
        self.assertIn("fetch('/api/accounts')", self.html)
        self.assertIn("available_cash: account.raw_data?.available_cash ?? ''", self.html)
        self.assertIn("const positions = (account.raw_data?.positions || []).map", self.html)
        self.assertIn("positions: this.sortPositionsByMarketValue(positions)", self.html)
        self.assertIn("只会保存这个账户，其他账户保持原样。", self.html)
        self.assertNotIn("账户事实日", self.html)
        self.assertNotIn("function accountPage()", self.html)
        self.assertNotIn("saveAllAccountFacts", self.html)

    def test_account_mobile_selection_is_explicit_and_respects_unsaved_guard(self):
        self.assertIn("account-current-mobile-selector", self.html)
        self.assertIn("aria-label=\"选择当前账户\"", self.html)
        self.assertIn("selectAccountFromControl($event.target.value, $event.target)", self.html)
        self.assertIn("control.value = this.selectedId", self.html)
        self.assertIn(".account-current-rail {\n      display: none;", self.html)

    def test_account_current_holdings_sort_by_market_value_descending(self):
        self.assertIn("列表按持仓市值从高到低排列", self.html)
        self.assertIn("sortPositionsByMarketValue(positions)", self.html)
        sort_start = self.html.index("sortPositionsByMarketValue(positions)")
        sort_end = self.html.index("markEdited()", sort_start)
        body = self.html[sort_start:sort_end]
        self.assertIn("return [...positions].sort", body)
        self.assertIn("return rightValue - leftValue", body)
        self.assertIn("if (leftValue === null) return 1", body)
        self.assertIn("if (rightValue === null) return -1", body)
        self.assertIn("localeCompare", body)

    def test_account_holdings_split_code_and_name_and_keep_one_add_action_at_the_end(self):
        self.assertIn(
            "<span>代码</span><span>名称</span><span>数量</span><span>价格</span><span>市值</span>",
            self.html,
        )
        self.assertIn('class="account-current-security-code"', self.html)
        self.assertIn('class="account-current-security-name"', self.html)
        self.assertIn(
            ':class="`${securityStatusClass(row)}${!row.name ? \' is-placeholder\' : \'\'}`.trim()"',
            self.html,
        )
        self.assertNotIn(
            ':class="[securityStatusClass(row), { \'is-placeholder\': !row.name }]"',
            self.html,
        )
        self.assertIn('class="account-current-add-row"', self.html)
        self.assertEqual(self.html.count("＋ 添加证券"), 1)
        self.assertNotIn("继续添加证券", self.html)
        heading_start = self.html.index('<div class="account-current-section-heading">')
        table_start = self.html.index('<div x-ref="holdingsTable"', heading_start)
        self.assertNotIn("添加证券", self.html[heading_start:table_start])
        self.assertIn("this.draft.positions.push({", self.html)
        self.assertIn("this.$refs.holdingsAddMore?.scrollIntoView({ block: 'nearest' })", self.html)
        self.assertIn("focus({ preventScroll: true })", self.html)

    def test_account_review_changes_identify_securities_by_name_first(self):
        self.assertIn("securityDisplayLabel(code)", self.html)
        self.assertIn("return name ? `${name}（${normalizedCode}）` : normalizedCode", self.html)
        self.assertIn(
            "changes.push(`${this.securityDisplayLabel(code)}：${oldValue} → ${newValue}`)",
            self.html,
        )

    def test_account_holding_quantity_uses_the_security_trading_unit(self):
        self.assertIn(':step="holdingQuantityStep()"', self.html)
        self.assertIn("holdingQuantityStep()", self.html)
        self.assertIn("if (this.selectedId === 'stock') return 100", self.html)
        self.assertIn("if (this.selectedId === 'cb') return 10", self.html)

    def test_new_security_code_lookup_gives_inline_feedback(self):
        self.assertIn("@input=\"handleSecurityCodeInput(row)\"", self.html)
        self.assertIn("if (/^\\d{6}$/.test(code)) this.lookupSecurity(row)", self.html)
        self.assertIn("正在识别证券…", self.html)
        self.assertIn("未能识别证券，保存后系统仍会按代码尝试估值", self.html)
        self.assertIn("securityHelperText(row)", self.html)

    def test_saved_account_can_be_reloaded_for_recheck(self):
        self.assertIn("重新读取核对", self.html)
        self.assertIn("async reloadCurrentAccount()", self.html)
        self.assertIn("已重新读取当前账户数据。", self.html)

    def test_account_workspace_combines_current_data_with_plan_input_readiness(self):
        self.assertIn("workStatusStore().load({ force:", self.html)
        self.assertIn("plan_readiness: readinessUnknown", self.html)
        self.assertIn("当前数据需要为本次计划更新。", self.html)
        self.assertIn("accountReadyForPlan(account)", self.html)

    def test_account_workspace_fails_closed_when_plan_readiness_is_unknown(self):
        self.assertIn("账户计划就绪度待确认", self.html)
        self.assertIn("无法确认计划输入状态", self.html)
        self.assertIn("plan_readiness: readinessUnknown", self.html)
        self.assertIn("? 'unknown'", self.html)
        ready_method = self.html.index("accountReadyForPlan(account)")
        ready_method_end = self.html.index("statusClass(account)", ready_method)
        self.assertIn(
            "account.plan_readiness === 'ready'",
            self.html[ready_method:ready_method_end],
        )
        self.assertNotIn(
            "!account.plan_readiness",
            self.html[ready_method:ready_method_end],
        )

    def test_account_clear_intents_keep_holdings_and_cash_separate(self):
        self.assertIn("clearRequirement(raw = this.payloadRaw())", self.html)
        self.assertIn("return 'holdings'", self.html)
        self.assertIn("return 'account'", self.html)
        self.assertIn("这只会把当前持仓记录为已确认空仓，可用资金和冻结资金保持不变", self.html)
        holdings_branch = self.html.index("else if (mode === 'holdings')")
        holdings_end = self.html.index("this.closeClearDialog(false)", holdings_branch)
        self.assertIn("this.draft.positions = []", self.html[holdings_branch:holdings_end])
        self.assertNotIn("this.draft.available_cash = 0", self.html[holdings_branch:holdings_end])

    def test_account_clear_action_lives_with_save_actions_without_forced_editor_space(self):
        self.assertNotIn("min-height: 560px", self.html)
        editor_start = self.html.index('class="account-current-editor"')
        review_start = self.html.index('class="account-current-review"', editor_start)
        self.assertNotIn("清空当前账户数据", self.html[editor_start:review_start])
        review_end = self.html.index("</aside>", review_start)
        review_body = self.html[review_start:review_end]
        self.assertIn('class="account-current-account-actions"', review_body)
        self.assertIn("清空当前账户数据…", review_body)
        self.assertIn("余额归零，并把当前持仓记录为空仓。", review_body)
        self.assertLess(review_body.index("account-current-primary"), review_body.index("account-current-account-actions"))

    def test_global_status_uses_lightweight_shared_status_without_full_plan_fetch(self):
        self.assertIn("Alpine.store('workStatus', createWorkStatusStore())", self.html)
        self.assertIn("function createWorkStatusStore()", self.html)
        self.assertIn("generation?.summary?.funding_action_count", self.html)
        start = self.html.index("function globalStatusBar()")
        end = self.html.index("function localDate()", start)
        body = self.html[start:end]
        self.assertNotIn("/api/plan/generated/${", body)
        self.assertNotIn("generatedPlan", body)

    def test_pending_plan_state_is_not_rendered_as_problem(self):
        self.assertIn(".plan-decision-hero[data-state=\"pending\"]", self.html)
        self.assertIn("if (!this.hasCurrentGeneratedPlan()) return 'pending'", self.html)
        self.assertNotIn("if (!this.hasCurrentGeneratedPlan()) return 'problem'", self.html)

    def test_default_dates_use_browser_local_date_instead_of_utc_date(self):
        self.assertIn("function localDate()", self.html)
        self.assertIn("tDate: localDate()", self.html)
        self.assertNotIn("factDateInputValue", self.html)

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

    def test_complete_plan_uses_the_only_frozen_server_endpoint(self):
        self.assertIn("async generateCompletePlan()", self.html)
        start = self.html.index("async generateCompletePlan()")
        end = self.html.index("async loadPlan(", start)
        body = self.html[start:end]
        self.assertIn("await this.saveFundingContext()", body)
        self.assertIn("fetch('/api/plan/generate', { method: 'POST' })", body)
        self.assertNotIn("response.status === 404", body)
        self.assertNotIn("size-orders", self.html)
        self.assertIn("componentErrors: { cb: '', stock: '' }", self.html)
        self.assertNotIn("async generateCompletePlanLegacy()", self.html)
        self.assertIn("请通过“生成完整计划”一并冻结调拨、定价和订单。", self.html)

    def test_complete_plan_error_lists_the_missing_plan_date_prices(self):
        self.assertIn("Object.entries(detail.missing || {})", self.html)
        self.assertIn("缺少计划日收盘价", self.html)

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
        self.assertIn("存在资金调拨安排", self.html)
        self.assertNotIn("需调拨 ${this.fundingActions().length} 笔", self.html)
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
        self.assertIn("isReviewableExecutionPlan()", self.html)
        self.assertIn('<template x-if="isReviewableExecutionPlan() || (isHistoricalPlanSnapshot() && snapshotExpanded)">', self.html)
        self.assertNotIn('x-show="[\'action\', \'no-action\'].includes(planState())" class="ark-panel plan-action-summary', self.html)

    def test_plan_page_exposes_read_only_history_snapshots(self):
        self.assertIn('aria-label="历史计划"', self.html)
        self.assertIn("fetch('/api/plan/generated/history')", self.html)
        self.assertIn("async loadHistoricalPlan(planId)", self.html)
        self.assertIn("仅供追溯，不应据此执行交易", self.html)

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
        self.assertIn("['waits_for_funds', 'blocked'].includes(funding?.state)", self.html)

    def test_account_trading_plans_use_real_accounts_and_complete_cash_equation(self):
        self.assertIn("账户交易计划", self.html)
        self.assertIn('x-for="(plan, accountIndex) in accountTradingPlans()"', self.html)
        self.assertIn("plan.account_name", self.html)
        self.assertIn("plan.strategy_name", self.html)
        self.assertIn("fundingStateText(plan.funding)", self.html)
        self.assertIn("accountTradeDateText(plan)", self.html)
        self.assertIn("if (!this.isSelectedHistoricalPlan()) return ''", self.html)
        self.assertIn("funding.display_date !== this.executionDate()", self.html)
        self.assertNotIn("plan.trade_summary.sell_count", self.html)
        self.assertNotIn("plan.trade_summary.buy_count", self.html)
        self.assertIn("fundingPrerequisiteText(plan)", self.html)
        self.assertNotIn("accountFundingSummary(plan)", self.html)
        self.assertIn("accountActionSummary(plan)", self.html)
        self.assertIn("plan.cash.starting_available", self.html)
        self.assertIn("plan.cash.transfer_in", self.html)
        self.assertIn("plan.cash.transfer_out", self.html)
        self.assertIn("plan.cash.expected_sell", self.html)
        self.assertIn("plan.cash.expected_buy", self.html)
        self.assertIn("plan.cash.expected_ending", self.html)
        self.assertIn("计划后预计资金余额", self.html)
        self.assertIn('class="plan-cash-audit"', self.html)
        self.assertIn("执行说明与资金估算", self.html)
        self.assertIn('class="plan-account-card"', self.html)
        self.assertNotIn('class="ark-panel plan-transfer-panel', self.html)
        self.assertNotIn('class="ark-panel plan-order-panel', self.html)

        account_summary_start = self.html.index('class="plan-account-heading"')
        account_summary_end = self.html.index('class="plan-account-detail"', account_summary_start)
        account_summary = self.html[account_summary_start:account_summary_end]
        self.assertNotIn('class="plan-cash-equation"', account_summary)
        self.assertNotIn("起始可用资金", account_summary)

    def test_funding_plan_precedes_collapsed_account_plans(self):
        funding = self.html.index('aria-label="资金调拨计划"')
        accounts = self.html.index('aria-label="账户交易计划"')
        self.assertLess(funding, accounts)
        self.assertIn('name="account-trading-plan"', self.html)
        self.assertIn(':open="expandedAccountId === plan.account_id"', self.html)
        self.assertIn('@toggle="handleAccountPlanToggle($event, plan.account_id)"', self.html)
        self.assertNotIn('<details class="plan-account-card" open', self.html)

    def test_expanded_account_plan_keeps_context_and_execution_guardrails(self):
        self.assertNotIn('class="plan-account-folio"', self.html)
        self.assertIn('x-show="expandedAccountId"', self.html)
        self.assertIn('aria-label="账户交易明细导航"', self.html)
        self.assertIn('position: sticky;', self.html)
        self.assertIn("closeAccountPlan(plan.account_id)", self.html)
        self.assertIn("plan.execution_guardrails?.price_basis_date", self.html)
        self.assertIn("plan.execution_guardrails?.rules", self.html)
        self.assertIn("guardrailText(rule)", self.html)
        self.assertIn("实际成交价必须严格低于", self.html)
        self.assertIn("单只股票不超过", self.html)
        self.assertIn("参考价（非限价）", self.html)
        self.assertIn("rule.max_price", self.html)
        self.assertNotIn("plan-trade-price-cap", self.html)
        self.assertNotIn("'· < ' + Number(order.max_execution_price).toFixed(3)", self.html)
        self.assertIn("switchAccountPlan(", self.html)
        self.assertNotIn("上一个账户", self.html)
        self.assertNotIn("下一个账户", self.html)
        trade_table = self.html.index('class="plan-trade-section"')
        cash_audit = self.html.index('class="plan-cash-audit"', trade_table)
        guardrails = self.html.index('class="plan-execution-guardrails"', cash_audit)
        cash_equation = self.html.index('class="plan-cash-equation"', guardrails)
        self.assertLess(trade_table, cash_audit)
        self.assertLess(cash_audit, guardrails)
        self.assertLess(guardrails, cash_equation)

    def test_funding_plan_is_one_compact_table_with_amount_next_to_route(self):
        self.assertIn('class="plan-funding-table"', self.html)
        self.assertIn('class="plan-funding-action"', self.html)
        self.assertNotIn('class="plan-funding-group"', self.html)
        self.assertIn(">调拨路线<", self.html)
        self.assertIn(">调拨金额<", self.html)
        self.assertIn(">资金可用时间<", self.html)
        self.assertNotIn(">用途与影响<", self.html)
        self.assertNotIn('class="plan-funding-impact"', self.html)
        self.assertNotIn("fundingGroupInstruction(group)", self.html)
        route = self.html.index(">调拨路线<")
        amount = self.html.index(">调拨金额<")
        available = self.html.index(">资金可用时间<")
        self.assertLess(route, amount)
        self.assertLess(amount, available)

    def test_account_plan_expansion_is_single_and_restorable_from_url(self):
        self.assertIn("expandedAccountId: new URL(window.location.href).searchParams.get('planAccount')", self.html)
        self.assertIn("syncExpandedAccountUrl()", self.html)
        self.assertIn("url.searchParams.set('planAccount', this.expandedAccountId)", self.html)
        self.assertIn("url.searchParams.delete('planAccount')", self.html)

    def test_past_execution_window_is_not_presented_as_executable(self):
        self.assertIn("executionWindowState()", self.html)
        self.assertIn("if (executionDate < localDate()) return 'past'", self.html)
        self.assertIn("if (this.executionWindowState() === 'past') return 'expired'", self.html)
        self.assertIn("执行窗口已过", self.html)
        self.assertIn("停止作为当前建议展示", self.html)

    def test_future_execution_window_is_reviewable_but_not_called_today_action(self):
        self.assertIn("if (this.executionWindowState() === 'future') return 'future-action'", self.html)
        self.assertIn("title: '计划待执行'", self.html)
        self.assertIn("未到执行窗口不要交易", self.html)
        self.assertIn("['action', 'future-action'].includes(this.planState())", self.html)

    def test_account_plan_details_group_actions_once_above_execution_rows(self):
        self.assertIn("accountTradeOrders(plan)", self.html)
        self.assertIn('x-for="group in accountTradeActionOverview(plan)"', self.html)
        self.assertIn('x-for="order in group.orders"', self.html)
        self.assertIn("actionRank = { SELL: 0, TRIM: 1, BUY: 2, ADD: 3 }", self.html)
        self.assertIn("left?.execution_priority", self.html)
        self.assertIn("right?.execution_priority", self.html)
        self.assertIn("accountTradeGroups(plan)", self.html)
        self.assertIn("direction: 'SELL', label: '卖出'", self.html)
        self.assertIn("direction: 'BUY', label: '买入'", self.html)
        self.assertIn("actions: ['SELL', 'TRIM']", self.html)
        self.assertIn("actions: ['BUY', 'ADD']", self.html)
        self.assertIn("positionActionText(action)", self.html)
        self.assertNotIn("tradeSideText(action)", self.html)
        self.assertNotIn("tradeSideClass(action)", self.html)
        self.assertNotIn('x-text="tradeSideText(order.action)"', self.html)
        self.assertNotIn(':class="tradeSideClass(order.action)"', self.html)
        self.assertNotIn("tradeInstructionText(order)", self.html)
        self.assertNotIn("tradeDirectionText(order?.action)", self.html)
        self.assertIn("order.code", self.html)
        self.assertIn("order.name", self.html)
        self.assertIn("order.current_quantity", self.html)
        self.assertIn("order.target_quantity", self.html)
        self.assertIn("order.quantity", self.html)
        self.assertIn("order.unit", self.html)
        self.assertIn("order.reference_price", self.html)
        self.assertIn("order.estimated_amount", self.html)
        self.assertNotIn(">顺序<", self.html)
        self.assertNotIn(">仓位动作<", self.html)
        self.assertIn(">证券代码<", self.html)
        self.assertIn(">证券名称<", self.html)
        self.assertNotIn(">证券代码与名称<", self.html)
        self.assertNotIn(">调仓动作<", self.html)
        self.assertIn(">委托数量<", self.html)
        self.assertIn(">预计金额<", self.html)
        self.assertNotIn('data-label="调仓动作"', self.html)
        self.assertIn('data-label="委托数量"', self.html)
        self.assertIn('data-label="预计金额"', self.html)
        self.assertNotIn(">仓位<", self.html)
        self.assertNotIn(">买卖<", self.html)
        self.assertNotIn(">交易数量<", self.html)
        self.assertNotIn('class="plan-trade-action"', self.html)
        self.assertNotIn('class="plan-trade-action-badge"', self.html)
        self.assertNotIn('class="plan-trade-action-col"', self.html)
        self.assertNotIn('class="plan-trade-side"', self.html)
        self.assertIn('class="plan-trade-name-col"', self.html)
        self.assertNotIn('class="plan-trade-side-col"', self.html)
        self.assertIn('class="plan-trade-quantity-col"', self.html)
        self.assertIn('class="plan-trade-price-col"', self.html)
        self.assertIn('class="plan-trade-amount-col"', self.html)
        self.assertIn(".plan-trade-table {\n    width: 100%;", self.html)
        self.assertIn(".plan-trade-name-col { width: 184px; }", self.html)
        self.assertIn('tradeActionToneClass(group.action)', self.html)
        self.assertNotIn('tradeActionGroupStartClass(', self.html)
        self.assertIn('class="plan-trade-quantity"', self.html)
        self.assertIn(':title="positionRouteText(order)"', self.html)
        self.assertIn("positionRouteText(order)", self.html)
        self.assertIn("仓位语义：", self.html)
        self.assertNotIn('class="plan-trade-position-route"', self.html)
        self.assertIn('class="plan-trade-position"', self.html)
        self.assertIn(">参考价<", self.html)
        self.assertNotIn('data-label="顺序"', self.html)
        self.assertNotIn('data-label="仓位动作"', self.html)
        self.assertNotIn('x-for="group in accountTradeGroups(plan)"', self.html)
        self.assertIn('class="plan-trade-group-row"', self.html)
        self.assertIn('colspan="5"', self.html)
        self.assertIn("group.label + ' · ' + group.count + ' 笔'", self.html)
        self.assertIn("SELL: '清仓'", self.html)
        self.assertIn("TRIM: '减仓'", self.html)
        self.assertIn("BUY: '建仓'", self.html)
        self.assertIn("ADD: '加仓'", self.html)
        self.assertNotIn("清仓卖出", self.html)
        self.assertNotIn("新买入", self.html)
        self.assertNotIn("phaseLabel(", self.html)
        self.assertNotIn("phaseInstruction(", self.html)
        self.assertNotIn("卖出阶段", self.html)
        self.assertNotIn("买入阶段", self.html)
        self.assertNotIn("executionStepLabel(", self.html)
        self.assertNotIn('colspan="6"', self.html)

    def test_account_plan_trade_groups_replace_duplicate_summary_cards_and_row_badges(self):
        self.assertNotIn('class="plan-trade-summary"', self.html)
        self.assertNotIn('aria-label="交易动作汇总"', self.html)
        self.assertIn('x-for="group in accountTradeActionOverview(plan)"', self.html)
        self.assertIn('class="plan-trade-group-main"', self.html)
        self.assertIn('class="plan-trade-group-meta"', self.html)
        self.assertIn("accountTradeActionOverview(plan)", self.html)
        self.assertIn("amount_label: '预计回笼'", self.html)
        self.assertIn("amount_label: '预计占用'", self.html)
        self.assertIn("orders: matched", self.html)
        self.assertNotIn("tradeActionGroupStartClass(previousAction, action)", self.html)
        self.assertNotIn("is-action-group-start", self.html)
        self.assertNotIn('class="plan-trade-action-badge"', self.html)

    def test_execution_document_uses_compact_desktop_ledger_density(self):
        self.assertIn("@media (min-width: 761px)", self.html)
        self.assertIn(".plan-execution-index-item {\n      grid-template-columns: 22px", self.html)
        self.assertIn(".plan-step-body { padding: 0 16px 10px 54px; }", self.html)
        self.assertIn(".plan-account-card[open] > summary { padding: 9px 16px; }", self.html)
        self.assertIn(".plan-trade-row td { padding: 4px 8px; line-height: 1.2; }", self.html)

    def test_account_plan_summary_exposes_the_trade_detail_toggle(self):
        self.assertIn("plan.account_name + '交易计划'", self.html)
        self.assertIn('class="plan-account-detail-toggle"', self.html)
        self.assertIn("'查看 ' + accountTradeOrders(plan).length + ' 笔交易'", self.html)
        self.assertIn("收起交易明细", self.html)
        self.assertIn('.plan-account-card[open] .plan-account-toggle-open', self.html)
        self.assertIn(".plan-account-card[open] .plan-account-detail-toggle { display: none; }", self.html)
        self.assertNotIn('class="plan-account-open-summary"', self.html)
        self.assertIn("accountExecutionSummary(plan)", self.html)
        self.assertIn("先确认 ${this.fmtMoney(funding.transfer_in)} 已调入", self.html)
        self.assertIn("${group.label} ${group.orders.length} 笔", self.html)
        self.assertNotIn("计划后资金", self.html)
        self.assertNotIn("accountPlanFolioTitle(plan)", self.html)
        self.assertNotIn("display_ordinal: `${index + 1}/${plans.length}`", self.html)
        self.assertNotIn("accountPlanFolioMeta(plan)", self.html)
        self.assertNotIn("plan-account-folio-meta", self.html)
        self.assertNotIn("截图模式", self.html)

    def test_new_account_trade_rows_remain_compact_mobile_records(self):
        self.assertIn('class="plan-trade-row"', self.html)
        self.assertIn('grid-template-areas:', self.html)
        self.assertNotIn('"action action action"', self.html)
        self.assertIn('"code name price amount"', self.html)
        self.assertIn('"position position price amount"', self.html)
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
        self.assertIn("hasSavedGeneratedPlan()", self.html)
        self.assertIn("isReadablePlanSnapshot()", self.html)
        self.assertIn("已保存计划快照", self.html)
        self.assertIn("if (!this.isReadablePlanSnapshot()) return null", self.html)
        self.assertIn("planState()", self.html)
        self.assertIn("planStateTitle()", self.html)
        self.assertIn("今日需要操作", self.html)
        self.assertIn("本次无需操作", self.html)
        self.assertIn("计划待执行", self.html)
        self.assertIn("计划待生成", self.html)
        self.assertIn("无法确认计划状态", self.html)
        self.assertIn("无法确认账户数据", self.html)
        self.assertIn("readinessError: ''", self.html)
        self.assertIn("待更新账户数据", self.html)
        self.assertIn("计划已失效", self.html)
        self.assertIn("计划生成失败", self.html)
        self.assertIn("executionReadModel()", self.html)
        self.assertIn("fundingPlan()", self.html)
        self.assertIn("hasExecutionPlans()", self.html)
        self.assertIn("aria-live=\"polite\"", self.html)
        self.assertIn('class="plan-masthead-state"', self.html)
        self.assertNotIn('class="plan-masthead-revision"', self.html)
        self.assertIn("'当前计划 · ' + planRevisionText()", self.html)
        self.assertIn('class="plan-masthead-side"', self.html)
        self.assertNotIn('class="plan-execution-status"', self.html)
        self.assertNotIn("planReady && isReviewableExecutionPlan()\"", self.html)
        self.assertIn("planReady && !isReviewableExecutionPlan()", self.html)
        self.assertIn('<template x-if="isReviewableExecutionPlan() || (isHistoricalPlanSnapshot() && snapshotExpanded)">', self.html)
        self.assertIn("if (!this.isReadablePlanSnapshot()) return null", self.html)
        self.assertIn("planTimelineText()", self.html)
        self.assertIn("planRevisionText()", self.html)
        self.assertIn("failureStageStrategy(stage)", self.html)
        self.assertIn("if (stage === 'stock_orders') return 'stock'", self.html)
        self.assertIn("const failedStrategy = this.latestGeneration?.status === 'failed'", self.html)
        self.assertLess(
            self.html.index("if (failedStrategy === strategy) return { state: 'failed', text: '生成失败' }"),
            self.html.index("if (section?.orders?.length) return { state: 'ready', text: '订单已生成' }"),
        )
        status = self.html.index('class="plan-masthead-state"')
        decision = self.html.index('class="plan-decision-hero')
        actions = self.html.index('class="plan-execution-dossier')
        readiness = self.html.index('class="ark-panel plan-readiness-panel')
        self.assertLess(status, actions)
        self.assertLess(decision, actions)
        self.assertLess(actions, readiness)
        self.assertIn("正在生成完整计划…", self.html)
        self.assertNotIn("generationStage + '…'", self.html)

    def test_plan_page_has_restorable_focus_view_and_stable_loading_state(self):
        self.assertIn("planFocus", self.html)
        self.assertIn("toggleFocusMode()", self.html)
        self.assertIn("plan-focus-mode", self.html)
        self.assertIn("正在确认最新计划…", self.html)
        self.assertIn('x-show="planReady && !isReviewableExecutionPlan()"', self.html)
        self.assertIn("await this.loadWorkStatus()", self.html)
        self.assertNotIn("await this.loadWorkStatus(true)\n          await this.refreshFullPlan(false)", self.html)

    def test_plan_generation_gate_guides_each_server_state_without_duplicate_submission(self):
        self.assertIn("canGenerateCompletePlan()", self.html)
        self.assertIn(':disabled="!canGenerateCompletePlan()"', self.html)
        self.assertIn("if (!this.canGenerateCompletePlan()) return", self.html)
        self.assertIn("async retryPlanReadiness()", self.html)
        self.assertIn("重新读取计划输入就绪度", self.html)
        self.assertIn("goToAccountNeedingUpdate()", self.html)
        self.assertIn("navigateToAccount(accountId)", self.html)
        self.assertIn("@account-focus.window=\"focusAccount($event.detail)\"", self.html)

if __name__ == "__main__":
    unittest.main()
