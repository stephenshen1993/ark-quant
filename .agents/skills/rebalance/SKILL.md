---
name: rebalance
description: Run the CB rotation strategy with current positions and display the output report. Use when the user wants to generate today's rebalance recommendation.
disable-model-invocation: false
---

Run the CB rotation strategy using the current positions file:

```bash
.venv/bin/python -m strategies.cb_rotation.run $ARGUMENTS
```

If `$ARGUMENTS` includes `--max-universe N`, pass it through for a quick smoke test.

After a successful run:
1. Print the output file paths (candidates CSV, rebalance CSV, report MD).
2. Read and display the contents of the Markdown report so the user can review recommendations inline.

If the strategy raises a RuntimeError about the run window (09:25–15:10 China time), tell the user clearly and stop — do not attempt to retry or modify the time check.

If the run fails for a data reason (AKShare unreachable, stale cache), report the exact error message and suggest checking the cache in `data/cache/` or retrying after market close.
