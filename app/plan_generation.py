from __future__ import annotations

from typing import Callable

from app import plan_lifecycle
from app import plan_service
from app import strategy_runner
from datasource import db
from portfolio_rebalance import PlanValidationError


def build_plan_context(refresh_temperature: bool = False) -> dict:
    return plan_service.build_plan_context(refresh_temperature=refresh_temperature)


def build_transfer_plan(refresh_temperature: bool = False) -> dict:
    return plan_service.build_transfer_plan(refresh_temperature=refresh_temperature)


def preview_current_plan(refresh_temperature: bool = False) -> dict:
    return plan_service.build_current_plan(refresh_temperature=refresh_temperature)


def get_generated_plan(plan_id: str) -> dict | None:
    return plan_lifecycle.get_plan(plan_id)


def generate_complete_plan(
    *,
    size_cb_orders: Callable[..., dict],
    size_stock_orders: Callable[..., dict],
) -> dict:
    plan_date, account, market_temperature, fund_transfer = prepare_complete_plan_generation()
    plan_id = plan_lifecycle.new_plan_id(plan_date)
    failed_stage = "cb_orders"
    plan = plan_service.build_generated_plan_response(
        plan_id=plan_id,
        status=plan_lifecycle.RUNNING,
        plan_date=plan_date,
        market_temperature=market_temperature,
        account=account,
        fund_transfer=fund_transfer,
        cb_result=None,
        stock_result=None,
    )
    plan_lifecycle.start(plan_id, plan_date, plan)

    try:
        _, deltas, _ = plan_service.fund_transfer_compatibility(fund_transfer)
        cb_result = size_cb_orders(
            plan_service.strategy_cash_after_transfer("cb", account, deltas),
            plan_date=plan_date,
        )
        plan_lifecycle.record_order_batch(
            plan_id, "cb", cb_result["orders"], cb_result["summary"]
        )
        plan = plan_service.build_generated_plan_response(
            plan_id=plan_id,
            status=plan_lifecycle.RUNNING,
            plan_date=plan_date,
            market_temperature=market_temperature,
            account=account,
            fund_transfer=fund_transfer,
            cb_result=cb_result,
            stock_result=None,
        )

        failed_stage = "stock_orders"
        stock_result = size_stock_orders(
            plan_service.strategy_cash_after_transfer("stock", account, deltas),
            plan_date=plan_date,
        )
        plan_lifecycle.record_order_batch(
            plan_id, "stock", stock_result["orders"], stock_result["summary"]
        )
        plan = plan_service.build_generated_plan_response(
            plan_id=plan_id,
            status=plan_lifecycle.COMPLETE,
            plan_date=plan_date,
            market_temperature=market_temperature,
            account=account,
            fund_transfer=fund_transfer,
            cb_result=cb_result,
            stock_result=stock_result,
        )
        plan_lifecycle.complete(plan_id, plan)
        return plan
    except Exception as exc:
        status_code = getattr(exc, "status_code", 500)
        detail = getattr(exc, "detail", str(exc))
        error = plan_service.generation_error(
            failed_stage,
            detail,
        )
        plan_lifecycle.fail(plan_id, plan, error)
        raise plan_service.PlanServiceError(status_code, {**error, "plan_id": plan_id}) from exc


def prepare_complete_plan_generation() -> tuple[str, dict, object, dict]:
    plan_date = plan_service.current_plan_date()
    account = db.get_current_account_summary()
    account_errors = plan_service.validate_account_inputs(
        plan_date,
        account,
        plan_service.account_transfer_window_end(plan_date),
        include_overseas=True,
    )
    if account_errors:
        raise plan_service.PlanServiceError(
            409,
            {
                "stage": "plan_inputs",
                "code": "PLAN_INPUT_DATE_MISMATCH",
                "message": "账户输入日期不一致，请按下方逐项更新到计划输入窗口内。",
                "plan_date": plan_date,
                "errors": account_errors,
            },
        )

    ensure_rankings_for_plan_date(plan_date)

    strategy_errors = plan_service.validate_strategy_inputs(plan_date)
    if strategy_errors:
        raise plan_service.PlanServiceError(
            409,
            {
                "stage": "strategy_rankings",
                "code": "PLAN_INPUT_DATE_MISMATCH",
                "message": "交易计划输入日期不一致，请按下方逐项更新到同一个计划日。",
                "plan_date": plan_date,
                "errors": strategy_errors,
            },
        )

    market_temperature = plan_service.get_market_temperature()
    account = dict(account)
    account["temperature"] = market_temperature.temperature
    cb_rankings = db.get_rankings("cb", plan_date)
    try:
        fund_transfer = plan_service.build_fund_transfer(
            account,
            qualified_cb_count=len(cb_rankings) if cb_rankings else None,
            qualified_cb_lot_costs=plan_service.cb_lot_costs(cb_rankings)
            if cb_rankings
            else None,
            include_a_internal=True,
        )
    except PlanValidationError as exc:
        raise plan_service.PlanServiceError(
            409,
            {
                "stage": "fund_transfer",
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc
    return plan_date, account, market_temperature, fund_transfer


def ensure_rankings_for_plan_date(plan_date: str) -> None:
    for strategy in ("cb", "stock"):
        try:
            strategy_runner.ensure_rankings(strategy, plan_date)
        except strategy_runner.StrategyRunnerError as exc:
            detail = exc.detail
            if detail.get("code") == "STRATEGY_RANKING_DATE_MISMATCH":
                detail = {
                    "stage": "strategy_rankings",
                    "code": "PLAN_INPUT_DATE_MISMATCH",
                    "message": detail["message"],
                    "plan_date": plan_date,
                    "errors": [
                        {
                            "input": strategy,
                            "date": detail.get("data_date"),
                            "expected": plan_date,
                            "message": "榜单日期与计划基准日不一致",
                        }
                    ],
                }
            else:
                detail = {
                    "stage": "strategy_rankings",
                    **detail,
                }
            raise plan_service.PlanServiceError(exc.status_code, detail) from exc


def size_strategy_orders(
    strategy: str,
    *,
    size_cb_orders: Callable[..., dict],
    size_stock_orders: Callable[..., dict],
) -> dict:
    if strategy not in {"cb", "stock"}:
        raise plan_service.PlanServiceError(
            400,
            {"code": "INVALID_STRATEGY", "message": f"未知策略: {strategy}"},
        )

    plan_date, account, deltas = prepare_strategy_order_context()
    if strategy == "cb":
        return size_cb_orders(
            plan_service.strategy_cash_after_transfer("cb", account, deltas),
            plan_date=plan_date,
        )
    return size_stock_orders(
        plan_service.strategy_cash_after_transfer("stock", account, deltas),
        plan_date=plan_date,
    )


def prepare_strategy_order_context() -> tuple[str, dict, dict]:
    plan_date = plan_service.current_plan_date()
    account = db.get_current_account_summary()
    errors = plan_service.validate_plan_inputs(plan_date, account)
    if errors:
        raise plan_service.PlanServiceError(
            409,
            {
                "stage": "plan_inputs",
                "code": "PLAN_INPUT_DATE_MISMATCH",
                "message": "交易计划输入日期不一致，请按下方逐项更新到同一个计划日。",
                "plan_date": plan_date,
                "errors": errors,
            },
        )
    account = dict(account)
    market_temperature = plan_service.get_market_temperature()
    account["temperature"] = market_temperature.temperature
    cb_rankings = db.get_rankings("cb", plan_date)
    try:
        fund_transfer = plan_service.build_fund_transfer(
            account,
            qualified_cb_count=len(cb_rankings) if cb_rankings else None,
            qualified_cb_lot_costs=plan_service.cb_lot_costs(cb_rankings)
            if cb_rankings
            else None,
            include_a_internal=True,
        )
    except PlanValidationError as exc:
        raise plan_service.PlanServiceError(
            409,
            {
                "stage": "fund_transfer",
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc
    _, deltas, _ = plan_service.fund_transfer_compatibility(fund_transfer)
    return plan_date, account, deltas
