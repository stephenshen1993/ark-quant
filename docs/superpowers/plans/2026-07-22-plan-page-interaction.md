# 完整计划页面交互 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把交易页改为一个主按钮生成完整计划、按需显示输入并始终明确展示三个独立结果的计划工具。

**Architecture:** 保持现有账户、资金调拨、榜单和订单 API 独立，由 Alpine 页面编排现有接口。页面内部把“保存条件”“生成榜单”“计算订单”拆成可失败的私有步骤，对外只提供一个常规主流程；局部错误按策略隔离。

**Tech Stack:** FastAPI、SQLite、Alpine.js、Tailwind CSS、Python `unittest`、Node.js 语法检查。

## Global Constraints

- 系统只生成计划，不执行交易或调拨。
- 不修改投资参数、股票策略或可转债策略规则。
- 资金调拨与两个榜单保持独立，只在完整计划页面汇合。
- 未核验 B 额度不得等同于已核验不可申购。
- 不触碰或暂存 `data/ark_quant.db.temperature.lock`。

---

### Task 1: 页面交互合同测试

**Files:**
- Create: `tests/test_trading_page.py`
- Test: `app/static/index.html`

**Interfaces:**
- Produces: 页面文本、条件显隐和编排函数的静态合同。

- [x] **Step 1: 写失败测试**

测试断言：常规页面仅出现“生成完整计划”；旧文案“保存并生成计划”“运行策略”和固定编号不存在；HTML 包含 `needsContribution()`、`needsBCheck()`、`b_purchase_status`、`generateCompletePlan()`、`transferStatusText()`。

- [x] **Step 2: 验证测试因旧页面失败**

Run: `.venv/bin/python -m unittest tests.test_trading_page -v`

Expected: 旧按钮文本和缺少的新交互函数导致断言失败。

### Task 2: 单一主流程与渐进输入

**Files:**
- Modify: `app/static/index.html`
- Test: `tests/test_trading_page.py`

**Interfaces:**
- `generateCompletePlan() -> Promise<void>`：保存条件、补榜单、算订单、刷新计划。
- `saveFundingContext() -> Promise<void>`：按当前检查类型规范化并保存上下文。
- `generateRanking(strategy) -> Promise<void>`：生成指定榜单并刷新页面数据。
- `generateOrders(strategy) -> Promise<void>`：生成指定订单，失败时抛错。

- [x] **Step 1: 实现条件显隐和 B 状态规范化**

为月度本金、B 状态、额度及核验事实增加 Alpine 条件。初始化时根据现有三项 B 事实推导 `unchecked`、`unavailable`、`available`。

- [x] **Step 2: 实现完整计划编排**

编排先保存上下文并读取计划；只为缺失的同日榜单调用榜单接口；随后分别重新计算两类订单；任何策略局部错误写入 `componentErrors[strategy]` 并继续另一策略。

- [x] **Step 3: 验证合同测试通过**

Run: `.venv/bin/python -m unittest tests.test_trading_page -v`

Expected: all tests pass.

### Task 3: 就绪状态与稳定结果卡

**Files:**
- Modify: `app/static/index.html`
- Modify: `tests/test_trading_page.py`

**Interfaces:**
- `readinessItems() -> Array<{label, state, text}>`
- `transferStatusText() -> string`
- `strategyStatus(strategy) -> {state, text}`

- [x] **Step 1: 增加失败测试**

断言页面始终渲染资金调拨、转债计划、股票计划三张卡；存在“无需调拨”状态和折叠的“高级操作”。

- [x] **Step 2: 实现状态区和三张结果卡**

移除条件隐藏和固定编号。保留既有榜单/订单明细表，只替换标题、状态和操作层级。

- [x] **Step 3: 运行定向测试和 JS 语法检查**

Run: `.venv/bin/python -m unittest tests.test_trading_page tests.test_api_plans tests.test_api_accounts -v`

Run: `node --check <从 index.html 提取的 script>`

Expected: all tests pass; Node exits 0.

### Task 4: 浏览器复核与文档收口

**Files:**
- Modify: `docs/工程/当前实现状态.md`
- Modify: `docs/策略/多因子可转债策略研究看板.md`

- [x] **Step 1: 浏览器复核**

检查 A 内部、月度、B 恢复三种类型的字段显隐；确认只有一个主按钮、三张卡始终存在、无固定断号和“运行策略”歧义。

- [x] **Step 2: 更新实现事实和研究看板**

仅记录页面编排已经落地且不改变策略研究、投资规则和真实执行边界。

- [x] **Step 3: 全量验证**

Run: `git diff --check`

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: no whitespace errors and all tests pass.

- [x] **Step 4: 检查工作区**

Run: `git status --short --branch`

Expected: 不提交、不暂存；保留既有用户改动，忽略 `data/ark_quant.db.temperature.lock`。
