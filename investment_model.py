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
        "label": "资金账户",
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

# 再平衡目标不是第四类领域概念，而是资金调拨策略针对策略/组合计算的金额目标。
# summary_total_fields 暂为现有宽表兼容字段；策略跨账户时可扩展为多字段或查询聚合。
DOMESTIC_REBALANCE_TARGETS: Final[dict[str, dict]] = {
    "stock": {
        "label": "广发小市值股票",
        "strategy_id": "smallcap_stock",
        "account_ids": ("stock",),
        "summary_total_fields": ("stock_total",),
        "transfer_out_action": "银证转出",
        "transfer_in_action": "银证转入",
    },
    "bond": {
        "label": "华泰多因子可转债",
        "strategy_id": "multifactor_convertible_bond",
        "account_ids": ("cb",),
        "summary_total_fields": ("bond_total",),
        "transfer_out_action": "银证转出",
        "transfer_in_action": "银证转入",
    },
    "changqian": {
        "label": "长钱投顾组合",
        "strategy_id": "domestic_long_term_advisory",
        "account_ids": ("changqian",),
        "summary_total_fields": ("changqian_total",),
        "transfer_out_action": "基金赎回",
        "transfer_in_action": "基金申购",
        "transfer_out_note": " (T+2~T+7)",
        "transfer_in_note": " (T+1/T+2 到账)",
    },
    "cash_pool": {
        "label": "资金账户",
        "strategy_id": "cash_management",
        "account_ids": ("cash",),
        "summary_total_fields": ("cash_pool",),
        "is_transfer_hub": True,
    },
}

# 保持当前操作顺序；将来新增目标只需在此明确其可用调拨路径和优先级。
TRANSFER_TARGET_ORDER: Final[tuple[str, ...]] = ("bond", "stock", "changqian")


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


def domestic_target_current_amounts(summary: dict) -> dict[str, float]:
    """Aggregate current amounts by rebalancing target, not by account identity."""
    return {
        target_id: sum(
            float(summary.get(field, 0) or 0)
            for field in definition["summary_total_fields"]
        )
        for target_id, definition in DOMESTIC_REBALANCE_TARGETS.items()
    }


def domestic_rebalance_target_definition(target_id: str) -> dict:
    """Return a JSON-safe target definition with resolved strategy and account names."""
    definition = DOMESTIC_REBALANCE_TARGETS[target_id]
    account_ids = definition["account_ids"]
    strategy_id = definition["strategy_id"]
    return {
        "id": target_id,
        "label": definition["label"],
        "strategy_id": strategy_id,
        "strategy_name": STRATEGY_DEFINITIONS[strategy_id]["name"],
        "account_ids": list(account_ids),
        "account_names": [ACCOUNT_DEFINITIONS[item]["label"] for item in account_ids],
        "participates_in_domestic_rebalance": True,
    }


def transfer_target_definitions() -> list[dict]:
    """Return executable non-cash target definitions in the configured order."""
    return [
        {"id": target_id, **DOMESTIC_REBALANCE_TARGETS[target_id]}
        for target_id in TRANSFER_TARGET_ORDER
    ]


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
        "domestic_rebalance_targets": [
            domestic_rebalance_target_definition(target_id)
            for target_id in DOMESTIC_REBALANCE_TARGETS
        ],
    }
