# 可转债历史研究最小可复现包

本目录只服务于 2026-07-20 对抗审查后的证据整改，不是生产策略实现，也不读取或修改
`ark_quant.db`、V0 持仓、正式规则或交易配置。

## 两阶段运行

1. `prepare_frozen_inputs.py` 是一次性冻结器。它读取本轮仍在 `/private/tmp/ark_cb_*` 的原始研究资产，
   生成持久化的最小输入包。完成冻结后，后续重跑不再依赖 `/private/tmp`。
2. `run_research.py` 只读取冻结包，统一重跑 `B0_BASE`、`C_BAL`、`C_OFF`，生成逐日净值、
   调仓记录、基准状态和统计复核。

默认冻结位置是 `data/processed/cb_research_20260720/`。该目录按仓库约定不入 Git；
输入/输出哈希与覆盖范围会写入 `manifest.json`，并同步一份可审计清单到 `docs/策略/证据/`。

```bash
.venv/bin/python scripts/research/cb_strategy_repro/prepare_frozen_inputs.py
.venv/bin/python scripts/research/cb_strategy_repro/run_research.py
.venv/bin/python -m unittest tests.test_cb_research_repro
```

## 冻结合同

- 信号：每月 10/20/30 日；非交易日取其后首个交易日。
- 执行：信号收盘排名，下一交易日开盘调仓。
- 持仓：Top20 等权，买卖各计 10bp。
- 缺价：目标券执行日无开盘时不买，原定份额留现金；旧仓无开盘时继续占资。
- 风险：已公告强赎、首次公开硬信用风险、风险警示区间和剩余期限只作过滤/退出，不参与收益排名。
- 收益：同时输出不含票息的价格净值和按公开票息计划建模的税前总收益净值；后者仍不含个人税、
  集合竞价冲击和非标准回收的不确定部分。

## 基准标签

- `U-EW`：最终安全合格域的无成本事件机会集，只是研究对照，不是正式指数。
- `JSL-CB-EW`：只有取得集思录官方完整历史时才允许使用该名称。
- `JSL-CB-EW-REPLICA`：按公开编制规则自算的价格指数副本，不冒充官方历史。
- `HS300-TR`：沪深300全收益指数 `H00300`；缺少真实序列时必须标记 `missing`。
- `SMALLCAP-PROXY`：国证2000价格指数，只作职责下限代理，不冒充果仁正式小市值策略。
