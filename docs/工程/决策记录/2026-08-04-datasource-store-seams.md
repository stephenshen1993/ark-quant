# 数据源仓储 seam 决策记录

> 状态：当前工程决策记录
> 职责：说明 `datasource/db.py` 拆出 store 模块的 seam、保留兼容入口的原因和后续拆分触发条件。
> 真源范围：`datasource` 持久化模块的工程组织方式。
> 不负责：定义投资规则、账户金额口径、策略参数或 SQLite 表结构细节。
> 日期：2026-08-04；最后核对：2026-08-04

## 1. 决策

`datasource/db.py` 继续作为 SQLite 连接、初始化迁移和旧调用方兼容门面，不再继续承载所有持久化实现。

当前确认三个 store 模块：

| 模块 | seam | 主要 interface | 当前职责 |
| --- | --- | --- | --- |
| `datasource/strategy_store.py` | 策略结果持久化 | `conn + strategy/run/date` | 策略运行、排名和衍生订单 |
| `datasource/account_store.py` | 账户事实持久化 | `conn + account/date/value` | 账户上下文、账户价值快照、账户摘要和账户历史 |
| `datasource/position_store.py` | 持仓事实持久化 | `conn + strategy/date/positions` | 持仓快照、明细、as-of 和日期窗口读取 |

这些 store 都接受外部传入的 `sqlite3.Connection`。事务边界由调用方控制；单一 store 内部可使用 savepoint 保护局部原子性。

## 2. 背景

体检时 `datasource/db.py` 同时承担 schema、迁移、测试连接注入、账户事实、持仓事实、策略结果、市场温度、生成计划和原始快照等职责，已经成为数据层最大单点。

直接按表或函数机械拆分会增加浅模块和转发层。更合理的 seam 是按业务事实簇拆：

- 策略运行、排名、订单是一组策略结果事实。
- 账户上下文和账户价值快照是一组账户事实。
- 持仓快照和持仓明细是一组持仓事实。

这三组事实都有独立测试价值，也都有多个调用方复用，因此 module interface 能带来 locality。

## 3. 保留 `datasource.db` 门面的原因

现有应用、策略脚本和测试大量通过 `from datasource import db` 调用数据层。一次性改掉所有调用方只会扩大改动面，增加与业务行为无关的风险。

因此当前做法是：

1. 新 store 承载 implementation。
2. `datasource.db` 保留旧函数名，作为兼容 interface。
3. 新 store 增加直接测试，证明它们不依赖 HTTP 路由或旧门面也能工作。
4. 后续只有在调用方自然修改时，才逐步改为直接调用 store。

## 4. 暂不继续拆的部分

暂不拆 `market_temperatures`、`market_temperature_refresh_state`、`generated_plans`、`plan_order_batches` 和 `raw_snapshots`。

原因：

- 市场温度牵涉外部刷新、缓存和失败状态，下一次应结合刷新策略一起设计。
- 生成计划牵涉计划版本、错误记录和订单批次，下一次应结合计划生命周期一起设计。
- 原始快照目前只是轻量辅助读写，尚未形成足够复杂的 module。

继续为了降低行数而拆，会得到浅 module。当前先收住。

## 5. 后续触发条件

只有出现以下情况之一，才继续拆新的 store：

- 某一组表的规则或查询开始独立变化。
- 有两个以上调用方需要同一组复杂读写语义。
- 测试需要跨过 `datasource.db` 直接验证更深的 module interface。
- 现有 `datasource.db` 门面阻碍事务边界表达。

如果只是新增单个简单 SQL，优先放在现有相关 store；找不到相关 store 时先留在 `datasource.db`，等事实簇变清楚再拆。

## 6. 验证约束

数据层改动至少满足：

- `ruff check .` 通过。
- `python -m unittest discover -s tests` 通过。
- 直接 store 测试覆盖新 module 的 interface。
- 涉及账户、持仓或计划输入时，本地服务 `/health` 保持可用。

当前拆分后验证状态：239 个测试通过，`/health` 返回 `200 OK`。
