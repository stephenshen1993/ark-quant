# 资金调拨层实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ark-quant 按已确认的 A/B/C 体系规则生成安全、可审计且不预支资金的资金调拨计划。

**Architecture:** 新建纯函数规划器，输入已确认账户事实与本次人工检查上下文，输出完整理想目标、部分可执行方案和即时执行动作。数据库/账户 API 保存本次检查上下文，计划路由只适配规划结果；股票和转债订单逻辑继续独立。

**Tech Stack:** Python 3、FastAPI、Pydantic、SQLite、`unittest`、Alpine.js。

## Global Constraints

- 体系真源优先于代码；不得修改账户内股票或可转债的选标的规则。
- 所有即时现金只能来自事实快照；不预支卖出、赎回、未来额度或新增本金。
- 资金调拨只经现金池；不建立执行状态机或跨计划待办。
- B 的申购事实缺失时必须按不可买处理。
- 旧 API 字段保留兼容，新增结构化字段，不以静默回退掩盖输入或规则错误。

---

### Task 1: 纯资金调拨规划器

**Files:**
- Create: `portfolio_rebalance.py`
- Create: `tests/test_portfolio_rebalance.py`

**Interfaces:**
- Produces: `build_fund_transfer_plan(account: dict, context: dict, qualified_cb_count: int | None) -> dict`
- Produces: `PlanValidationError(ValueError)`，包含可展示的 `code` 与 `message`。

- [ ] **Step 1: 写入失败测试：目标守恒和 A 内部映射**

```python
from portfolio_rebalance import build_fund_transfer_plan

def test_neutral_targets_keep_top_level_and_a_internal_conserved():
    result = build_fund_transfer_plan(
        {"stock_total": 45000, "bond_total": 30000, "cash_pool": 10000,
         "changqian_total": 15000, "overseas_total": 20000},
        {"temperature": 50, "check_type": "a_internal", "cash_available": 10000},
        qualified_cb_count=20,
    )
    assert result["top_level"]["targets"]["A"] == 78000
    assert result["top_level"]["targets"]["B"] == 24000
    assert result["a_internal"]["base_targets"]["stock"] == 45000
    assert round(sum(result["a_internal"]["final_targets"].values()), 2) == 85000
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m unittest tests.test_portfolio_rebalance -v`

Expected: `ModuleNotFoundError: No module named 'portfolio_rebalance'`.

- [ ] **Step 3: 实现最小纯规划器**

```python
def build_fund_transfer_plan(account, context, qualified_cb_count):
    temperature = _temperature(context["temperature"])
    current = _current_amounts(account)
    top_level = _top_level(current, temperature, context)
    a_internal = _a_internal(current, top_level["executed_deltas"]["A"], temperature,
                             qualified_cb_count)
    return {"top_level": top_level, "a_internal": a_internal}
```

Implement private helpers for the documented A/B/C mapping, A three-anchor interpolation, `A_exec`, and the convertible-bond safety valve. If `qualified_cb_count is None`, preserve top-level results but mark A internal as paused and emit no immediate stock/bond cash outflow. Round only serialized monetary values, never intermediate proportions.

- [ ] **Step 4: 运行测试至通过**

Run: `.venv/bin/python -m unittest tests.test_portfolio_rebalance -v`

Expected: all Task 1 tests pass.

### Task 2: 顶层触发、部分执行和现金安全

**Files:**
- Modify: `portfolio_rebalance.py`
- Modify: `tests/test_portfolio_rebalance.py`

**Interfaces:**
- `top_level["ideal_actions"]` 保存半带宽前的完整方案。
- `top_level["executed_actions"]` 只保留本次可成立的调入/调出。
- `cash["immediate_outflow"] <= context["cash_available"]` 始终成立。

- [ ] **Step 1: 写入失败测试：月度不卖老仓、季度半带宽、B 受限**

```python
def test_monthly_contribution_never_sells_existing_top_level_holdings():
    result = build_fund_transfer_plan(overweight_a_account, {
        "temperature": 50, "check_type": "monthly_contribution",
        "cash_available": 7500, "new_contribution": 7500,
        "b_purchase_limit": 0,
    })
    assert all(a["amount"] >= 0 for a in result["top_level"]["executed_actions"])

def test_quarterly_b_unavailable_removes_b_inflow_and_scales_sources():
    result = build_fund_transfer_plan(overweight_a_account, {
        "temperature": 50, "check_type": "quarterly", "cash_available": 0,
        "b_purchase_limit": 0,
    })
    assert not any(a["target"] == "B" and a["amount"] > 0 for a in result["top_level"]["executed_actions"])
    assert sum(a["amount"] for a in result["top_level"]["outflows"]) == 0
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m unittest tests.test_portfolio_rebalance -v`

Expected: failures because trigger/constraint handling is absent.

- [ ] **Step 3: 实现触发和约束**

Implement `monthly_contribution` using only `min(new_contribution, cash_available)`, `quarterly`/`ad_hoc` using strict outer-band triggering and common-ratio half-band repair, and `b_recovery` using actual cash only. For B, classify actual limit as unavailable/partial/normal; deletion of B inflow must proportionally scale only existing source outflows. Convert all approved actions into cash-pool-star routes.

- [ ] **Step 4: 运行测试至通过**

Run: `.venv/bin/python -m unittest tests.test_portfolio_rebalance -v`

Expected: all Task 1–2 tests pass.

### Task 3: 保存检查上下文并接入计划 API

**Files:**
- Modify: `datasource/db.py`
- Modify: `app/routers/accounts.py`
- Modify: `app/routers/plans.py`
- Modify: `tests/test_db.py`
- Modify: `tests/test_api_accounts.py`
- Modify: `tests/test_api_plans.py`

**Interfaces:**
- `POST /api/account/context` 接受 `check_type`、`new_contribution`、`b_purchase_limit`、`b_purchase_checked_at`、`b_purchase_source`。
- `GET /api/plan/transfer` 返回 `fund_transfer`，并保留 `targets`、`transfer_steps`、`transfer_deltas`。

- [ ] **Step 1: 写入失败测试：上下文往返和计划 API 安全停止**

```python
def test_context_preserves_b_purchase_facts():
    db.insert_account_context("2026-07-21", 50, check_type="quarterly",
        b_purchase_limit=2000, b_purchase_checked_at="2026-07-21T15:30:00",
        b_purchase_source="manual")
    assert db.get_current_account_summary()["check_type"] == "quarterly"

def test_transfer_plan_requires_current_overseas_snapshot():
    db._TEST_CONN.execute("DELETE FROM account_value_snapshots WHERE account_id='overseas'")
    response = self.client.get("/api/plan/transfer")
    assert response.status_code == 409
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m unittest tests.test_db tests.test_api_accounts tests.test_api_plans -v`

Expected: new tests fail because fields and full top-level validation do not exist.

- [ ] **Step 3: 最小数据库/API 实现**

Add nullable context columns through idempotent migration. Validate finite nonnegative amounts and ISO timestamps. Require an overseas snapshot for complete transfer planning. Build once with `build_fund_transfer_plan`; derive compatibility `targets`, `transfer_deltas` and human-readable star `transfer_steps` from its immediate actions. Remove the broad `except Exception` that currently hides planner failures.

- [ ] **Step 4: 运行 API 与数据库测试至通过**

Run: `.venv/bin/python -m unittest tests.test_db tests.test_api_accounts tests.test_api_plans -v`

Expected: all listed tests pass.

### Task 4: 计划页面输入与结构化展示

**Files:**
- Modify: `app/static/index.html`
- Modify: `tests/test_api_plans.py`

**Interfaces:**
- 账户页可保存本次检查类型、新增本金和 B 的单次申购事实。
- 交易页显示顶层目标/偏离、执行受限原因和星型调拨；不存在任何“待到账后继续”的按钮。

- [ ] **Step 1: 写入 API 合同失败测试**

```python
def test_transfer_plan_exposes_structured_fund_transfer_result():
    data = self.client.get("/api/plan/transfer").json()
    assert {"top_level", "a_internal", "cash"} <= data["fund_transfer"].keys()
```

- [ ] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m unittest tests.test_api_plans.TestPlansApi.test_transfer_plan_exposes_structured_fund_transfer_result -v`

Expected: assertion failure before API integration.

- [ ] **Step 3: 最小页面接入**

Add compact fields to the existing account/plan flow and render the new plan object. Preserve old step list as an execution summary. Show B unavailable/partial status, unfilled gaps and input errors explicitly; never present expected redemption proceeds as available cash.

- [ ] **Step 4: 运行完整回归**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 5: 文档与可审计交付

**Files:**
- Modify: `docs/工程/当前实现状态.md`
- Modify: `docs/体系/仓位与再平衡规则.md`
- Modify: `docs/策略/多因子可转债策略研究看板.md`

- [ ] **Step 1: 将实现事实与规则状态分开记录**

Update engineering status with the implemented planner scope and residual order-sizing gap. Do not edit target percentages or strategy parameters. Update the research board only to state that account-level convertible-bond research continues independently of the new funding layer.

- [ ] **Step 2: 验证文档与差异**

Run: `git diff --check && .venv/bin/python -m unittest discover -s tests -v`

Expected: no whitespace errors and all tests pass.

- [ ] **Step 3: 提交检查点（仅在用户再次授权提交时）**

Run: `git status --short --branch`

Expected: stage only the planner, API, UI, test and documentation files; never add `data/ark_quant.db.temperature.lock`.
