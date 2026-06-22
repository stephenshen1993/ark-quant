# 体系级再平衡运行手册

> 用途：每次盘后或次日盘前生成交易计划时，先读本文件。它定义信息入口、决策顺序、需要人工提供的最小动态信息，以及哪些资产参与国内再平衡。

## 一、核心入口

体系级决策不是按单个账户各自满仓运行，而是先按国内总资产和全市场温度计算资产桶目标，再把策略内部轮动落成订单。

固定入口文件：

| 文件 | 用途 |
| --- | --- |
| `docs/system/investment_system.md` | 投资体系、资产桶、目标仓位公式、执行红线 |
| `portfolios/accounts_state.json` | 最近一次账户总额、可用现金、温度 |
| `portfolios/current_cb_positions.csv` | 当前可转债持仓，单位为张 |
| `portfolios/current_stock_positions.csv` | 当前小市值股票持仓，单位为股 |
| `config/cb_rotation.json` | 可转债策略参数 |
| `config/stock_smallcap.json` | 小市值策略参数 |
| `outputs/*_report_*.md` | 策略运行报告 |
| `outputs/*_orders_*.csv` | 具体下单清单 |

## 二、资产桶规则

国内再平衡包含四个桶：

| 桶 | 定位 | 是否参与国内再平衡 |
| --- | --- | --- |
| 小市值股票 | 进攻核心 | 是 |
| 可转债 | 进攻辅助 / 防御性进攻 | 是 |
| 长钱账户 | 国内安全垫 | 是，但小额偏离可不动 |
| 现金池 | 再平衡弹药 | 是 |

海外长钱单独追踪，原则上长期持有，不参与国内再平衡卖出。

家庭生活备用金不进入本项目投资资金池。

## 三、目标仓位公式

设：

```text
T_raw = 全市场温度，范围 0-100
T_norm = T_raw / 100
国内总资产 = 小市值账户 + 可转债账户 + 长钱账户 + 现金池
现金目标 = 国内总资产 x 20% x T_norm
B = 国内总资产 - 现金目标
```

国内非现金资产目标：

```text
长钱目标 = B x 20%
可转债目标 = B x 80% x (30% + 0.4% x T_raw)
小市值目标 = B x 80% x (70% - 0.4% x T_raw)
```

温度来源：有知有行温度计 `https://youzhiyouxing.cn/data`。

## 四、盘后运行顺序

1. 查询最新全市场温度。
2. 更新 `portfolios/accounts_state.json`：
   - `updated_at`
   - `temperature`
   - 股票账户可用现金
   - 转债账户可用现金
   - 长钱账户总额
   - 现金池总额
   - 海外长钱总额
3. 用当前持仓和实时/收盘后报价估算股票账户总额、转债账户总额。
4. 跑策略候选和调仓建议：

```bash
source .venv/bin/activate && python3 -m strategies.cb_rotation.run
source .venv/bin/activate && python3 -m strategies.stock_smallcap.run
```

5. 按体系公式计算四个国内桶的目标金额和偏离。
6. 先做体系级资金判断，再生成策略内部订单：
   - 现金池高配时，可补低配的股票/转债。
   - 现金池低配时，优先从高配资产回补。
   - 长钱高配但偏离很小时，可以不动，避免基金申赎摩擦。
   - 海外长钱不参与国内再平衡。
7. 输出体系级计划、可转债订单、小市值订单。
8. 实盘执行后，更新两份持仓文件和 `accounts_state.json`。

## 五、需要人工提供的最小动态信息

如果要生成明天交易计划，通常只需要人工提供：

```text
股票账户可用现金：___
转债账户可用现金：___
长钱账户总资产：___
现金池总资产：___
海外长钱总资产：___
```

温度可以由系统查询；股票和转债账户总资产应优先由本地持仓乘以行情价格估算，除非券商口径明显不同或持仓文件过期。

如果 `current_cb_positions.csv` 或 `current_stock_positions.csv` 不是最新实盘持仓，必须先更新持仓文件，再生成订单。

## 六、执行把关规则

体系级计划输出前，必须检查：

- 是否使用了最新温度。
- 是否使用了最新持仓文件。
- 是否把海外长钱排除在国内再平衡之外。
- 是否没有卖出家庭生活备用金。
- 是否没有为了很小偏离频繁申赎长钱账户。
- 是否先卖出、再划转、再买入，避免买入端现金不足。
- 是否考虑整 10 张可转债和整 100 股股票的取整误差。

长钱账户不是不能卖，而是需要把关摩擦和金额：

```text
偏离很小：默认不动。
偏离较大：可以按体系公式部分申购或赎回。
是否较大，可在后续工程化为阈值，例如超过 1,000 元或超过国内总资产 0.2%。
```

## 七、输出命名建议

体系级计划建议使用：

```text
outputs/system_trade_plan_<YYYYMMDD_HHMMSS>.md
outputs/system_cb_orders_<YYYYMMDD_HHMMSS>.csv
outputs/system_stock_orders_<YYYYMMDD_HHMMSS>.csv
```

单策略自身输出继续保留：

```text
outputs/cb_rotation_report_*.md
outputs/cb_rotation_rebalance_*.csv
outputs/stock_smallcap_report_*.md
outputs/stock_smallcap_rebalance_*.csv
```

体系级计划应引用单策略报告，但最终以体系级资金分配为准。
