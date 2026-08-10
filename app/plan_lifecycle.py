from __future__ import annotations

from uuid import uuid4

from datasource import db

DRAFT = "draft"
RUNNING = "running"
COMPLETE = "complete"
FAILED = "failed"
STALE = "stale"

GENERATION_STAGES = ("account", "rankings", "fund_transfer", "orders", "summary")

DEFAULT_STALE_REASON = {
    "code": "PLAN_INPUTS_CHANGED",
    "message": "账户事实已更新，既有计划需要重新生成。",
}


def new_plan_id(plan_date: str) -> str:
    return f"plan-{plan_date}-{uuid4().hex[:8]}"


def generation_stages() -> list[str]:
    return list(GENERATION_STAGES)


def with_status(plan: dict, status: str) -> dict:
    return {**plan, "generation": {**plan["generation"], "status": status}}


def start(plan_id: str, plan_date: str, plan: dict) -> bool:
    return db.try_insert_running_generated_plan(
        plan_id,
        plan_date,
        with_status(plan, RUNNING),
    )


def capture_inputs(plan_id: str, plan: dict) -> bool:
    """Persist the inputs protected by the running-plan lifecycle CAS."""
    return db.update_generated_plan(
        plan_id,
        status=RUNNING,
        plan=with_status(plan, RUNNING),
        expected_status=RUNNING,
    )


def record_order_batch(
    plan_id: str,
    strategy: str,
    orders: list[dict],
    summary: dict,
) -> None:
    db.insert_plan_order_batch(plan_id, strategy, orders, summary)


def complete(plan_id: str, plan: dict) -> bool:
    return db.update_generated_plan(
        plan_id,
        status=COMPLETE,
        plan=with_status(plan, COMPLETE),
        expected_status=RUNNING,
    )


def fail(plan_id: str, plan: dict, error: dict) -> dict:
    failed_plan = with_status(plan, FAILED)
    db.update_generated_plan(
        plan_id,
        status=FAILED,
        plan=failed_plan,
        error=error,
        expected_status=RUNNING,
    )
    return failed_plan


def mark_stale(reason: dict | None = None) -> None:
    db.mark_generated_plans_stale(reason or DEFAULT_STALE_REASON)


def get_plan(plan_id: str) -> dict | None:
    return db.get_generated_plan(plan_id)


def get_latest_plan_status(plan_date: str | None = None) -> dict | None:
    if plan_date is None:
        from app.plan_service import current_plan_date

        plan_date = current_plan_date()
    return db.get_latest_generated_plan(plan_date)


def get_running_plan_status(plan_date: str) -> dict | None:
    return db.get_running_generated_plan(plan_date)


def get_order_batch(plan_id: str, strategy: str) -> dict | None:
    return db.get_plan_order_batch(plan_id, strategy)
