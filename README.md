# ark-quant

`ark-quant` 是落实个人投资体系的辅助工具，不是投资体系本身。

投资体系先定义长期目标、风险边界、仓位与再平衡战术、账户内策略和记录口径；本项目负责把已经盘定的规则转成可记录、可检查、可复核的计算与操作流程。若代码、配置或页面与体系文档冲突，应修正工具或登记实现差距，不能用实现现状反推规则。

当前 MVP 目标很明确：基于最新账户、持仓、温度和策略输入，快速生成资金调拨计划与股票、可转债交易计划。计划是否执行、如何执行、部分成交和跨日状态不由 ark-quant 跟踪；长期投资账本继续使用已经手工维护三年多的有知有行投资账本，暂不迁移。

## 先读什么

- [文档地图](docs/README.md)：所有正式文档的职责、状态和阅读顺序。
- [投资体系总纲](docs/体系/投资体系总纲.md)：战略真源。
- [仓位与再平衡规则](docs/体系/仓位与再平衡规则.md)：正在盘点的资金调拨战术。
- [日常运行手册](docs/使用/日常运行手册.md)：当前工具的操作流程。
- [当前实现状态](docs/工程/当前实现状态.md)：工具已经做到什么、还有什么差距。

## 当前能力

- 运行小市值股票与多因子可转债策略，保存榜单和订单结果。
- 记录账户、持仓、市场温度及其时间口径。
- 计算当前国内目标金额和账户间调拨方向。
- 通过本地 Web 看板录入事实、查看榜单和检查本期计划。
- 在关键输入缺失或日期不一致时停止生成计划。

这里的“当前目标金额”只代表现行战术快照。仓位公式、比例、频率或阈值即使已经写进代码，也不因此成为正式共识。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
scripts/start_app.sh
```

打开 `http://127.0.0.1:8000`。完整操作顺序见 [日常运行手册](docs/使用/日常运行手册.md)。

常用本地命令：

```bash
# 启动 / 查看 / 停止本地看板
scripts/start_app.sh
scripts/status_app.sh
scripts/stop_app.sh

# 自动化测试
.venv/bin/python -m unittest discover -s tests -v

# 静态检查
.venv/bin/ruff check .

# 两条账户内策略
.venv/bin/python -m strategies.cb_rotation.run
.venv/bin/python -m strategies.stock_smallcap.run

# 当前体系级目标金额与调拨计划
.venv/bin/python rebalance.py
```

## 目录

```text
app/             本地 Web 看板与 API
datasource/      SQLite、行情、温度和交易日数据访问
strategies/      账户内策略实现
config/          当前实现参数；不是规则真源
portfolios/      本地账户与持仓输入
data/            本地数据库、快照与缓存
outputs/         运行生成物；不是正式文档
logs/            运行日志
docs/            体系、策略、使用、工程、研究、复盘与档案
tests/           自动化回归测试
```

## 安全边界

- 系统只生成辅助信息，不连接券商自动交易。
- 系统不替代有知有行投资账本，不要求写回实际成交或计划执行状态。
- 券商、银行和基金平台的真实成交、持仓与余额高于系统派生结果。
- `data/`、`portfolios/`、`outputs/` 和 `logs/` 可能包含敏感投资信息，分享或提交前必须检查。
- 不提交 `.env`、令牌、私钥或生产凭据。
