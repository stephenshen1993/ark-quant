from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import logging
import threading
from time import perf_counter

from datasource import db
from datasource.market_data_bundle import PreparationMetadata
from datasource.trade_calendar import resolve_effective_trading_date

Strategy = str
LOGGER = logging.getLogger(__name__)


class StrategyRunnerError(Exception):
    def __init__(self, status_code: int, detail: dict):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


@dataclass(frozen=True)
class StrategyRunResult:
    strategy: Strategy
    data_date: str
    trade_date: str | None
    run_id: int | None
    item_count: int
    generated: bool
    items: list[dict] = field(repr=False, default_factory=list)
    preparation: dict | None = field(repr=False, default=None)

    def to_ranking_response(self) -> dict:
        response = {
            "data_date": self.data_date,
            "trade_date": self.trade_date,
            "items": self.items,
        }
        if self.run_id is not None:
            response["run_id"] = self.run_id
        if self.preparation is not None:
            response["preparation"] = self.preparation
        return response


@dataclass(frozen=True)
class StrategyRunTask:
    started_at: datetime
    effective_date: date

    @property
    def effective_date_iso(self) -> str:
        return self.effective_date.isoformat()


_RUN_LOCKS: dict[Strategy, threading.Lock] = {
    "cb": threading.Lock(),
    "stock": threading.Lock(),
}


def freeze_strategy_run_task(
    *,
    effective_date: str | date | None = None,
    now: datetime | None = None,
) -> StrategyRunTask:
    started_at = now or datetime.now()
    if effective_date is None:
        frozen_date = resolve_effective_trading_date(now=started_at)
    elif isinstance(effective_date, date):
        frozen_date = effective_date
    else:
        frozen_date = date.fromisoformat(effective_date)
    return StrategyRunTask(started_at=started_at, effective_date=frozen_date)


def ensure_rankings(
    strategy: Strategy,
    plan_date: str,
    *,
    task: StrategyRunTask | None = None,
) -> StrategyRunResult:
    """Ensure rankings for one strategy and plan date exist."""
    started = perf_counter()
    _validate_strategy(strategy)
    existing = db.get_strategy_run_meta(strategy, plan_date)
    if existing:
        items = db.get_rankings_by_run_id(strategy, existing["id"])
        elapsed = int((perf_counter() - started) * 1000)
        preparation = {
            "ranking": PreparationMetadata(
                effective_date=plan_date,
                mode="cache_hit",
                last_coverage_watermark=plan_date,
                reused_records=len(items),
                stage_timings_ms={"total": elapsed},
            ).to_dict()
        }
        return _result_from_run(
            strategy,
            existing,
            items,
            generated=False,
            preparation=preparation,
        )

    task = task or freeze_strategy_run_task(effective_date=plan_date)
    if task.effective_date_iso != plan_date:
        raise ValueError("策略运行任务的有效交易日与计划基准日不一致")
    generated = run_strategy(strategy, task=task)
    if generated.data_date != plan_date:
        raise StrategyRunnerError(
            409,
            {
                "code": "STRATEGY_RANKING_DATE_MISMATCH",
                "message": (
                    f"生成的{strategy_label(strategy)}榜单日期为 "
                    f"{generated.data_date}，与计划基准日 {plan_date} 不一致。"
                ),
                "strategy": strategy,
                "data_date": generated.data_date,
                "expected": plan_date,
            },
        )
    return generated


def run_strategy(
    strategy: Strategy,
    *,
    task: StrategyRunTask | None = None,
) -> StrategyRunResult:
    """Run one strategy and return the exact persisted run just generated."""
    _validate_strategy(strategy)
    lock = _RUN_LOCKS[strategy]
    if not lock.acquire(blocking=False):
        raise StrategyRunnerError(
            409,
            {
                "code": "STRATEGY_RUNNING",
                "message": f"{strategy_label(strategy)}策略正在运行中，请稍后再试。",
            },
        )

    try:
        artifacts = _run_strategy_impl(strategy, task) if task is not None else _run_strategy_impl(strategy)
        run_id = getattr(artifacts, "run_id", None)
        if run_id is None:
            raise _persistence_error("策略运行没有返回 run_id")
        strategy_run = db.get_strategy_run(run_id, strategy)
        if strategy_run is None:
            raise _persistence_error(f"策略运行 {run_id} 缺失或未完整持久化")
        items = db.get_rankings_by_run_id(strategy, run_id)
        if not items:
            raise _persistence_error(f"策略运行 {run_id} 没有有效榜单")
        return _result_from_run(
            strategy,
            strategy_run,
            items,
            generated=True,
            preparation=getattr(artifacts, "preparation", None),
        )
    except StrategyRunnerError:
        raise
    except (Exception, SystemExit) as exc:
        msg = str(exc) if str(exc) else type(exc).__name__
        code = _classify_error(msg)
        LOGGER.exception("Strategy run failed: strategy=%s code=%s", strategy, code)
        raise StrategyRunnerError(
            500,
            {"code": code, "message": f"策略运行失败：{msg}"},
        ) from exc
    finally:
        lock.release()


def build_ranking_response(
    strategy: Strategy,
    data_date: str,
    *,
    run_id: int | None = None,
) -> dict:
    _validate_strategy(strategy)
    items = (
        db.get_rankings_by_run_id(strategy, run_id)
        if run_id is not None
        else db.get_rankings(strategy, data_date)
    )
    trade_date = items[0]["trade_date"] if items else None
    response = {"data_date": data_date, "trade_date": trade_date, "items": items}
    if run_id is not None:
        response["run_id"] = run_id
    return response


def strategy_label(strategy: Strategy) -> str:
    return "转债" if strategy == "cb" else "股票"


def _result_from_run(
    strategy: Strategy,
    strategy_run: dict,
    items: list[dict],
    *,
    generated: bool,
    preparation: dict | None = None,
) -> StrategyRunResult:
    return StrategyRunResult(
        strategy=strategy,
        data_date=strategy_run["data_date"],
        trade_date=strategy_run.get("trade_date"),
        run_id=strategy_run.get("id"),
        item_count=len(items),
        generated=generated,
        items=items,
        preparation=preparation,
    )


def _validate_strategy(strategy: Strategy) -> None:
    if strategy not in {"cb", "stock"}:
        raise StrategyRunnerError(
            400,
            {"code": "INVALID_STRATEGY", "message": f"未知策略: {strategy}"},
        )


def _persistence_error(message: str) -> StrategyRunnerError:
    return StrategyRunnerError(
        500,
        {"code": "STRATEGY_PERSISTENCE_ERROR", "message": message},
    )


def _classify_error(msg: str) -> str:
    trading_hours_markers = ("请在", "前运行", "后运行", "收盘数据")
    data_source_markers = ("无法获取", "获取失败", "没有可用缓存", "数据源")
    if any(m in msg for m in trading_hours_markers):
        return "TRADING_HOURS_LOCKED"
    if any(m in msg for m in data_source_markers):
        return "DATA_SOURCE_UNAVAILABLE"
    return "STRATEGY_ERROR"


def _run_strategy_impl(strategy: Strategy, task: StrategyRunTask | None = None):
    task = task or freeze_strategy_run_task()
    if strategy == "cb":
        from strategies.cb_rotation.run import DEFAULT_CONFIG, DEFAULT_POSITIONS, run

        return run(
            DEFAULT_CONFIG,
            DEFAULT_POSITIONS,
            effective_date=task.effective_date,
            started_at=task.started_at,
        )

    from strategies.stock_smallcap.run import DEFAULT_CONFIG, DEFAULT_POSITIONS, run

    return run(
        DEFAULT_CONFIG,
        DEFAULT_POSITIONS,
        effective_date=task.effective_date,
        started_at=task.started_at,
    )
