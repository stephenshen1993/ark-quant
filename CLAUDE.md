# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Activate the virtual environment before running anything:

```bash
source .venv/bin/activate
```

## Commands

**组合分配器 (Portfolio allocator):**
```bash
python3 rebalance.py --temperature <T>                 # 读取上次账户状态，生成调拨计划
python3 rebalance.py --temperature <T> \
  --stock <总市值> --stock-cash <账户现金> \
  --bond  <总市值> --bond-cash  <账户现金> \
  --changqian <净值> --cash-pool <余额> --overseas <净值>
```
- 温度来源: https://youzhiyouxing.cn/data
- 状态持久化: `portfolios/accounts_state.json`（每次运行自动更新，除非加 `--no-save`）

**可转债多因子轮动 (CB rotation):**
```bash
python3 -m strategies.cb_rotation.run                  # 出 Top20 榜单
python3 -m strategies.cb_rotation.run --max-universe 30   # 快速冒烟测试
python3 -m strategies.cb_rotation.size_orders --cash <现金>  # 算具体买卖张数
```

**股票小市值 (Stock small-cap):**
```bash
python3 -m strategies.stock_smallcap.run               # 出 Top20 榜单
python3 -m strategies.stock_smallcap.run --max-universe 400  # 快速冒烟测试
python3 -m strategies.stock_smallcap.size_orders --cash <现金>  # 算具体买卖手数
```

**测试:**
```bash
python3 -m unittest discover tests/ -v
```

## Positions Files

| 文件 | 格式 | 策略 |
|---|---|---|
| `portfolios/current_cb_positions.csv` | `bond_code,bond_name,shares`(张数) | CB rotation |
| `portfolios/current_stock_positions.csv` | `stock_code,stock_name,shares`(股数) | Stock small-cap |
| `portfolios/accounts_state.json` | 各账户总市值 + 内部现金 | 组合分配器 |

执行完调仓后必须更新持仓文件,否则下次计算增量会出错。

## Critical Gotchas

**CB 盘中锁**: CB rotation 在 09:25–15:10 之间运行会抛 `RuntimeError`。收盘后数据冻结，盘后跑和次日盘前跑结果完全一样，都用上一完成交易日收盘数据，次日开盘执行。Stock small-cap 无硬性时间锁，但同理：盘后或次日盘前跑，结果相同，都在次日开盘执行。

**`strict_original_rules`**: 默认 `true`(`config/cb_rotation.json`)。不要在没有明确确认的情况下建议降低——它防止数据字段缺失时静默跑偏。

**股票小市值参数**: hold_n=20(不是10),sell_rank=21(跌出前20才卖),Model I(每期全量对齐),5日调仓。果仁网调优结果表明原参数已是实盘约束下最优,不要修改(见 memory/guorn_optimization_insight.md)。

**东方财富接口**: `stock_zh_a_spot_em` 和 `stock_individual_info_em` 在本机环境对 Python 请求被拦截。正股总市值已切换为腾讯接口(`qt.gtimg.cn`字段45)作为首选源。不要试图绕过或修复东财接口。

## Data & Safety

- `data/`, `outputs/`, `logs/`, `portfolios/` may contain sensitive investment context. Do not commit, upload, or share without inspection.
- Never commit `.env`, API tokens, private keys, or production credentials.
- Do not execute real trades, modify brokerage accounts, or connect to production trading systems.
- Generated outputs in `outputs/` and logs in `logs/` are local artifacts — treat them as potentially sensitive.

## Project Shape

Long-term personal investment workspace. Current active modules are the Web dashboard, account/rebalance layer, and the `cb_rotation` / `stock_smallcap` strategies. Create new top-level research, backtest, or execution areas only when they contain a concrete runnable implementation.

Propose directory or structural changes before making them. Do not delete files without explicit confirmation. Do not perform broad refactors unless the user asks.

## MCP Tools

- Use **Context7** for current AKShare / pandas / library API documentation.
- Use **Serena** for symbol lookup, call-chain tracing, and refactoring. Activate `/Users/Admin/Workspace/personal/repos/ark-quant` as the project before semantic analysis.
