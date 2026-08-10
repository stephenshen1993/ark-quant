from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from app.account_read_model import build_account_read_model
from app import account_current_state, plan_lifecycle
from app.plan_execution_read_model import build_execution_read_model
from datasource import db
from datasource.youzhiyouxing import TemperatureFetchError, get_or_fetch_market_temperature
from portfolio_rebalance import PlanValidationError, build_fund_transfer_plan


class PlanServiceError(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def build_plan_context(refresh_temperature: bool = False) -> dict:
    market_temperature = _market_temperature(refresh=refresh_temperature)
    return {
        "plan_date": market_temperature.updated_at[:10],
        "market_temperature": market_temperature.to_dict(),
    }


def current_account_summary() -> dict | None:
    """Return the valuation-aware account inputs used throughout planning."""
    return account_current_state.build_current_account_summary()


def hydrate_execution_read_model(plan: dict) -> dict:
    """Rebuild derived execution UX fields for stored plans without mutating history."""
    if not isinstance(plan, dict):
        return plan
    required = ("plan_date", "account_read_model", "fund_transfer", "cb", "stock")
    if any(key not in plan for key in required):
        return plan
    return {
        **plan,
        "execution_read_model": build_execution_read_model(
            plan_date=plan["plan_date"],
            account_read_model=plan.get("account_read_model"),
            fund_transfer=plan.get("fund_transfer"),
            cb=plan.get("cb"),
            stock=plan.get("stock"),
        ),
    }


def build_plan_readiness() -> dict:
    market_temperature = _market_temperature(refresh=False)
    plan_date = market_temperature.updated_at[:10]
    input_end = account_transfer_window_end(plan_date)
    account = current_account_summary()
    errors = validate_account_inputs(
        plan_date,
        account,
        input_end,
        include_overseas=True,
    )
    account_facts = (build_account_read_model(account or {}) or {}).get("accounts", [])
    errors_by_account = {
        error["account_id"]: error
        for error in errors
        if error.get("account_id")
    }
    accounts = []
    for fact in account_facts:
        error = errors_by_account.get(fact["id"])
        if account is None:
            fact_status = "missing"
            message = "缺少账户数据"
        elif error:
            fact_status = error.get("status") or (
                "missing" if error.get("date") is None else "outside_window"
            )
            message = error["message"]
        else:
            fact_status = "ready"
            message = None
        accounts.append({
            "account_id": fact["id"],
            "account_name": fact["name"],
            "portfolio_id": fact["portfolio_id"],
            "snapshot_date": fact["snapshot_date"],
            "status": fact_status,
            "message": message,
        })
    return {
        "plan_date": plan_date,
        "input_window": {"start": plan_date, "end": input_end},
        "status": "needs_facts" if errors else "ready",
        "accounts": accounts,
        "errors": errors,
    }


def build_transfer_plan(refresh_temperature: bool = False) -> dict:
    market_temperature = _market_temperature(refresh=refresh_temperature)
    plan_date = resolve_plan_date(market_temperature.updated_at[:10])
    account = current_account_summary()
    if account:
        account = dict(account)
        account["temperature"] = market_temperature.temperature

    account_errors = validate_account_inputs(
        plan_date,
        account,
        account_transfer_window_end(plan_date),
        include_overseas=True,
    )
    if account_errors:
        raise PlanServiceError(
            409,
            {
                "code": "TRANSFER_INPUT_DATE_MISMATCH",
                "message": "资金调拨输入日期不一致，请先更新账户快照。",
                "plan_date": plan_date,
                "market_temperature": market_temperature.to_dict(),
                "errors": account_errors,
            },
        )

    try:
        fund_transfer = build_fund_transfer(account)
    except PlanValidationError as exc:
        raise PlanServiceError(
            409,
            {"code": exc.code, "message": exc.message},
        ) from exc
    targets, deltas, transfer_steps = fund_transfer_compatibility(fund_transfer)
    account_read_model = build_account_read_model(account)

    return {
        "generated_at": datetime.now().isoformat(),
        "plan_date": plan_date,
        "market_temperature": market_temperature.to_dict(),
        "account": account,
        "account_read_model": account_read_model,
        "targets": targets,
        "transfer_steps": transfer_steps,
        "transfer_deltas": deltas,
        "fund_transfer": fund_transfer,
        "warnings": plan_input_warnings(plan_date, account),
    }


def build_current_plan(refresh_temperature: bool = False) -> dict:
    market_temperature = _market_temperature(refresh=refresh_temperature)
    plan_date = market_temperature.updated_at[:10]

    account = current_account_summary()
    if account:
        account = dict(account)
        account["temperature"] = market_temperature.temperature

    account_errors = validate_account_inputs(
        plan_date,
        account,
        account_transfer_window_end(plan_date),
        include_overseas=True,
    )
    if account_errors:
        raise PlanServiceError(
            409,
            {
                "code": "PLAN_INPUT_DATE_MISMATCH",
                "message": "账户输入日期不一致，请按下方逐项更新到计划输入窗口内。",
                "plan_date": plan_date,
                "market_temperature": market_temperature.to_dict(),
                "errors": account_errors,
            },
        )
    warnings = plan_input_warnings(plan_date, account)
    trade_errors = validate_strategy_inputs(plan_date)

    cb_orders = db.get_orders("cb", plan_date)
    cb_data_date = plan_date
    cb_trade_date = trade_date_for("cb", cb_data_date)
    cb_universe = db.get_rankings("cb", plan_date)
    cb_rankings = cb_universe if not cb_orders else []

    stock_orders = db.get_orders("stock", plan_date)
    stock_data_date = plan_date
    stock_trade_date = trade_date_for("stock", stock_data_date)
    stock_rankings = db.get_rankings("stock", plan_date) if not stock_orders else []

    try:
        fund_transfer = build_fund_transfer(
            account,
            qualified_cb_count=len(cb_universe) if cb_universe else None,
            qualified_cb_lot_costs=cb_lot_costs(cb_universe) if cb_universe else None,
            include_a_internal=True,
        )
    except PlanValidationError as exc:
        raise PlanServiceError(
            409,
            {"code": exc.code, "message": exc.message},
        ) from exc
    targets, deltas, transfer_steps = fund_transfer_compatibility(fund_transfer)
    account_read_model = build_account_read_model(account)

    def order_summary(orders, cash_key: str) -> dict | None:
        if not orders or not account:
            return None
        delta = deltas.get(cash_key, 0)
        base = account.get(f"{cash_key}_available_cash", account.get(f"{cash_key}_cash", 0))
        return summarize_order_cash(base, delta, orders)

    cb_section = {
        "data_date": cb_data_date,
        "trade_date": cb_trade_date,
        "orders": cb_orders,
        "rankings": cb_rankings,
        "summary": order_summary(cb_orders, "bond"),
    }
    stock_section = {
        "data_date": stock_data_date,
        "trade_date": stock_trade_date,
        "orders": stock_orders,
        "rankings": stock_rankings,
        "summary": order_summary(stock_orders, "stock"),
    }

    return {
        "generated_at": datetime.now().isoformat(),
        "plan_date": plan_date,
        "market_temperature": market_temperature.to_dict(),
        "account": account,
        "account_read_model": account_read_model,
        "warnings": warnings,
        "trade_errors": trade_errors,
        "targets": targets,
        "transfer_steps": transfer_steps,
        "transfer_deltas": deltas,
        "fund_transfer": fund_transfer,
        "execution_sequence": execution_sequence(cb_orders, stock_orders, transfer_steps),
        "execution_read_model": build_execution_read_model(
            plan_date=plan_date,
            account_read_model=account_read_model,
            fund_transfer=fund_transfer,
            cb=cb_section,
            stock=stock_section,
        ),
        "cb": cb_section,
        "stock": stock_section,
    }


def generate_complete_plan(
    *,
    size_cb_orders: Callable[..., dict],
    size_stock_orders: Callable[..., dict],
) -> dict:
    from app import plan_generation

    return plan_generation.generate_complete_plan(
        size_cb_orders=size_cb_orders,
        size_stock_orders=size_stock_orders,
    )


def trade_date_for(strategy: str, data_date: str | None) -> str | None:
    if not data_date:
        return None
    meta = db.get_strategy_run_meta(strategy, data_date)
    return meta.get("trade_date") if meta else None


def validate_plan_inputs(plan_date: str, account: dict | None) -> list[dict]:
    return (
        validate_account_inputs(
            plan_date,
            account,
            account_input_window_end(plan_date),
            include_overseas=True,
        )
        + validate_strategy_inputs(plan_date)
    )


def prepare_complete_plan_generation() -> tuple[str, dict, object, dict]:
    from app import plan_generation

    return plan_generation.prepare_complete_plan_generation()


def ensure_rankings_for_plan_date(plan_date: str) -> None:
    from app import plan_generation

    return plan_generation.ensure_rankings_for_plan_date(plan_date)


def strategy_label(strategy: str) -> str:
    return "转债" if strategy == "cb" else "股票"


def build_generated_plan_response(
    *,
    plan_id: str,
    status: str,
    plan_date: str,
    generated_at: str,
    market_temperature,
    account: dict,
    fund_transfer: dict,
    price_snapshot: dict,
    strategy_rankings: dict,
    cb_result: dict | None,
    stock_result: dict | None,
) -> dict:
    targets, deltas, transfer_steps = fund_transfer_compatibility(fund_transfer)
    cb_orders = explain_order_targets((cb_result or {}).get("orders", []))
    stock_orders = explain_order_targets((stock_result or {}).get("orders", []))
    account_read_model = build_account_read_model(account)
    cb_section = {
        "data_date": plan_date,
        "trade_date": trade_date_for("cb", plan_date),
        "orders": cb_orders,
        "rankings": [],
        "summary": (cb_result or {}).get("summary"),
    }
    stock_section = {
        "data_date": plan_date,
        "trade_date": trade_date_for("stock", plan_date),
        "orders": stock_orders,
        "rankings": [],
        "summary": (stock_result or {}).get("summary"),
    }
    strategy_trade_dates = {
        "cb": cb_section["trade_date"],
        "stock": stock_section["trade_date"],
    }
    execution_dates = {value for value in strategy_trade_dates.values() if value}
    execution_date = execution_dates.pop() if len(execution_dates) == 1 else None
    return {
        "generated_at": generated_at,
        "plan_date": plan_date,
        "snapshot": build_plan_snapshot(
            plan_id=plan_id,
            plan_date=plan_date,
            generated_at=generated_at,
            execution_date=execution_date,
            market_temperature=market_temperature,
            account=account,
            strategy_trade_dates=strategy_trade_dates,
            price_snapshot=price_snapshot,
            strategy_rankings=strategy_rankings,
        ),
        "price_snapshot": price_snapshot,
        "market_temperature": market_temperature.to_dict(),
        "account": account,
        "account_read_model": account_read_model,
        "warnings": plan_input_warnings(plan_date, account),
        "trade_errors": [],
        "targets": targets,
        "transfer_steps": transfer_steps,
        "transfer_deltas": deltas,
        "fund_transfer": fund_transfer,
        "execution_sequence": execution_sequence(cb_orders, stock_orders, transfer_steps),
        "execution_read_model": build_execution_read_model(
            plan_date=plan_date,
            account_read_model=account_read_model,
            fund_transfer=fund_transfer,
            cb=cb_section,
            stock=stock_section,
        ),
        "generation": {
            "plan_id": plan_id,
            "plan_date": plan_date,
            "status": status,
            "stages": plan_lifecycle.generation_stages(),
        },
        "cb": cb_section,
        "stock": stock_section,
    }


def build_plan_snapshot(
    *,
    plan_id: str,
    plan_date: str,
    generated_at: str,
    execution_date: str | None = None,
    market_temperature=None,
    account: dict | None = None,
    strategy_trade_dates: dict[str, str | None] | None = None,
    price_snapshot: dict | None = None,
    strategy_rankings: dict | None = None,
) -> dict:
    """Describe the immutable facts captured for one generated plan version."""
    strategy_trade_dates = strategy_trade_dates or {}
    return {
        "version": 1,
        "plan_id": plan_id,
        "data_date": plan_date,
        "execution_date": execution_date,
        "generated_at": generated_at,
        "scope": "complete_next_trading_day",
        "input_provenance": {
            "market_temperature": {
                "data_date": plan_date,
                "updated_at": getattr(market_temperature, "updated_at", None),
            },
            "account": {
                "data_date": plan_date,
                "snapshot_dates": (account or {}).get("account_snapshot_dates", {}),
            },
            "strategies": {
                strategy: {"data_date": plan_date, "trade_date": trade_date}
                for strategy, trade_date in strategy_trade_dates.items()
            },
            "strategy_rankings": strategy_rankings or {},
            "prices": {
                "data_date": (price_snapshot or {}).get("data_date"),
                "captured_at": (price_snapshot or {}).get("captured_at"),
            },
        },
    }


def capture_frozen_plan_prices(plan_date: str, strategy_rankings: dict[str, dict] | None = None) -> dict:
    """Read the immutable plan-date close prices required by a complete plan."""

    input_end = account_input_window_end(plan_date)
    cb_positions = position_snapshot_for_plan("cb", plan_date, input_end) or {}
    stock_positions = position_snapshot_for_plan("stock", plan_date, input_end) or {}
    strategy_rankings = strategy_rankings or {
        strategy: {"items": db.get_rankings(strategy, plan_date)}
        for strategy in ("cb", "stock")
    }
    cb_rankings = strategy_rankings["cb"].get("items", [])
    stock_rankings = strategy_rankings["stock"].get("items", [])
    cb_codes = _plan_price_codes(cb_rankings, cb_positions.get("items", []), "bond_code")
    stock_codes = _plan_price_codes(stock_rankings, stock_positions.get("items", []), "stock_code")

    cb_prices = _ranking_prices(cb_rankings, "bond_code", "cb_price")
    stock_prices = _ranking_prices(stock_rankings, "stock_code", "close_price")
    missing_cb = [code for code in cb_codes if code not in cb_prices]
    missing_stock = [code for code in stock_codes if code not in stock_prices]
    cb_raw_prices = _raw_snapshot_prices(plan_date, "cb", missing_cb)
    stock_raw_prices = _raw_snapshot_prices(plan_date, "stock", missing_stock)
    cb_prices.update(cb_raw_prices)
    stock_prices.update(stock_raw_prices)
    missing = {
        "cb": [code for code in cb_codes if code not in cb_prices],
        "stock": [code for code in stock_codes if code not in stock_prices],
    }
    if any(missing.values()):
        raise PlanServiceError(
            503,
            {
                "code": "PLAN_PRICE_SNAPSHOT_INCOMPLETE",
                "message": "计划基准价不完整，无法生成冻结交易计划。",
                "missing": missing,
            },
        )
    return {
        "data_date": plan_date,
        "captured_at": datetime.now().isoformat(),
        "source": {
            "type": "plan_date_close_snapshot",
            "cb": {"ranking_count": len(cb_codes) - len(missing_cb), "raw_snapshot_count": len(cb_raw_prices)},
            "stock": {"ranking_count": len(stock_codes) - len(missing_stock), "raw_snapshot_count": len(stock_raw_prices)},
        },
        "cb": cb_prices,
        "stock": stock_prices,
    }


def _plan_price_codes(rankings: list[dict], positions: list[dict], code_key: str) -> list[str]:
    raw_codes = [row.get(code_key) for row in rankings] + [row.get("code") for row in positions]
    return list(dict.fromkeys(str(code).zfill(6) for code in raw_codes if code is not None))


def explain_order_targets(orders: list[dict]) -> list[dict]:
    """Expose target shortfalls without changing the frozen order itself."""
    explained = []
    for order in orders:
        row = dict(order)
        target = row.get("target_shares")
        ideal = row.get("ideal_target_shares", target)
        if target is not None and ideal is not None:
            row.setdefault("ideal_target_shares", ideal)
            row.setdefault("executable_target_shares", target)
            row.setdefault("residual_shares", int(ideal) - int(target))
        row.setdefault(
            "execution_reason",
            "mandatory_exit" if row.get("action") == "SELL" else "frozen_target",
        )
        explained.append(row)
    return explained


def _ranking_prices(rankings: list[dict], code_key: str, price_key: str) -> dict[str, float]:
    prices = {}
    for row in rankings:
        price = row.get(price_key)
        if price is None or pd.isna(price) or float(price) <= 0:
            continue
        prices[str(row[code_key]).zfill(6)] = float(price)
    return prices


def _raw_snapshot_prices(plan_date: str, strategy: str, codes: list[str]) -> dict[str, float]:
    """Fill non-candidate holdings only from the strategy's dated raw snapshot."""
    if not codes:
        return {}
    root = Path(__file__).resolve().parents[1] / "data" / "raw" / plan_date.replace("-", "")
    if strategy == "cb":
        path, code_key, price_key = root / "enriched_universe.csv", "bond_code", "cb_price"
    else:
        path, code_key, price_key = root / "stock_smallcap" / "merged.csv", "stock_code", "price"
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, dtype={code_key: str})
    except (OSError, ValueError):
        return {}
    if code_key not in frame or price_key not in frame:
        return {}
    raw = {
        str(row[code_key]).zfill(6): float(row[price_key])
        for _, row in frame[[code_key, price_key]].dropna().iterrows()
        if float(row[price_key]) > 0
    }
    return {code: raw[code] for code in codes if code in raw}


def generation_error(stage: str, detail) -> dict:
    if isinstance(detail, dict):
        return {
            "stage": stage,
            "code": detail.get("code", "PLAN_GENERATION_FAILED"),
            "message": detail.get("message", "完整计划生成失败"),
        }
    return {
        "stage": stage,
        "code": "PLAN_GENERATION_FAILED",
        "message": str(detail or "完整计划生成失败"),
    }


def validate_account_inputs(
    plan_date: str,
    account: dict | None,
    account_input_end: str,
    *,
    include_overseas: bool = False,
) -> list[dict]:
    errors = []
    if not account:
        errors.append({"input": "account", "date": None, "expected": plan_date, "message": "缺少账户数据"})
    else:
        read_model = build_account_read_model(account)
        account_facts = {
            item["id"]: item for item in (read_model or {}).get("accounts", [])
        }
        required_accounts = ["stock", "cb", "changqian", "cash"]
        if include_overseas:
            required_accounts.append("overseas")
        for account_id in required_accounts:
            account_fact = account_facts.get(account_id, {})
            actual = account_fact.get("snapshot_date")
            if not is_fact_date_acceptable(actual, plan_date, account_input_end):
                errors.append({
                    "input": f"account.{account_id}",
                    "kind": "account",
                    "account_id": account_id,
                    "account_name": account_fact.get("name", account_id),
                    "account_role": account_fact.get("role"),
                    "portfolio_id": account_fact.get("portfolio_id"),
                    "date": actual,
                    "expected": f"{plan_date} 至 {account_input_end}",
                    "message": f"{account_fact.get('name', account_id)}事实日期不在计划输入窗口内",
                })
            elif account_fact.get("total") is None:
                errors.append({
                    "input": f"account.{account_id}.valuation",
                    "kind": "account",
                    "account_id": account_id,
                    "account_name": account_fact.get("name", account_id),
                    "account_role": account_fact.get("role"),
                    "portfolio_id": account_fact.get("portfolio_id"),
                    "date": actual,
                    "expected": "账户估值可用",
                    "status": "valuation_unavailable",
                    "message": f"{account_fact.get('name', account_id)}行情暂不可用，计划需等待估值完成",
                })

    return errors


def build_fund_transfer(
    account: dict | None,
    *,
    qualified_cb_count: int | None = None,
    qualified_cb_lot_costs: list[float] | None = None,
    include_a_internal: bool = False,
) -> dict:
    if not account:
        raise PlanValidationError("MISSING_ACCOUNT", "缺少账户快照")
    context = {
        "temperature": account["temperature"],
        "check_type": account.get("check_type", "a_internal"),
        "new_contribution": account.get("new_contribution", 0),
        "b_purchase_limit": account.get("b_purchase_limit", 0),
        "cash_available": account.get("cash_pool", 0),
    }
    return build_fund_transfer_plan(
        account,
        context,
        qualified_cb_count,
        qualified_cb_lot_costs=qualified_cb_lot_costs,
        include_a_internal=include_a_internal,
    )


def cb_lot_costs(rankings: list[dict]) -> list[float]:
    costs = []
    for row in rankings:
        price = row.get("cb_price")
        if price is None:
            continue
        try:
            amount = float(price) * 10
        except (TypeError, ValueError):
            continue
        if amount > 0:
            costs.append(amount)
    return costs


def fund_transfer_compatibility(fund_transfer: dict) -> tuple[dict, dict, list[str]]:
    top = fund_transfer["top_level"]
    internal = fund_transfer["a_internal"]
    targets = {"A": top["targets"]["A"], "B": top["targets"]["B"], "C": top["targets"]["C"]}
    internal_deltas = internal.get("planned_deltas") or {"stock": 0.0, "bond": 0.0, "cash_pool": 0.0}
    deltas = {**top["executed_deltas"], **internal_deltas}
    if internal["status"] in {"ready", "within_threshold"}:
        targets.update(internal["final_targets"])
    top_actions = [
        action
        for action in (top["executed_actions"] + top["outflows"])
        if action.get("source") != "A" and action.get("target") != "A"
    ]
    actions = top_actions + internal.get("actions", [])
    labels = {
        "stock": "广发账户",
        "bond": "华泰账户",
        "cash_pool": "资金账户",
        "B": "海外长钱",
        "C": "国内长钱",
    }
    steps = [
        f"{labels.get(action['source'], action['source'])} → "
        f"{labels.get(action['target'], action['target'])}："
        f"{action['amount']:.2f}（{action['reason']}）"
        for action in actions
    ]
    return targets, deltas, steps


def validate_strategy_inputs(plan_date: str) -> list[dict]:
    errors = []
    required_risk_fields = {
        "cb": ("cb_price", "premium_rate", "double_low", "score"),
        "stock": ("market_cap", "pe_ttm", "roe_ex", "close_price"),
    }
    for strategy, label in (("cb", "转债榜单"), ("stock", "股票榜单")):
        dates = db.get_ranking_dates(strategy)
        has_rankings = plan_date in dates
        has_orders = bool(db.get_orders(strategy, plan_date))
        if not has_rankings and not has_orders:
            errors.append({
                "input": strategy,
                "date": dates[0] if dates else None,
                "expected": plan_date,
                "message": f"缺少 {plan_date} 的{label}",
            })
            continue
        rankings = db.get_rankings(strategy, plan_date)
        invalid = []
        for row in rankings:
            missing = [
                field for field in required_risk_fields[strategy]
                if row.get(field) is None or pd.isna(row.get(field))
            ]
            if missing:
                invalid.append({"code": row.get("bond_code") or row.get("stock_code"), "fields": missing})
        if invalid:
            errors.append({
                "input": f"{strategy}.risk_fields",
                "date": plan_date,
                "expected": "完整且有效的策略风险字段",
                "message": f"{label}存在缺失的风险字段，无法生成冻结交易计划。",
                "invalid": invalid[:20],
            })
    return errors


def plan_input_warnings(plan_date: str, account: dict | None) -> list[dict]:
    warnings = []
    if not account:
        return warnings
    account_input_end = account_input_window_end(plan_date)
    account_dates = account.get("account_snapshot_dates") or {}
    overseas_snapshot_date = account_dates.get("overseas")
    if not is_fact_date_acceptable(overseas_snapshot_date, plan_date, account_input_end):
        warnings.append({
            "input": "account.overseas",
            "date": overseas_snapshot_date,
            "expected": f"{plan_date} 至 {account_input_end}",
            "message": "海外长钱事实日期不在计划输入窗口内；它不参与国内再平衡，本次仅提示。",
        })
    return warnings


def account_input_window_end(plan_date: str) -> str:
    trade_dates = []
    for strategy in ("cb", "stock"):
        meta = db.get_strategy_run_meta(strategy, plan_date)
        if meta and meta.get("trade_date"):
            trade_dates.append(meta["trade_date"])
    if trade_dates:
        return max(trade_dates)
    return plan_date


def account_transfer_window_end(plan_date: str) -> str:
    input_end = account_input_window_end(plan_date)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    return max(input_end, today)


def is_fact_date_acceptable(fact_date: str | None, plan_date: str, window_end: str) -> bool:
    if fact_date is None:
        return False
    try:
        actual_date = date.fromisoformat(fact_date)
        start = date.fromisoformat(plan_date)
        end = date.fromisoformat(window_end)
    except ValueError:
        return False
    return start <= actual_date <= end


def current_plan_date() -> str:
    market_temperature = get_market_temperature()
    return market_temperature.updated_at[:10]


def get_market_temperature(*, refresh: bool = False):
    return _market_temperature(refresh=refresh)


def resolve_plan_date(market_date: str) -> str:
    cb_dates = set(db.get_ranking_dates("cb"))
    stock_dates = set(db.get_ranking_dates("stock"))
    common_dates = sorted(date_str for date_str in cb_dates & stock_dates if date_str <= market_date)
    return common_dates[-1] if common_dates else market_date


def strategy_cash_after_transfer(strategy: str, account: dict, deltas: dict) -> float:
    if strategy == "cb":
        return round((account.get("bond_available_cash") or 0) + (deltas.get("bond") or 0), 2)
    if strategy == "stock":
        return round((account.get("stock_available_cash") or 0) + (deltas.get("stock") or 0), 2)
    raise ValueError(f"Unknown strategy: {strategy}")


def summarize_order_cash(base_cash: float, transfer_delta: float, orders: list[dict]) -> dict:
    sells = sum(order.get("amount", 0) for order in orders if shares(order) < 0)
    buys = sum(order.get("amount", 0) for order in orders if shares(order) > 0)
    order_delta = round(sells - buys, 2)
    starting_cash = round(base_cash or 0, 2)
    transfer_delta = round(transfer_delta or 0, 2)
    return {
        "starting_cash": starting_cash,
        "transfer_delta": transfer_delta,
        "order_delta": order_delta,
        "cash_left": round(starting_cash + transfer_delta + order_delta, 2),
    }


def shares(order: dict) -> int:
    return order.get("delta_shares", order.get("shares", 0)) or 0


def ensure_order_cash_nonnegative(strategy: str, summary: dict) -> None:
    cash_left = round(summary.get("cash_left", 0) or 0, 2)
    if cash_left < -0.01:
        label = "转债账户" if strategy == "cb" else "股票账户"
        raise PlanServiceError(
            409,
            {
                "code": "INSUFFICIENT_RELEASABLE_CASH",
                "message": f"{label}可释放资金不足，无法满足本次调拨后的订单现金约束。",
                "cash_left": cash_left,
                "cash_in": round(summary.get("cash_in", 0) or 0, 2),
                "holdings_value": round(summary.get("holdings_value", 0) or 0, 2),
            },
        )


def execution_sequence(cb_orders: list[dict], stock_orders: list[dict], transfer_steps: list[str]) -> list[dict]:
    sell_actions = {"SELL", "TRIM"}
    buy_actions = {"BUY", "ADD"}

    def phase_orders(strategy: str, orders: list[dict], actions: set[str]) -> list[dict]:
        return [
            {**order, "strategy": strategy}
            for order in orders
            if order.get("action") in actions and shares(order) != 0
        ]

    return [
        {
            "phase": "sell",
            "label": "先执行账户内卖出或减仓",
            "orders": phase_orders("cb", cb_orders, sell_actions)
            + phase_orders("stock", stock_orders, sell_actions),
        },
        {
            "phase": "transfer",
            "label": "再执行当日入金，券商转出次交易日回流",
            "steps": transfer_steps,
        },
        {
            "phase": "buy",
            "label": "最后买入或加仓",
            "orders": phase_orders("cb", cb_orders, buy_actions)
            + phase_orders("stock", stock_orders, buy_actions),
        },
    ]


def position_snapshot_for_plan(strategy: str, plan_date: str, input_end: str) -> dict | None:
    return db.get_position_snapshot_between(strategy, plan_date, input_end)


def positions_for_plan(strategy: str, plan_date: str, input_end: str) -> list[dict]:
    snapshot = position_snapshot_for_plan(strategy, plan_date, input_end)
    if snapshot is None:
        return []
    return snapshot["items"]


def ensure_plan_inputs_consistent() -> tuple[str, dict, dict]:
    from app import plan_generation

    return plan_generation.prepare_strategy_order_context()


def _market_temperature(*, refresh: bool = False):
    try:
        return get_or_fetch_market_temperature(refresh=refresh)
    except TemperatureFetchError as exc:
        raise PlanServiceError(
            503,
            {
                "code": "MARKET_TEMPERATURE_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc
