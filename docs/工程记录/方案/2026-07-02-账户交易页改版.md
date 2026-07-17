# 账户 & 交易页面重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将导航从三个页面（账户总览 / 交易计划 / 榜单）合并为两个（账户 / 交易），温度和日期从账户页移至交易页

**Architecture:** 纯前端重构，主要改动在 `app/static/index.html`（单文件 Alpine.js 应用）。账户页只保留账户数据，交易页融合榜单和计划

**Tech Stack:** Alpine.js + Tailwind CSS + FastAPI 测试客户端

## Global Constraints

- 不引入新依赖
- 不改 DB schema（temperature 字段保留但不展示在账户页）
- 不改变 `/api/account/snapshot` 的 request body（temperature 仍可传，账户页不传即可）
- `load_or_fetch` DB 缓存机制不变

---

### Task 1: 导航合并 + 账户页移除温度/日期

**Files:**
- Modify: `app/static/index.html` — 导航定义 + 账户总览区块

**Interfaces:**
- Consumes: 当前导航三元组 `[{id:'account'},{id:'plan'},{id:'rankings'}]`
- Produces: 新导航二元组 `[{id:'account'},{id:'trading'}]`，store page 值变化

- [ ] **Step 1: 改导航定义**

将侧边导航从三个改为两个：

```javascript
// 替换原来的导航 items 数组（约第 16-19 行）
{id:'account', label:'账户', icon:'📊'},
{id:'trading', label:'交易', icon:'📈'},
```

- [ ] **Step 2: 账户页移除日期和温度区块**

删除总资产卡片右上角的日期、温度输入框和"保存账户"按钮（约第 55-63 行），改为只读展示日期：

```html
<!-- 替换原来的日期/温度/保存行 -->
<span x-show="snap" class="text-xs text-gray-400"
  x-text="'快照 ' + snap.snapshot_date"></span>
```

- [ ] **Step 3: 账户页头部移入详情页中**

各账户详情页（stock/cb/changqian/cash/overseas）的现金输入框，去掉 `@input.debounce`，加独立"保存"按钮。给 stock 和 cb 账户的现金卡片加保存按钮：

```html
<!-- 股票账户现金卡片，替换原来的 @input.debounce -->
<div class="bg-white border border-gray-100 rounded-xl p-5 shadow-sm shadow-gray-100/40">
  <div class="text-xs text-gray-400 mb-2">可用现金</div>
  <input type="text" inputmode="decimal" x-model="form.stock_cash"
    class="text-2xl font-semibold w-full bg-transparent outline-none border-b border-gray-200 pb-1"
    placeholder="0.00">
  <button @click="saveSnapshot({ reload: false })"
    class="mt-2 text-xs px-3 py-1 bg-gray-800 text-white rounded-md hover:bg-gray-600">保存</button>
  <span x-show="autoSaved" class="text-xs text-green-600 ml-2">已保存</span>
</div>
```

同理处理 cb 账户的 `bond_cash`、changqian 的 `changqian_total`、cash 的 `cash_pool`、overseas 的 `overseas_total`。

- [ ] **Step 4: 测试**

```bash
source .venv/bin/activate && python3 -m unittest discover tests/ -v
```

确保现有 75 个测试全过。手动确认：导航两个条目，账户页无温度/日期输入，详情页现金字段旁有保存按钮。

- [ ] **Step 5: Commit**

```bash
git add app/static/index.html
git commit -m "refactor: 导航三元→二元, 账户页移除温度日期, 详情页用显式保存替防抖"
```

---

### Task 2: Plan 和 Rankings 合并为 Trading 页

**Files:**
- Modify: `app/static/index.html` — 新增 trading 页内容，移除旧的 plan 和 rankings 独立区块

**Interfaces:**
- Consumes: Task 1 产生的 `$store.page === 'trading'`
- Produces: `tradingPage()` Alpine 组件，包含 T 日数据 + 资金分配 + 转债 + 股票四大区块

- [ ] **Step 1: 创建 tradingPage 组件**

合并 rankingsPage 和 planPage 逻辑。核心结构：

```javascript
function tradingPage() {
  return {
    // T 日数据
    tDate: new Date().toISOString().slice(0, 10),
    temperature: 0,
    // 资金分配
    plan: null,
    // 转债
    tab: 'cb',
    cbItems: [], cbDates: [], cbDate: '', cbTradeDate: '',
    cbOrders: [], cbRankings: [],
    sizingCb: false,
    // 股票
    stockItems: [], stockDates: [], stockDate: '', stockTradeDate: '',
    stockOrders: [], stockRankings: [],
    sizingStock: false,
    // 通用
    running: false, error: '',

    async init() { await this.load() },

    async load() {
      // 加载账户数据
      const [snap, plan] = await Promise.all([
        fetch('/api/account/latest').then(r => r.json()),
        fetch('/api/plan').then(r => r.json()),
      ])
      this.tDate = snap?.snapshot_date || this.tDate
      this.temperature = snap?.temperature || 0

      // 加载排名日期列表
      const [cbDates, stockDates] = await Promise.all([
        fetch('/api/rankings/cb/dates').then(r => r.json()),
        fetch('/api/rankings/stock/dates').then(r => r.json()),
      ])
      this.cbDates = cbDates.sort().reverse()
      this.stockDates = stockDates.sort().reverse()
      this.cbDate = this.cbDates[0] || ''
      this.stockDate = this.stockDates[0] || ''

      // 加载最新排名
      if (this.cbDate) await this.loadCb()
      if (this.stockDate) await this.loadStock()

      // 加载计划
      this.plan = plan
      this.cbOrders = plan?.cb?.orders || []
      this.cbRankings = plan?.cb?.rankings || []
      this.stockOrders = plan?.stock?.orders || []
      this.stockRankings = plan?.stock?.rankings || []
    },

    async saveTDayData() {
      await fetch('/api/account/snapshot', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          snapshot_date: this.tDate,
          temperature: Number(this.temperature) || 0,
          stock_total: this.plan?.account?.stock_total || 0,
          stock_cash: this.plan?.account?.stock_cash || 0,
          bond_total: this.plan?.account?.bond_total || 0,
          bond_cash: this.plan?.account?.bond_cash || 0,
          changqian_total: this.plan?.account?.changqian_total || 0,
          cash_pool: this.plan?.account?.cash_pool || 0,
          overseas_total: this.plan?.account?.overseas_total || 0,
        }),
      })
      await this.load()
    },

    // 排名相关（从 rankingsPage 搬过来）
    async runStrategy() { /* 同上 */ },
    async loadCb() { /* 同上 */ },
    async loadStock() { /* 同上 */ },
    async exportMd(strategy) { /* 同上 */ },

    // 计划相关（从 planPage 搬过来）
    fmtMoney(v) { /* 同上 */ },
    executionDate() { /* 同上 */ },
    groupClass(action) { /* 同上 */ },
    cbGroups() { /* 同上 */ },
    stockGroups() { /* 同上 */ },
    async sizeOrders(strategy) { /* 同上 */ },
  }
}
```

- [ ] **Step 2: 创建 trading 页 HTML 结构**

四大区块自上而下排列：

```html
<!-- 交易 -->
<div x-show="$store.page === 'trading'" x-data="tradingPage()" x-init="init()" class="max-w-7xl">
  <!-- T 日收盘数据 -->
  <div class="flex items-center gap-4 mb-5 p-4 bg-white border border-gray-100 rounded-xl shadow-sm">
    <h1 class="text-xl font-semibold">交易</h1>
    <label class="text-xs text-gray-400 flex items-center gap-2">日期
      <input type="date" x-model="tDate" class="text-xs bg-gray-50 rounded px-2 py-1 outline-none">
    </label>
    <label class="text-xs text-gray-400 flex items-center gap-2">温度
      <input type="number" x-model.number="temperature" class="w-12 text-xs bg-gray-50 rounded px-2 py-1 outline-none text-center" placeholder="45">
    </label>
    <span x-show="savedTDay" class="text-xs text-green-600">已保存</span>
    <button @click="saveTDayData()" class="ml-auto px-3 py-1.5 bg-gray-900 text-white text-xs rounded-lg hover:bg-gray-700">保存</button>
  </div>

  <!-- ① 资金分配 -->
  <div class="bg-white border border-gray-100 rounded-xl p-5 mb-4 shadow-sm" x-show="plan?.transfer_steps?.length">
    <div class="text-sm font-semibold mb-3">① 资金分配</div>
    <template x-for="(step, i) in plan?.transfer_steps || []">
      <div class="flex gap-3 text-sm text-gray-700 py-2 border-b border-gray-50 last:border-0">
        <span class="text-xs w-5 h-5 rounded-full bg-gray-100 text-gray-500 flex items-center justify-center shrink-0" x-text="i+1"></span>
        <span x-text="step"></span>
      </div>
    </template>
  </div>

  <!-- ② 转债 -->
  <!-- ③ 股票 -->
  <!-- 复用现有的 plan 页结构 + rankings 页结构，嵌入 -->
</div>
```

- [ ] **Step 3: 把 rankings 页内容嵌入 trading 页的②③区块**

将原来 rankings 页的转债 Tab / 股票 Tab 内容分别嵌入到②③区块。②转债调仓区先展示排名表（从 rankingsPage `loadCb()` 取），下方是该策略的订单/生成按钮；③股票同理。

- [ ] **Step 4: 把 plan 页内容嵌入 trading 页的②③区块**

②转债调仓区的下半部分是订单区——复用现有的订单卡片展示逻辑和生成按钮。③股票同理。

- [ ] **Step 5: 清理旧代码**

删除不再使用的独立 `rankingsPage()` 和 `planPage()` 组件定义，以及对应的独立 `<div x-show="$store.page === 'plan'">` 和 `<div x-show="$store.page === 'rankings'">` 区块。

- [ ] **Step 6: 测试**

```bash
source .venv/bin/activate && python3 -m unittest discover tests/ -v
```

- [ ] **Step 7: Commit**

```bash
git add app/static/index.html
git commit -m "refactor: plan+rankings 合并为 trading 页, 温度日期移至交易页顶"
```

---

### Task 3: API 层调整

**Files:**
- Modify: `app/routers/plans.py` — 接受 date 和 temperature 参数

**Interfaces:**
- Consumes: `/api/plan` GET 新增可选 query params `?date=YYYY-MM-DD&temperature=N`
- Produces: transfer_steps 使用传入 temperature 而非从 account snapshot 取

- [ ] **Step 1: 修改 /api/plan 支持 query params**

```python
@router.get("")
def get_plan(date: str | None = None, temperature: float | None = None):
    account = db.get_latest_account_snapshot()
    # temperature 优先用传入值，其次用账户快照中的值
    temp = temperature if temperature is not None else (account["temperature"] if account else 0)
    # ... 后续逻辑用 temp 替代 account["temperature"] ...
```

- [ ] **Step 2: 前端 tradingPage 调用时传入温度**

```javascript
async load() {
    const temp = Number(this.temperature) || 0
    const date = this.tDate
    const plan = await fetch(`/api/plan?date=${date}&temperature=${temp}`).then(r => r.json())
    // ...
}
```

- [ ] **Step 3: 测试 + Commit**

更新 `test_api_plans.py` 测试 query params，跑全量测试，commit。

---

### Task 4: 全量测试 + 验证

- [ ] **Step 1: 跑全量测试**

```bash
source .venv/bin/activate && python3 -m unittest discover tests/ -v
```

- [ ] **Step 2: 手动验证清单**

1. 导航只有"账户"和"交易"两个
2. 账户页无温度/日期，详情页有显式保存按钮
3. 交易页顶部有日期+温度，下方有资金分配+转债+股票
4. 温度改动 → 资金分配重新计算
5. 日期改动 → 排名切换到对应日期
6. 运行策略 → 排名更新 → 订单可生成

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "test: 账户交易页面重构验收测试通过"
```
