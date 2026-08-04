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
.venv/bin/python scripts/research/cb_strategy_repro/historical_shadow_dry_run.py
.venv/bin/python scripts/research/cb_strategy_repro/shadow_snapshot.py --signal-date 2026-07-30 --dry-run --probe-sources --write-universe-template
.venv/bin/python -m unittest tests.test_cb_research_repro
```

`historical_shadow_dry_run.py` 只用历史冻结包回放某个已知信号日，验证前瞻影子记录的
`manifest/universe/execution/portfolio/review` 格式、排名复算和缺失/排除原因展示。它的输出必须标记为
`dry_run_invalid_for_forward_sample`，不能计入前瞻样本、候选收益或交易计划。

`shadow_snapshot.py` 是 2026-07-30 前瞻影子快照的只读入口护栏。非 10/20/30 日不允许生成
非 dry-run manifest；`--dry-run` 只写 rehearsal manifest 与 source checks，不生成榜单、不写数据库、
不生成订单。`--probe-sources` 只保存来源状态、行数、列名、耗时和错误摘要，不保存市场原始行。
`--write-universe-template` 只写空 `universe` 和人工风险队列模板，用于固定封存 schema；不得把模板当榜单。
`build_universe_from_snapshot()` 是 live 入口后续接真实免费源时复用的 23 列拼装合同，负责把同一快照同时
映射到 `B0_BASE/C_BAL/C_OFF` 三条排名和排除原因；当前仅由单测/历史式输入验证，不提前封存 7 月 30 榜单。
`build_snapshot_from_free_sources()` 固定第一批免费源字段语义：转债现价、正股收盘、转股价复算转股价值、
转股溢价率和双低；`build_manual_risk_queue_from_universe()` 把排除原因转成待人工核验队列。二者仍是只读
研究合同，不裁决风险、不写库、不下单。
`collect_free_source_inputs()` 与 `build_shadow_universe_bundle()` 固定了 7 月 30 可替换真实 provider 的
离线采集接口：每个来源只落审计摘要，必需来源失败时停止拼榜并返回空模板；全部可用时才在内存中串起
snapshot、23 列 universe 和人工风险队列。
`default_free_source_providers()` 是默认免费源 provider 工厂：转债快照走 AKShare 的新浪转债接口，
正股收盘按单券优先尝试东方财富日线再切新浪日线备份；正股收盘必须命中信号日，不能用最近一行替代。
`validate_security_master()` 固定证券主表最低合同：`bond_code/stock_code/convert_price/contract_maturity`
缺列或行级关键值缺失会阻断 universe 拼装；`double_low_z252/balance_bil` 属于增强路线字段，缺失时不阻断
`B0_BASE`，但会在 `master_validation.missing_enhancement_columns` 中显式暴露，并让增强路线排名留空。
`apply_risk_status_inputs()` 固定风险状态输入合同：强赎公告、尾部信用风险、ST/风险警示只映射到
`call_status/credit_risk_state/tradability_state` 和排除原因，进入人工风险队列，不参与收益排名。`build_shadow_universe_bundle()`
可接收 `risk_inputs`，在排名前完成过滤口径映射。
`seal_shadow_universe_bundle()` 固定输出封存合同：只允许写 `manifest/source-audit/universe/manual-risk-queue/master-issues`
五类研究证据。`ready=false` 时写失败 manifest、审计表和空模板；`ready=true` 时写 23 列 universe 和人工队列。
该函数不写 raw snapshot、provider frames、数据库或订单。

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
