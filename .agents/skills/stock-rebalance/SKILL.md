---
name: stock-rebalance
description: Run the small-cap A-share rotation screen (Stock-SmallCap) and show the candidate pool + rebalance vs current stock holdings. Use when the user wants today's small-cap stock list.
disable-model-invocation: false
---

Run the small-cap stock screen:

```bash
source .venv/bin/activate && python3 -m strategies.stock_smallcap.run $ARGUMENTS
```

Reads `config/stock_smallcap.json` and `portfolios/current_stock_positions.csv` (schema `stock_code,stock_name,shares`). Universe + names come from sina spot; price/成交额/PE/总市值/涨跌停 from Tencent (eastmoney-free); ROE from sina financial indicator. Writes `outputs/stock_smallcap_pool_*.csv`, `_rebalance_*.csv`, `_report_*.md`, and a `data/raw/<date>/stock_smallcap/` snapshot.

After it runs:
1. Show the report's 小市值候选 table and the 调仓建议 (BUY/SELL/HOLD with reasons).
2. Surface the data-note caveats — especially: ROE is regular 净资产收益率 (approximating 扣非ROE), and 总市值/成交额/涨跌停 are run-time values.
3. **Warn if run during trading hours (09:30–15:00):** 当日成交额 is partial and 涨跌停 can still change, so the list is preliminary — recommend re-running pre-open or after close for an execution-ready list.

Notes:
- `--max-universe N` limits the universe for a quick smoke test.
- The strategy holds ~10 names; a holding is sold when its small-cap rank drops to ≥ `sell_rank` (default 20).
- Sizing into shares (一手=100) is a separate follow-up; this command produces the selection + BUY/SELL/HOLD plan.
