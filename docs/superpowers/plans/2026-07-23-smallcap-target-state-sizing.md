# 小市值目标态定额 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让小市值 CLI 与计划 API 共用一个符合正式规则的目标态定额器。

**Architecture:** 纯函数模块拥有容量、整手、集中度、普通门槛和余现修补的全部决策。API 与 CLI 仅负责准备输入、调用、持久化和展示。

**Tech Stack:** Python 3.9, pandas, FastAPI, unittest.

## Global Constraints

- 不修改 Top 20、因子、轮动频率或体系级资金调拨。
- 固定 100 股整手；单股硬上限为预算的 `10%`。
- 普通净订单门槛为 `max(1000, B / 20 * 10%)`。
- 排名/风险退出、清尾仓和超过 10% 的集中度减持不受普通门槛限制。
- 容量冲突和报价缺失不能写入订单，不能以第 21 名替代。

---

### Task 1: 建立共享目标态定额器

**Files:**

- Create: `strategies/stock_smallcap/target_sizing.py`
- Create: `tests/test_stock_target_sizing.py`

**Interfaces:**

- Consumes: 排名 DataFrame（`stock_code`, `stock_name`, `rank`）、持仓 DataFrame（`stock_code`, `stock_name`, `shares`）、预算 `B`、报价字典。
- Produces: `size_target_state(...) -> (order_sheet, summary)`；失败时抛出 `SizingError(code, message, details)`。

- [ ] **Step 1: 写失败测试**

```python
def test_capacity_requires_one_lot_for_every_top_twenty():
    with self.assertRaisesRegex(SizingError, "CAPACITY_CONFLICT"):
        size_target_state(rankings(20), empty_positions(), 19_000, prices(20, 10))

def test_required_exit_bypasses_ordinary_order_threshold():
    sheet, _ = size_target_state(rankings(20), positions("999999", 100), 40_000, prices_with_exit())
    self.assertEqual(row(sheet, "999999")["action"], "SELL")
```

- [ ] **Step 2: 运行并确认失败**

Run: `./.venv/bin/python -m unittest tests.test_stock_target_sizing -v`

Expected: import error because the module does not yet exist.

- [ ] **Step 3: 实现纯定额器**

```python
LOT = 100
TARGET_COUNT = 20
MAX_SINGLE_WEIGHT = 0.10

def size_target_state(rankings, positions, budget, prices):
    # validate 20 targets and quotes; reject capacity conflicts
    # create capped full target lots; merge to one net order per code
    # filter ordinary orders; then repair eligible buy deficits with residual cash
    ...
```

- [ ] **Step 4: 增补并运行完整契约测试**

覆盖报价缺失、100 股取整、10% 上限、动态门槛、集中度减持、余现修补和非负现金。

Run: `./.venv/bin/python -m unittest tests.test_stock_target_sizing -v`

Expected: PASS.

### Task 2: 将计划 API 改为共享定额器调用方

**Files:**

- Modify: `app/routers/plans.py:440-526`
- Modify: `tests/test_api_plans.py`

**Interfaces:**

- Consumes: `size_target_state(rankings, positions, cash, prices)`。
- Produces: 原有订单响应；`SizingError` 映射为 HTTP 409，且不持久化订单。

- [ ] **Step 1: 写 API 容量冲突失败测试**

```python
def test_size_stock_orders_rejects_capacity_conflict_before_persisting(self):
    response = self.client.post("/api/plan/stock/size-orders")
    self.assertEqual(response.status_code, 409)
    self.assertEqual(response.json()["detail"]["code"], "CAPACITY_CONFLICT")
    self.assertEqual(db.get_orders("stock", "2026-06-29"), [])
```

- [ ] **Step 2: 运行失败测试**

Run: `./.venv/bin/python -m unittest tests.test_api_plans.TestPlansApi.test_size_stock_orders_rejects_capacity_conflict_before_persisting -v`

Expected: FAIL because legacy sizing silently emits partial targets.

- [ ] **Step 3: 替换 API 的本地 rebalance 生成与固定 1000 元调用**

保留计划日期、事实输入窗口、报价检查、持久化和现有响应字段；用共享定额器的订单与摘要替代 `min_trade_value=1000` 的旧分支。

- [ ] **Step 4: 运行 API 计划测试**

Run: `./.venv/bin/python -m unittest tests.test_api_plans -v`

Expected: PASS.

### Task 3: 将命令行改为共享定额器调用方

**Files:**

- Modify: `strategies/stock_smallcap/size_orders.py`
- Modify: `tests/test_stock_smallcap.py`

**Interfaces:**

- Consumes: 同一个 `size_target_state` 接口。
- Produces: 现有 CSV 字段和控制台输出，增加正式门槛及告警显示。

- [ ] **Step 1: 写入口一致性失败测试**

```python
def test_cli_adapter_matches_shared_target_sizer(self):
    actual, _ = size_orders.size_rebalance(rankings(20), positions(), 200_000, prices())
    expected, _ = size_target_state(rankings(20), positions(), 200_000, prices())
    pd.testing.assert_frame_equal(actual, expected)
```

- [ ] **Step 2: 运行并确认失败**

Run: `./.venv/bin/python -m unittest tests.test_stock_smallcap.StockSmallCapTests.test_cli_adapter_matches_shared_target_sizer -v`

Expected: FAIL because the CLI has a separate allocator.

- [ ] **Step 3: 用兼容适配器替换旧分配循环**

`size_rebalance` 保留为薄兼容函数，直接委托共享定额器；移除 `--min-trade-value` 覆盖入口，展示共享摘要中的门槛与告警。

- [ ] **Step 4: 运行小市值和共享定额测试**

Run: `./.venv/bin/python -m unittest tests.test_stock_smallcap tests.test_stock_target_sizing -v`

Expected: PASS.

### Task 4: 端到端回归与工程事实更新

**Files:**

- Modify: `docs/工程/当前实现状态.md`

- [ ] **Step 1: 记录已实现边界**

说明 CLI/API 已共享小市值目标态定额器；容量、报价或现金失败会停止计划；不跟踪真实成交。

- [ ] **Step 2: 运行全量回归**

Run: `./.venv/bin/python -m unittest discover -s tests -v`

Expected: PASS with zero failures.

- [ ] **Step 3: 审核变更范围**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and no changes outside the listed files plus pre-existing user files.
