# 账户事实 read-model 与 legacy strategy 命名决策记录

> 状态：当前工程决策记录
> 职责：说明账户事实 read-model 的 seam、legacy `strategy` 命名的保留范围和后续调用约束。
> 真源范围：账户、账户内策略、A/B/C 组合三类概念在应用层的表达方式。
> 不负责：修改投资规则、删除旧 API、修改 SQLite 表结构或实现 B 多承载通道。
> 日期：2026-08-05；最后核对：2026-08-05

## 1. 决策

账户事实使用 `app/account_read_model.py` 作为应用层 read-model interface。

新调用方应依赖 read-model 中的结构化字段：

- `accounts`：账户事实，表达资金和持仓所在的托管与执行位置。
- `strategies`：账户内策略，表达选取、持有、退出和调仓逻辑。
- `portfolio_nodes`：组合内部节点，表达 A 组合下属对象或 B/C 承载节点。
- `portfolios`：A/B/C 顶层组合。
- `legacy_adapter`：旧 DB/API 命名兼容说明。

旧 `strategy` 字段和路径名只作为 legacy adapter 保留，不再作为账户事实的新概念真源。

## 2. 背景

早期实现里，股票账户和小市值股票策略、转债账户和多因子可转债策略基本一一承载，所以很多路径和表字段用 `strategy` 指代 `stock` 或 `cb`。

当前真源已经明确：

- 账户是资金和持仓所在的托管与执行位置。
- 策略是账户内决策逻辑。
- 组合是 A/B/C 的投资体系层级。

如果继续让页面、计划校验或后续 module 直接读取历史 `strategy` 命名，就会把一一承载的当前实现误写成长期架构约束。

## 3. 保留 legacy 命名的范围

以下使用点保留为 legacy adapter 或策略运行语义：

- `strategy_runs`、排名、订单和 `datasource/strategy_store.py`：真实策略运行语义，仍可使用 `strategy`。
- `app/routers/rankings.py`：触发或读取账户内策略榜单，仍是策略语义。
- `app/routers/positions.py` 的 `/{strategy}` 路径：历史接口路径，当前只允许 `stock` 或 `cb`，属于 legacy adapter。
- `POST /api/plan/{strategy}/size-orders`：历史接口路径，内部已经由订单 sizing 编排 module 处理。
- `position_snapshots.strategy`：历史表字段，短期不改表结构。
- read-model 中的 `legacy.strategy`：只用于说明旧字段如何映射到账户，不供新调用方继续扩散。

## 4. 新调用方约束

新增账户事实调用方必须优先使用 read-model：

- 需要账户事实日期、账户名、角色、现金或承载关系时，读取 `accounts`。
- 需要账户内策略与账户承载关系时，读取 `strategies` 或账户上的 `strategy_ids`。
- 需要 A/B/C 组合层级时，读取 `portfolios` 和 `portfolio_nodes`。
- 只有在兼容旧接口、旧表字段或旧测试夹具时，才读取 `legacy_adapter` 或 `legacy.strategy`。

## 5. 第一阶段结果

本阶段已经完成：

- 账户 API 返回 `read_model`，并保留旧 `total_assets`、`context` 和 `accounts` 字段。
- 计划输入校验通过 read-model 定位账户事实，错误信息返回账户名、账户角色和组合 ID。
- 账户页展示读取 read-model 角色，用户可见文案区分账户、账户内策略和组合。

本阶段没有：

- 删除旧 API。
- 修改 SQLite 表结构。
- 做全仓机械重命名。
- 实现 B 多承载通道汇总。

## 6. 验证约束

账户事实 read-model 和 legacy 命名治理至少满足：

- `ruff check .` 通过。
- `python -m unittest discover -s tests` 通过。
- 账户 API、计划 API 和页面测试覆盖 read-model 行为。
- 搜索剩余 `strategy` 时，能按真实策略语义或 legacy adapter 语义解释其存在。
