# ark-quant

`ark-quant` 是一个长期投资工程化项目，用于持续沉淀、回测、跟踪和执行个人投资策略。

当前 MVP 聚焦：

> 可转债多因子轮动策略（CB-MF-Rotation v1）

目标不是一次性脚本，而是逐步演化成可长期维护的投资系统。

## 目录结构

```text
ark-quant/
├── strategies/
│   └── cb_rotation/        # 可转债多因子轮动策略
├── factor_lab/             # 因子研究
├── backtests/              # 回测模块
├── execution/              # 执行与调仓
├── portfolios/             # 当前持仓、组合配置
├── data/
│   ├── raw/                # 原始数据
│   ├── processed/          # 清洗后数据
│   └── cache/              # 临时缓存
├── dashboards/             # 可视化看板
├── outputs/                # 策略输出
├── logs/                   # 运行日志
├── config/                 # 策略配置
├── notebooks/              # 研究笔记
└── README.md
```

## 当前已实现

- 使用 AKShare 获取全市场可转债数据。
- 获取正股实时数据与历史行情。
- 正股总市值优先使用 AKShare A 股全市场快照，失败时再尝试个股信息接口和合格缓存。
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
- 根据 `portfolios/current_cb_positions.csv` 生成调仓建议。
- 输出保存到 `outputs/`，日志保存到 `logs/`。

## 安装依赖

建议使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## 运行策略

```bash
python3 -m strategies.cb_rotation.run
```

快速试跑少量标的：

```bash
python3 -m strategies.cb_rotation.run --max-bonds 30
```

指定配置和当前持仓：

```bash
python3 -m strategies.cb_rotation.run \
  --config config/cb_rotation.json \
  --positions portfolios/current_cb_positions.csv
```

## 当前持仓文件格式

编辑 `portfolios/current_cb_positions.csv`：

```csv
bond_code,bond_name,target_weight
113000,示例转债,0.05
```

策略会用最新 Top20 和当前持仓对比，生成：

- `BUY`
- `SELL`
- `HOLD`

## 输出文件

每次运行会在 `outputs/` 生成：

- `cb_rotation_top20_*.csv`
- `cb_rotation_top20_*.xlsx`
- `cb_rotation_rebalance_*.csv`
- `cb_rotation_report_*.md`

运行日志在 `logs/`：

- `cb_rotation_*.log`

关键数据缓存保存在 `data/cache/`。数据源短暂不可用时，策略会尝试合格缓存。严格模式下，成交额、正股历史因子和总市值必须覆盖全部待评分标的，市场数据日期也必须在允许范围内；否则停止输出，避免生成口径漂移的调仓建议。

## 数据源策略

数据源按字段解耦，可以逐步扩展，不锁死在单一供应方：

| 标准字段 | 当前来源优先级 |
| --- | --- |
| 全市场转债基础信息 | AKShare `bond_zh_cov`，再尝试 `bond_cb_jsl`，最后读取合格缓存 |
| 存续债参考、强赎状态、剩余规模、到期日 | AKShare 封装的集思录强赎表，失败后读取合格缓存 |
| 转债成交额 | 新浪批量行情补充；收盘后使用 AKShare 转债日线补齐；失败后读取合格缓存 |
| 正股动量、波动率 | AKShare 日线，失败时切换腾讯历史行情，再读取合格缓存 |
| 正股总市值 | AKShare A 股全市场快照，再尝试个股信息接口，最后读取合格缓存 |

新增数据源时，只需要适配为现有标准字段并经过相同的数据质量校验，不需要修改策略评分规则。

## 配置

核心参数在 `config/cb_rotation.json`：

- `top_n`: 候选持仓数量
- `filters`: 基础过滤阈值
- `weights`: 因子权重
- `position`: 仓位规则
- `data`: 数据缓存与故障回退规则

## 当前未完成事项

- AKShare 字段和接口会变化，后续需要增加数据快照与字段回归测试。
- 强赎风险目前使用集思录强赎表补充，后续需要增加字段校验和快照留存。
- 暂停交易过滤尚未实现。
- 行业集中度限制还未实现。
- 中证转债指数 120 日均线风控还未实现。
- 未实现历史回测。
- 未实现交易成本、滑点和停牌处理。
- 未实现自动周频调度。

## 下一步计划

1. 保存每次原始数据快照到 `data/raw/`。
2. 增加可转债数据字段校验和样例测试。
3. 增加 `backtests/` 下的周频回测 MVP。
4. 增加强赎专用数据源和更严格的风险过滤。
5. 增加调仓建议的人类可读 Markdown 报告模板。
