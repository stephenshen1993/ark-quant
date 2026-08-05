# 订单 sizing 编排 module 决策记录

> 状态：当前工程决策记录
> 职责：说明订单 sizing 编排 module 的边界、adapter 关系和验证约束。
> 真源范围：从计划现金、榜单、持仓、报价到订单草案的应用层编排方式。
> 不负责：定义小市值股票或多因子可转债的 sizing 算法，不定义投资规则或成交事实。
> 日期：2026-08-05；最后核对：2026-08-05

## 1. 决策

订单 sizing 编排形成独立 module：`app/order_sizing.py`。

该 module 的 interface 包括：

- `size_strategy_orders(strategy)`：按当前计划输入为指定策略生成订单。
- `size_cb_orders(cash, plan_date=...)`：为转债策略生成订单。
- `size_stock_orders(cash, plan_date=...)`：为股票策略生成订单。

HTTP 路由只作为 adapter，把请求转换为 module 调用，并把 `PlanServiceError` 转为 HTTP 响应。

## 2. module 边界

订单 sizing 编排 module 负责：

- 读取计划日榜单。
- 读取计划输入窗口内的持仓事实。
- 获取本次 sizing 所需的实时报价。
- 调用账户内策略 sizing implementation。
- 校验本次订单后的现金约束。
- 把策略订单写回对应策略运行。
- 返回页面和计划生命周期可使用的订单与摘要。

订单 sizing 编排 module 不负责：

- 计算账户间资金调拨金额。
- 决定计划生命周期状态。
- 定义股票或转债 sizing 算法。
- 记录成交事实。
- 修改投资体系规则。

## 3. 背景

原先 `app/routers/plans.py` 同时承担 HTTP adapter、计划输入协调、报价获取、持仓转换、策略 sizing 调用、现金校验和订单落库。

这让路由变成 shallow interface 加 heavy implementation：调用方想复用订单 sizing 时必须绕过 HTTP 语境，测试也被迫 patch 路由内部函数。

把 sizing 编排收敛到独立 module 后，HTTP、完整计划生成和未来后台任务可以依赖同一个应用层 interface。

## 4. 与计划生命周期的关系

计划生命周期 module 负责登记订单批次和计划状态。

订单 sizing 编排 module 负责产出订单内容。计划生命周期不进入股票或转债 sizing implementation，订单 sizing 也不决定计划状态。

## 5. 验证约束

订单 sizing 编排治理至少满足：

- `ruff check .` 通过。
- `python -m unittest discover -s tests` 通过。
- `/api/plan/{strategy}/size-orders` 行为不退化。
- `/api/plan/generate` 仍能在服务端完整生成计划并登记订单批次。
- 新 module 具有不经过 HTTP 路由的直接测试。
