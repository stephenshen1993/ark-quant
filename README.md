# ark-quant

`ark-quant` 是一个长期个人投资工程化项目，用于持续沉淀、回测、跟踪和执行个人投资策略。

当前已上线两条策略线：

> - 可转债多因子轮动策略（CB-MF-Rotation v1）
> - A股小市值轮动策略（Stock-SmallCap v1）

目标不是一次性脚本，而是逐步演化成可长期维护的投资系统（转债 + 股票 + 基金 + 多策略协同）。

## 操作入口

如果是盘后或盘前生成交易计划，先读：

- `docs/README.md`：文档地图，说明体系、操作、研究、复盘各自在哪。
- `docs/app/web_dashboard.md`：本地 Web 看板说明，包含启动、页面分工、数据来源和录入方式。
- `docs/system/accounting_model.md`：账户、总资产、持仓资产、可用现金和金额展示口径。
- `docs/operations/daily_dashboard_workflow.md`：盘后/盘前使用看板完成账户录入、策略检查和计划确认的流程。
- `docs/operations/system_rebalance_runbook.md`：体系级再平衡运行手册，定义输入、公式、执行顺序和把关规则。
- `docs/system/investment_system.md`：长期投资体系说明，定义资产桶和目标仓位公式。
- `portfolios/accounts_state.json`：最近账户状态，包括温度、现金、长钱、海外长钱。
- `portfolios/current_cb_positions.csv`、`portfolios/current_stock_positions.csv`：当前策略持仓。

体系级决策顺序是：先按温度和国内总资产计算现金、长钱、可转债、小市值目标金额，再把可转债和小市值策略内部轮动落成具体订单。不要把本项目理解成“股票账户和转债账户各自独立满仓运行”。

常用执行入口：

```bash
# 启动本地 Web 看板
python3 run_app.py

# 用持仓和行情估算股票/转债账户总市值，并回填账户状态
python3 value_accounts.py

# 自动读取有知有行全市场温度，按账户状态计算体系级目标仓位与调拨方向
python3 rebalance.py
```

## 目录结构

```text
ark-quant/
├── strategies/
│   ├── cb_rotation/        # 可转债多因子轮动策略
│   └── stock_smallcap/     # A股小市值轮动策略
├── portfolios/             # 当前持仓（cb + stock）
├── config/                 # 策略配置 JSON
├── data/
│   ├── raw/                # 不可变快照（按交易日分目录）
│   ├── processed/          # 清洗后数据
│   └── cache/              # 工作缓存（短期故障回退）
├── outputs/                # 策略输出（榜单、调仓建议、报告）
├── logs/                   # 运行日志
├── datasource/             # 统一行情与数据存取封装
├── docs/                   # 体系说明、运行手册、策略设计、研究与复盘
│   ├── operations/         # 日常运行手册
│   ├── system/             # 投资体系与仓位公式
│   ├── strategies/         # 策略设计与差距分析
│   ├── research/           # 数据源、因子和外部资料调研
│   └── reviews/            # 阶段复盘与组合快照
├── backtests/              # 回测模块（待建）
├── factor_lab/             # 因子研究（待建）
├── execution/              # 执行与调仓自动化（待建）
├── dashboards/             # 可视化看板（待建）
├── notebooks/              # 研究笔记
├── tests/                  # 自动化测试
├── rebalance.py            # 体系级目标仓位与调拨计划
└── value_accounts.py       # 股票/转债账户估值并回填状态
```

## 当前已实现

### 可转债多因子轮动（CB-MF-Rotation v1）

- 使用 AKShare 获取全市场可转债数据。
- 使用最近已完成交易日的收盘数据选债，下一交易日开盘执行调仓。
- 获取正股历史行情和收盘后市场快照。
- 正股总市值优先使用腾讯行情接口（免登录、不依赖东方财富），失败时再尝试东财快照、个股信息接口和合格缓存，仍缺失时按配置回退流通市值估算。
- 计算四个 MVP 因子：
  - 双低因子：转债价格 + 转股溢价率
  - 正股 20 日动量
  - 正股 20 日波动率
  - 正股总市值
- 执行基础过滤：
  - 转债价格 < 130
  - 剩余规模 > 3 亿
  - 日成交额 > 3000 万
  - 排除强赎风险债
  - 排除 ST 正股
  - 到期时间 > 1 年
- 综合评分：
  - 双低 35%
  - 正股动量 30%
  - 低波动 20%
  - 小市值 15%
- 输出 Top20 候选持仓。
- 支持等权仓位，单债权重上限默认 8%。
- 根据 `portfolios/current_cb_positions.csv`（含持仓张数）生成调仓建议。
- `strategies/cb_rotation/size_orders.py`：根据榜单 + 持仓张数 + 可用现金，计算具体买卖张数（腾讯实时价，10张整数倍，防超支）。
- 输出保存到 `outputs/`，日志保存到 `logs/`。

### A股小市值轮动（Stock-SmallCap v1）

- 选股域：主板 + 中小板，排除创业板/科创板/北交所/B股/ST/退市。
- 过滤条件：当日成交额 > 1000万、非涨停、非跌停、PE > 0（EP > 0）、ROE > -1%。
- 排名：总市值从小到大（纯小市值单因子）。
- 持仓 20 只等权，排名跌出前 20（≥21）时卖出，5个交易日调仓，开盘价成交（果仁网 Model I 复刻）。
- 数据全程免登录：新浪全市场名单 + 腾讯快照（价格/成交额/PE/总市值/涨跌停）+ 新浪财务指标（ROE）。
- 根据 `portfolios/current_stock_positions.csv`（含持仓股数）生成调仓建议。
- `strategies/stock_smallcap/size_orders.py`：计算具体买卖手数（1手=100股，腾讯实时价）。
- 每次运行自动存不可变快照到 `data/raw/<交易日>/stock_smallcap/`。

## 安装依赖

建议使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## 运行策略

### 可转债轮动

CB rotation 使用上一已完成交易日收盘数据，交易时段（09:25–15:10）内停止运行：

```bash
# 出 Top20 榜单 + 调仓建议
python3 -m strategies.cb_rotation.run

# 快速冒烟测试
python3 -m strategies.cb_rotation.run --max-universe 30

# 根据榜单 + 持仓 + 现金，计算具体买卖张数
python3 -m strategies.cb_rotation.size_orders --cash 5000
```

### 股票小市值

```bash
# 出 Top20 榜单 + 调仓建议（收盘后数据冻结，可盘后跑或次日盘前跑，结果相同）
python3 -m strategies.stock_smallcap.run

# 快速冒烟测试
python3 -m strategies.stock_smallcap.run --max-universe 400

# 根据榜单 + 持仓 + 现金，计算具体买卖手数（1手=100股）
python3 -m strategies.stock_smallcap.size_orders --cash 2000
```

## 持仓文件格式

**可转债**（`portfolios/current_cb_positions.csv`）：

```csv
bond_code,bond_name,shares
110059,某某转债,100
```

**股票**（`portfolios/current_stock_positions.csv`）：

```csv
stock_code,stock_name,shares
600001,某某股份,300
```

每次执行完调仓后需手动更新持仓文件，下次 `size_orders` 才能算出正确增量。

## 输出文件

**可转债（`outputs/`）：**

| 文件 | 内容 |
|---|---|
| `cb_rotation_top20_*.csv/.xlsx` | Top20 候选持仓（含因子值、评分） |
| `cb_rotation_rebalance_*.csv` | 调仓建议（BUY/SELL/HOLD） |
| `cb_rotation_report_*.md` | 可读报告（含数据口径提醒） |
| `cb_orders_*.csv` | 具体买卖张数下单清单 |

**股票（`outputs/`）：**

| 文件 | 内容 |
|---|---|
| `stock_smallcap_pool_*.csv` | 候选池（按总市值升序，含ROE/PE/成交额） |
| `stock_smallcap_rebalance_*.csv` | 调仓建议（BUY/SELL/HOLD，含跌出原因） |
| `stock_smallcap_report_*.md` | 可读报告 |
| `stock_orders_*.csv` | 具体买卖手数下单清单 |

**日志：** `logs/cb_rotation_*.log`、`logs/stock_smallcap_*.log`

**数据快照：** `data/raw/<YYYYMMDD>/`（CB）和 `data/raw/<YYYYMMDD>/stock_smallcap/`（股票）

关键数据缓存保存在 `data/cache/`（可变的工作缓存，用于短期故障回退）。数据源短暂不可用时，策略会尝试合格缓存。严格模式下，成交额、正股历史因子和总市值必须覆盖全部待评分标的，市场数据日期也必须在允许范围内；否则停止输出，避免生成口径漂移的调仓建议。

每次成功运行还会把决定榜单的原始/中间数据按"数据基准日"存为不可变快照到 `data/raw/<交易日>/`（含源数据、富化全集、打分全集、Top 榜单、配置和 `manifest.json`）。用途：任何一天的榜单可复现复核、源临时不可用时可回退、为回测累积历史。可用 `data.save_raw_snapshot` 关闭。

## 数据源策略

数据源按字段解耦，可以逐步扩展，不锁死在单一供应方：

| 标准字段 | 当前来源优先级 |
| --- | --- |
| 全市场转债基础信息 | AKShare `bond_zh_cov`，再尝试 `bond_cb_jsl`，最后读取合格缓存 |
| 存续债参考、强赎状态、剩余规模、到期日 | AKShare 封装的集思录强赎表，失败后读取合格缓存 |
| 转债收盘价、成交额 | AKShare 转债日线；失败后读取合格缓存 |
| 正股动量、波动率 | AKShare 日线，失败时切换腾讯历史行情，再读取合格缓存 |
| 正股总市值 | 腾讯行情接口 `qt.gtimg.cn`（总市值，免登录、不走东财），失败后再尝试东财快照/个股信息，最后读取合格缓存；仍缺失时按配置回退流通市值估算 |

新增数据源时，只需要适配为现有标准字段并经过相同的数据质量校验，不需要修改策略评分规则。

## 配置

核心参数在 `config/cb_rotation.json`：

- `top_n`: 候选持仓数量
- `filters`: 基础过滤阈值
- `weights`: 因子权重
- `position`: 仓位规则
- `data`: 数据缓存与故障回退规则

## 当前未完成事项

**可转债：**
- 暂停交易过滤尚未实现。
- 行业集中度限制（单行业上限30%）还未实现。
- 中证转债指数 120 日均线风控还未实现。
- 正股总市值长期稳定来源：腾讯 qt.gtimg.cn 是现用主源，Tushare `daily_basic.total_mv` 是未来备选（需注册积分）。

**股票：**
- ROE 使用常规净资产收益率近似扣非ROE（免费）；可升级为 Tushare `fina_indicator.roe_dt` 精确口径。
- 停牌过滤依赖成交量=0判断，可补更明确的停牌状态字段。

**通用：**
- 未实现历史回测（`backtests/` 待建）。
- 未实现自动调度（cron/定时）。
- 基金持仓跟踪尚未建设（用户持有基金）。
- AKShare 接口字段会变化，后续需增加字段回归测试。

## 下一步计划

1. ~~保存每次原始数据快照到 `data/raw/`。~~（已完成）
2. 基金持仓跟踪：接入天天基金净值/持仓数据。
3. 转债/股票周频回测 MVP（`backtests/`）。
4. 中证转债 120 日均线择时风控。
5. Tushare `daily_basic` 接入（注册后）：升级总市值口径，扩展财务因子。
