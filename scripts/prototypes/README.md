# 交易计划定额敏感性原型

> THROWAWAY：用于回答“同一份冻结输入下，离散目标手数如何改变等权偏离、现金余量和费用？”；不属于生产定额器。

从仓库根目录运行：

```sh
.venv/bin/python scripts/prototypes/sizing_sensitivity_tui.py
```

工具只读最新的已完成转债计划。输入 `add <排名>`、`remove <排名>` 或 `fill` 后，屏幕会重绘完整状态；`reset` 回到冻结计划的原始目标手数，`quit` 退出。

可用下面的无数据库合成场景复现“每只补一手都会跨过理论等权”的取舍：

```sh
.venv/bin/python scripts/prototypes/sizing_sensitivity_tui.py --case crossover
```
