"""投资领域的稳定定义。

账户是资金托管和执行通道；策略决定账户内交易或账户间资金调拨；
资产类别描述所持有的对象。这里不保存快照，也不承载仓位公式，避免
把当前的一一承载关系误写成“账户就是策略”。
"""
from __future__ import annotations

from typing import Final


ACCOUNT_DEFINITIONS: Final[dict[str, dict]] = {
    "stock": {
        "label": "广发账户",
        "sub": "证券交易与托管",
        "asset_classes": ("股票", "现金"),
        "country_exposure": "国内",
        "strategy_ids": ("smallcap_stock",),
        "participates_in_domestic_rebalance": True,
    },
    "cb": {
        "label": "华泰账户",
        "sub": "证券交易与托管",
        "asset_classes": ("可转债", "现金"),
        "country_exposure": "国内",
        "strategy_ids": ("multifactor_convertible_bond",),
        "participates_in_domestic_rebalance": True,
    },
    "changqian": {
        "label": "长钱投顾组合",
        "sub": "雪球基金托管",
        "asset_classes": ("基金",),
        "country_exposure": "国内",
        "strategy_ids": ("domestic_long_term_advisory",),
        "participates_in_domestic_rebalance": True,
    },
    "overseas": {
        "label": "海外长钱投顾组合",
        "sub": "雪球基金托管",
        "asset_classes": ("基金",),
        "country_exposure": "海外",
        "strategy_ids": ("overseas_long_term_advisory",),
        "participates_in_domestic_rebalance": False,
    },
    "cash": {
        "label": "浦发现金账户",
        "sub": "资金调拨与现金管理",
        "asset_classes": ("现金",),
        "country_exposure": "国内",
        "strategy_ids": ("cash_management",),
        "participates_in_domestic_rebalance": True,
    },
}

STRATEGY_DEFINITIONS: Final[dict[str, dict]] = {
    "fund_transfer": {
        "name": "资金调拨策略",
        "scope": "跨账户",
        "description": "按策略/组合目标金额和偏离控制账户间资金流向。",
    },
    "smallcap_stock": {
        "name": "小市值股票策略",
        "scope": "账户内",
        "description": "在广发账户内选股、轮动和下单。",
    },
    "multifactor_convertible_bond": {
        "name": "多因子可转债策略",
        "scope": "账户内",
        "description": "在华泰账户内选债、轮动和下单。",
    },
    "domestic_long_term_advisory": {
        "name": "国内长钱投顾组合",
        "scope": "托管组合",
        "description": "雪球基金托管下的国内基金长期配置。",
    },
    "overseas_long_term_advisory": {
        "name": "海外长钱投顾组合",
        "scope": "托管组合",
        "description": "雪球基金托管下的海外基金长期配置。",
    },
    "cash_management": {
        "name": "现金管理",
        "scope": "资金承载",
        "description": "保留调拨与再平衡所需的现金。",
    },
}


def account_definition(account_id: str) -> dict:
    """Return a JSON-safe account definition, including resolved strategy names."""
    definition = ACCOUNT_DEFINITIONS[account_id]
    strategy_ids = definition["strategy_ids"]
    return {
        "id": account_id,
        "label": definition["label"],
        "sub": definition["sub"],
        "asset_classes": list(definition["asset_classes"]),
        "country_exposure": definition["country_exposure"],
        "strategy_ids": list(strategy_ids),
        "strategy_names": [STRATEGY_DEFINITIONS[item]["name"] for item in strategy_ids],
        "participates_in_domestic_rebalance": definition[
            "participates_in_domestic_rebalance"
        ],
    }


def public_investment_model() -> dict:
    """Return the stable model consumed by API clients and future configuration UI."""
    return {
        "concepts": ("资产类别", "策略", "账户"),
        "country_note": "国别是资产或策略的风险暴露属性，不是独立层级。",
        "accounts": [account_definition(account_id) for account_id in ACCOUNT_DEFINITIONS],
        "strategies": [
            {"id": strategy_id, **definition}
            for strategy_id, definition in STRATEGY_DEFINITIONS.items()
        ],
    }
