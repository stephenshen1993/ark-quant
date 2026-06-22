---
name: cb-orders
description: Turn the latest CB rotation target list + current holdings + cash into a concrete buy/sell order sheet (张数). Use after /rebalance when the user wants exact share counts to trade. Pass cash as the argument.
disable-model-invocation: false
---

Convert the latest target Top-N list into an executable order sheet (how many 张 to buy/sell per bond).

`$ARGUMENTS` is the available cash in yuan (e.g. `/cb-orders 14802.95`). If the user did not provide a cash number, ask for it before running — sizing requires it.

Run:

```bash
source .venv/bin/activate && python3 -m strategies.cb_rotation.size_orders --cash $ARGUMENTS
```

This reads the most recent `outputs/cb_rotation_top*_*.csv` as the target and `portfolios/current_cb_positions.csv` (schema: `bond_code,bond_name,shares`) as current holdings, fetches live convertible-bond prices from Tencent (eastmoney-free, works any time), and writes `outputs/cb_orders_<stamp>.csv`.

After it runs:
1. Show the printed order sheet (清仓卖出 / 减仓 / 买入 / 加仓 / 持有不动) and the leftover cash.
2. Point the user at the saved `cb_orders_*.csv`.
3. Remind them: lot size is 10 张; prices are live so place sells first and adjust buys if prices moved; the plan never overspends (leaves a positive cash buffer).

After execution, offer to update `portfolios/current_cb_positions.csv` with the new holdings so next week's sizing is correct.

Notes:
- To size against a specific (not latest) target list: add `--target <path>`.
- If a bond's price can't be fetched, the tool stops and names it rather than guessing.
