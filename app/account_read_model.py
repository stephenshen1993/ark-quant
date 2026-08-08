from __future__ import annotations

from investment_model import ACCOUNT_DEFINITIONS, STRATEGY_DEFINITIONS

ACTIVE_PORTFOLIO = "A"
OVERSEAS_LONG_TERM_PORTFOLIO = "B"
DOMESTIC_LONG_TERM_PORTFOLIO = "C"

ACCOUNT_FACT_ORDER = ("stock", "cb", "cash", "overseas", "changqian")

ACCOUNT_FACT_FIELDS = {
    "stock": {
        "total": "stock_total",
        "cash": "stock_cash",
        "available_cash": "stock_available_cash",
        "frozen_cash": "stock_frozen_cash",
        "portfolio_id": ACTIVE_PORTFOLIO,
        "portfolio_node_id": "a_smallcap_stock",
        "portfolio_node_name": "小市值股票策略",
        "role": "A 组合小市值股票策略承载账户",
        "legacy_strategy": "stock",
    },
    "cb": {
        "total": "bond_total",
        "cash": "bond_cash",
        "available_cash": "bond_available_cash",
        "frozen_cash": "bond_frozen_cash",
        "portfolio_id": ACTIVE_PORTFOLIO,
        "portfolio_node_id": "a_convertible_bond",
        "portfolio_node_name": "多因子可转债策略",
        "role": "A 组合多因子可转债策略承载账户",
        "legacy_strategy": "cb",
    },
    "cash": {
        "total": "cash_pool",
        "cash": None,
        "available_cash": None,
        "frozen_cash": None,
        "portfolio_id": ACTIVE_PORTFOLIO,
        "portfolio_node_id": "a_cash_pool",
        "portfolio_node_name": "现金池",
        "role": "A 组合现金池承载账户",
        "legacy_strategy": "cash_pool",
    },
    "overseas": {
        "total": "overseas_total",
        "cash": None,
        "available_cash": None,
        "frozen_cash": None,
        "portfolio_id": OVERSEAS_LONG_TERM_PORTFOLIO,
        "portfolio_node_id": "b_overseas_long_term",
        "portfolio_node_name": "海外长钱组合",
        "role": "B 组合当前承载通道",
        "legacy_strategy": "overseas",
    },
    "changqian": {
        "total": "changqian_total",
        "cash": None,
        "available_cash": None,
        "frozen_cash": None,
        "portfolio_id": DOMESTIC_LONG_TERM_PORTFOLIO,
        "portfolio_node_id": "c_domestic_long_term",
        "portfolio_node_name": "国内长钱组合",
        "role": "C 组合当前承载通道",
        "legacy_strategy": "changqian",
    },
}

PORTFOLIO_DEFINITIONS = {
    ACTIVE_PORTFOLIO: {
        "id": ACTIVE_PORTFOLIO,
        "name": "主动组合",
        "kind": "top_portfolio",
        "description": "核心进攻与主动超额来源。",
        "child_node_ids": ("a_smallcap_stock", "a_convertible_bond", "a_cash_pool"),
    },
    OVERSEAS_LONG_TERM_PORTFOLIO: {
        "id": OVERSEAS_LONG_TERM_PORTFOLIO,
        "name": "海外长钱",
        "kind": "top_portfolio",
        "description": "海外长期收益、国别与货币分散、第二收益来源。",
        "child_node_ids": ("b_overseas_long_term",),
    },
    DOMESTIC_LONG_TERM_PORTFOLIO: {
        "id": DOMESTIC_LONG_TERM_PORTFOLIO,
        "name": "国内长钱",
        "kind": "top_portfolio",
        "description": "国内长钱底仓、偏大盘风格补充、可信投顾托管。",
        "child_node_ids": ("c_domestic_long_term",),
    },
}


def build_account_read_model(summary: dict | None) -> dict | None:
    if summary is None:
        return None
    account_facts = [_account_fact(account_id, summary) for account_id in ACCOUNT_FACT_ORDER]
    portfolio_nodes = _portfolio_nodes(account_facts)
    portfolios = _top_portfolios(portfolio_nodes)
    return {
        "context": _context(summary),
        "accounts": account_facts,
        "strategies": _strategy_facts(account_facts),
        "portfolio_nodes": portfolio_nodes,
        "portfolios": portfolios,
        "legacy_adapter": _legacy_adapter(account_facts),
    }


def _context(summary: dict) -> dict:
    return {
        "snapshot_date": summary.get("snapshot_date"),
        "temperature": summary.get("temperature"),
        "check_type": summary.get("check_type", "a_internal"),
        "new_contribution": summary.get("new_contribution", 0) or 0,
        "b_purchase_status": summary.get("b_purchase_status", "unchecked"),
        "b_purchase_limit": summary.get("b_purchase_limit", 0) or 0,
        "b_purchase_checked_at": summary.get("b_purchase_checked_at"),
        "b_purchase_source": summary.get("b_purchase_source"),
        "total_assets": _nullable_amount(summary.get("total_assets")),
    }


def _account_fact(account_id: str, summary: dict) -> dict:
    fields = ACCOUNT_FACT_FIELDS[account_id]
    definition = ACCOUNT_DEFINITIONS[account_id]
    model_ids = list(definition["strategy_ids"])
    strategy_ids = _account_internal_strategy_ids(model_ids)
    carrier_ids = [item for item in model_ids if item not in strategy_ids]
    return {
        "id": account_id,
        "kind": "account",
        "name": definition["label"],
        "role": fields["role"],
        "portfolio_id": fields["portfolio_id"],
        "portfolio_node_id": fields["portfolio_node_id"],
        "portfolio_node_name": fields["portfolio_node_name"],
        "strategy_ids": strategy_ids,
        "strategy_names": [STRATEGY_DEFINITIONS[item]["name"] for item in strategy_ids],
        "carrier_strategy_ids": carrier_ids,
        "carrier_strategy_names": [
            STRATEGY_DEFINITIONS[item]["name"] for item in carrier_ids
        ],
        "asset_classes": list(definition["asset_classes"]),
        "country_exposure": definition["country_exposure"],
        "total": _nullable_amount(summary.get(fields["total"])),
        "cash": _nullable_amount(summary.get(fields["cash"])) if fields["cash"] else None,
        "available_cash": (
            _nullable_amount(summary.get(fields["available_cash"]))
            if fields["available_cash"]
            else None
        ),
        "frozen_cash": (
            _nullable_amount(summary.get(fields["frozen_cash"]))
            if fields["frozen_cash"]
            else None
        ),
        "snapshot_date": summary.get("account_snapshot_dates", {}).get(account_id),
        "updated_at": summary.get("account_updated_at", {}).get(account_id),
        "legacy": {
            "account_id": account_id,
            "strategy": fields["legacy_strategy"],
            "total_field": fields["total"],
        },
    }


def _strategy_facts(account_facts: list[dict]) -> list[dict]:
    strategy_accounts: dict[str, list[dict]] = {}
    for account in account_facts:
        for strategy_id in account["strategy_ids"]:
            strategy_accounts.setdefault(strategy_id, []).append(account)
    return [
        {
            "id": strategy_id,
            "kind": "strategy",
            "name": STRATEGY_DEFINITIONS[strategy_id]["name"],
            "scope": STRATEGY_DEFINITIONS[strategy_id]["scope"],
            "account_ids": [account["id"] for account in accounts],
            "account_names": [account["name"] for account in accounts],
            "portfolio_ids": sorted({account["portfolio_id"] for account in accounts}),
            "portfolio_node_ids": [account["portfolio_node_id"] for account in accounts],
        }
        for strategy_id, accounts in strategy_accounts.items()
    ]


def _account_internal_strategy_ids(strategy_ids: list[str]) -> list[str]:
    return [
        strategy_id
        for strategy_id in strategy_ids
        if STRATEGY_DEFINITIONS[strategy_id]["scope"] == "账户内"
    ]


def _portfolio_nodes(account_facts: list[dict]) -> list[dict]:
    nodes: dict[str, dict] = {}
    for account in account_facts:
        node = nodes.setdefault(
            account["portfolio_node_id"],
            {
                "id": account["portfolio_node_id"],
                "kind": "portfolio_node",
                "name": account["portfolio_node_name"],
                "portfolio_id": account["portfolio_id"],
                "account_ids": [],
                "account_names": [],
                "strategy_ids": [],
                "current_amount": 0.0,
            },
        )
        node["account_ids"].append(account["id"])
        node["account_names"].append(account["name"])
        node["strategy_ids"].extend(account["strategy_ids"])
        if node["current_amount"] is not None:
            node["current_amount"] = (
                None
                if account["total"] is None
                else round(node["current_amount"] + account["total"], 2)
            )
    for node in nodes.values():
        node["strategy_ids"] = list(dict.fromkeys(node["strategy_ids"]))
    return list(nodes.values())


def _top_portfolios(portfolio_nodes: list[dict]) -> list[dict]:
    node_by_id = {node["id"]: node for node in portfolio_nodes}
    portfolios = []
    for portfolio_id, definition in PORTFOLIO_DEFINITIONS.items():
        children = [
            node_by_id[node_id]
            for node_id in definition["child_node_ids"]
            if node_id in node_by_id
        ]
        portfolios.append({
            "id": portfolio_id,
            "kind": definition["kind"],
            "name": definition["name"],
            "description": definition["description"],
            "child_node_ids": [child["id"] for child in children],
            "account_ids": [
                account_id
                for child in children
                for account_id in child["account_ids"]
            ],
            "current_amount": (
                None
                if any(child["current_amount"] is None for child in children)
                else round(sum(child["current_amount"] for child in children), 2)
            ),
        })
    return portfolios


def _legacy_adapter(account_facts: list[dict]) -> dict:
    return {
        "note": "legacy.strategy 仅用于兼容旧 DB/API 命名；新调用方应使用 account/strategy/portfolio 字段。",
        "strategy_to_account_id": {
            account["legacy"]["strategy"]: account["id"] for account in account_facts
        },
    }


def _amount(value) -> float:
    return round(float(value or 0), 2)


def _nullable_amount(value) -> float | None:
    if value is None:
        return None
    return _amount(value)
