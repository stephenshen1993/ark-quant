from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app import order_sizing
from app import plan_service
from app.plan_service import PlanServiceError

router = APIRouter(prefix="/api/plan", tags=["plan"])


@router.get("/context")
def get_plan_context(refresh_temperature: bool = False):
    return _service_response(plan_service.build_plan_context, refresh_temperature)


@router.get("/transfer")
def get_transfer_plan(refresh_temperature: bool = False):
    return _service_response(plan_service.build_transfer_plan, refresh_temperature)


@router.get("")
def get_plan(refresh_temperature: bool = False):
    return _service_response(plan_service.build_current_plan, refresh_temperature)


@router.post("/generate")
def generate_plan():
    """Generate a full trading plan in one server-side workflow."""
    try:
        return plan_service.generate_complete_plan(
            size_cb_orders=order_sizing.size_cb_orders,
            size_stock_orders=order_sizing.size_stock_orders,
        )
    except PlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _service_response(builder, *args, **kwargs):
    try:
        return builder(*args, **kwargs)
    except PlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/{strategy}/size-orders")
def size_orders(strategy: str):
    """根据计划日期榜单 + 持仓 + 服务端账户事实，生成具体买卖张数/股数。"""
    try:
        return order_sizing.size_strategy_orders(strategy)
    except PlanServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
