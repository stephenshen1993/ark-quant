# 文档地图

`docs/` 只放长期有参考价值的说明、手册、研究和复盘。临时输出、策略报告和下单清单继续放在 `outputs/`。

## 先读什么

| 场景 | 入口 |
| --- | --- |
| 使用本地 Web 看板录入账户和查看计划 | `app/web_dashboard.md` |
| 理解账户、资产、现金和金额显示口径 | `system/accounting_model.md` |
| 盘后/盘前按看板完成日常操作 | `operations/daily_dashboard_workflow.md` |
| 生成盘后/盘前体系级交易计划 | `operations/system_rebalance_runbook.md` |
| 理解整体投资体系 | `system/investment_system.md` |
| 查看最近体系复盘 | `reviews/portfolio_snapshot_2026-06-15.md` |
| 查看策略研究和数据源调研 | `research/` |

## 目录说明

```text
docs/
├── README.md                         # 本文档地图
├── app/                              # 本地 Web 看板说明
│   └── web_dashboard.md
├── operations/                       # 日常运行手册和执行流程
│   ├── daily_dashboard_workflow.md
│   └── system_rebalance_runbook.md
├── system/                           # 长期投资体系、资产桶、仓位公式
│   ├── accounting_model.md
│   └── investment_system.md
├── strategies/                       # 策略设计、差距分析、实现说明
│   ├── cb_rotation_gap.md
│   └── cb_rotation_mvp_design.md
├── research/                         # 外部资料、数据源、因子研究
│   ├── cb_multifactor_public_research.md
│   └── data_sources_survey.md
├── reviews/                          # 阶段复盘、体系审视、组合快照
│   ├── investment_system_review.md
│   └── portfolio_snapshot_2026-06-15.md
└── superpowers/                      # 历史计划/规格文档
```

## 文档分工

- `system/` 写稳定规则：体系定位、资产架构、目标仓位公式、执行红线。
- `app/` 写本地应用说明：页面用途、数据来源、交互约定和 API 口径。
- `operations/` 写操作流程：每天/每周怎么跑、需要什么输入、输出怎么看。
- `strategies/` 写策略内部：参数、过滤、轮动、差距和待办。
- `research/` 写可变研究：公开资料、数据源比较、未来实验方向。
- `reviews/` 写阶段记录：组合快照、体系审视、年度或阶段复盘。

## 更新原则

- 规则变更先改 `system/`，再同步 `operations/`。
- 页面文案、金额展示或账户字段变更，先同步 `system/accounting_model.md`，再改 `app/web_dashboard.md`。
- 新策略先在 `strategies/` 留设计说明，再进入代码。
- 日常生成的 CSV/Markdown 报告不要放进 `docs/`，继续留在 `outputs/`。
- 账户状态和持仓真值在 `portfolios/`，不要复制进文档作为长期真相。
