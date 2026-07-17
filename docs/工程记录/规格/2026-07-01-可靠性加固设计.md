# Reliability Hardening Design — 2026-07-01

分层加固 ark-quant 投资系统的 6 个可靠性短板。Pareto 原则：每项挑投入产出比最高的做法，不动存储架构大改。

---

## 第 1 节：数据层 failover 加固

### 当前状态

- 股票策略快照 **只依赖腾讯**（`datasource/market.py::fetch_tencent_snapshot`）
- 腾讯挂了只能退回前一天缓存（盘前检测逻辑在 `stock_smallcap/run.py` 里）
- CB 转债列表只依赖 akshare，无可替代源，本轮不改

### 变更

1. `datasource/market.py` 新增 `fetch_sina_snapshot()` — 调新浪 `hq.sinajs.cn` 实时行情
2. `stock_smallcap/run.py::run()` 快照获取改为 fallback 链：**腾讯 → 新浪 → 缓存 → 报错**
3. 不改 CB 策略数据获取路径

### 涉及文件

- `datasource/market.py` — 新增函数
- `strategies/stock_smallcap/run.py` — 修改快照获取逻辑

---

## 第 2 节：Web API 错误透传结构化信息

### 当前状态

`app/routers/rankings.py:75-77` 只透传 `str(exc)`，前端无法区分错误类型。

### 变更

HTTPException detail 改为 dict，包含 machine-readable `code` 字段：

| code | 含义 |
|---|---|
| `TRADING_HOURS_LOCKED` | 盘中锁拦截 |
| `DATA_SOURCE_UNAVAILABLE` | 数据源挂了 |
| `STRATEGY_ERROR` | 策略内部异常 |

HTTP status code 不变（409/500），前端 fallback 到 `detail.message` 或 `str(detail)`。

### 涉及文件

- `app/routers/rankings.py` — `run_strategy()` 函数

---

## 第 3 节：股票策略加盘中锁

### 当前状态

CB 策略有 `RuntimeError("盘中不能运行策略")`，股票策略没有等价保护。

### 变更

1. 提取公共时间判断函数到 `datasource/trade_calendar.py`（或复用 CB 已有逻辑）
2. `stock_smallcap/run.py::run()` 开头加相同锁
3. 锁范围：09:25–15:10（与 CB 一致）

### 涉及文件

- `datasource/trade_calendar.py` — 新增（提取公共时间判断）
- `strategies/cb_rotation/run.py` — 改为调用公共函数
- `strategies/stock_smallcap/run.py` — 加锁

---

## 第 4 节：CSV vs DB 持仓对账脚本

### 当前状态

持仓有两套记录，可能不一致：
- `portfolios/current_*_positions.csv` — 策略读写
- SQLite（`datasource/db.py`）— Web 展示用

### 变更

新增 `scripts/reconcile_positions.py`：
- 读 CSV → 按 code 聚合
- 读 DB 最新榜单持仓
- 输出差异报告（仅 CSV 有 / 仅 DB 有 / 数量不一致）
- `--fix` 参数把 CSV 写回 DB
- 不自动修复，不接入 CI

### 涉及文件

- `scripts/reconcile_positions.py` — 新增

---

## 第 5 节：outputs/ 自动清理

### 当前状态

每次运行策略在 `outputs/` 下生成 3 个文件，永不删除。

### 变更

- `run()` 末尾加清理：保留最近 N=30 次运行的文件
- 按文件名 timestamp 前缀分组，同一次运行的 3 个文件作为一组
- N 可在 config 中覆盖

### 涉及文件

- `strategies/cb_rotation/run.py` — 加清理逻辑
- `strategies/stock_smallcap/run.py` — 加清理逻辑

---

## 第 6 节：data/raw/ 快照保留

### 当前状态

`snapshot_raw_data()` 按 `YYYYMMDD/` 存快照，无限增长。

### 变更

- 保留最近 90 天快照，删除更早的目录
- 在 `snapshot_raw_data()` 调用后做清理

### 涉及文件

- `strategies/cb_rotation/run.py` — `snapshot_raw_data()` 函数或调用方
- `strategies/stock_smallcap/run.py` — 同上

---

## 边界与排除

- **不做**存储统一到 SQLite 单源（投入太大）
- **不引入**新外部依赖（新浪接口用标准库 `urllib`/`requests`）
- **不改**CB 数据获取路径（akshare 无可替代源）
- **不接入** CI/定时任务

## 预估总变更

约 200 行，6 个文件修改 + 2 个新文件。
