from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app import plan_generation, plan_lifecycle
from app import order_sizing
from app.plan_service import PlanServiceError, build_plan_readiness

router = APIRouter(prefix="/api/plan", tags=["plan"])


@router.get("/context")
def get_plan_context(refresh_temperature: bool = False):
    return _service_response(plan_generation.build_plan_context, refresh_temperature)


@router.get("/readiness")
def get_plan_readiness():
    try:
        return _service_response(build_plan_readiness)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PLAN_READINESS_UNAVAILABLE",
                "message": "无法读取计划输入就绪度，请稍后重试。",
            },
        ) from exc


@router.get("/transfer")
def get_transfer_plan(refresh_temperature: bool = False):
    return _service_response(plan_generation.build_transfer_plan, refresh_temperature)


@router.get("")
def get_plan(refresh_temperature: bool = False):
    return _service_response(plan_generation.preview_current_plan, refresh_temperature)


@router.post("/generate")
def generate_plan():
    """Generate a full trading plan in one server-side workflow."""
    try:
        return plan_generation.generate_complete_plan(
            size_cb_orders=order_sizing.size_cb_orders,
            size_stock_orders=order_sizing.size_stock_orders,
        )
    except PlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/generated")
def get_latest_generated_plan():
    try:
        return {"generation": _with_generation_summary(plan_lifecycle.get_latest_complete_plan_status())}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PLAN_LIFECYCLE_UNAVAILABLE",
                "message": "无法读取当前计划生成状态，请稍后重试。",
            },
        ) from exc


@router.get("/generated/history")
def get_generated_plan_history():
    try:
        return {
            "history": [
                _with_generation_summary(item)
                for item in plan_lifecycle.get_plan_history()
            ]
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PLAN_HISTORY_UNAVAILABLE",
                "message": "无法读取历史计划，请稍后重试。",
            },
        ) from exc


@router.get("/generated/{plan_id}")
def get_generated_plan(plan_id: str):
    plan = plan_generation.get_generated_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    return plan


def _with_generation_summary(generation: dict | None) -> dict | None:
    if generation is None:
        return None
    summary = {
        "funding_action_count": 0,
        "account_trading_plan_count": 0,
    }
    if generation.get("status") in {"complete", "stale"} and generation.get("plan_id"):
        saved = plan_generation.get_generated_plan(generation["plan_id"]) or {}
        plan = saved.get("plan") if isinstance(saved, dict) else {}
        execution = plan.get("execution_read_model") if isinstance(plan, dict) else {}
        execution = execution if isinstance(execution, dict) else {}
        funding_plan = execution.get("funding_plan")
        funding_plan = funding_plan if isinstance(funding_plan, dict) else {}
        account_trading_plans = execution.get("account_trading_plans")
        account_trading_plans = account_trading_plans if isinstance(account_trading_plans, list) else []
        summary["funding_action_count"] = sum(
            len(group.get("actions") or []) for group in funding_plan.get("groups") or []
        )
        summary["account_trading_plan_count"] = len(account_trading_plans)
    return {**generation, "summary": summary}


def _service_response(builder, *args, **kwargs):
    try:
        return builder(*args, **kwargs)
    except PlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/{strategy}/size-orders")
def size_orders(strategy: str):
    """根据计划日期榜单 + 持仓 + 服务端账户事实，生成具体买卖张数/股数。"""
    try:
        return plan_generation.size_strategy_orders(
            strategy,
            size_cb_orders=order_sizing.size_cb_orders,
            size_stock_orders=order_sizing.size_stock_orders,
        )
    except PlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
