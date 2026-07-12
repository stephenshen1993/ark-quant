from __future__ import annotations
import threading
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from datasource import db

router = APIRouter(prefix="/api/rankings", tags=["rankings"])

_RUN_LOCKS: dict[str, threading.Lock] = {"cb": threading.Lock(), "stock": threading.Lock()}


@router.get("/{strategy}/dates")
def get_dates(strategy: Literal["cb", "stock"]):
    return db.get_ranking_dates(strategy)


@router.get("/{strategy}/latest")
def get_latest(strategy: Literal["cb", "stock"]):
    dates = db.get_ranking_dates(strategy)
    if not dates:
        return {"data_date": None, "trade_date": None, "items": []}
    return _build_response(strategy, dates[0])


@router.get("/{strategy}/{data_date}/export")
def export_md(strategy: Literal["cb", "stock"], data_date: str):
    items = db.get_rankings(strategy, data_date)
    if not items:
        raise HTTPException(404, "榜单不存在")
    trade_date = items[0].get("trade_date", "")
    lines = [
        f"# {'转债' if strategy == 'cb' else '股票'} Top 20 榜单",
        f"- 数据日期：{data_date}  适用交易日：{trade_date}",
        "",
    ]
    if strategy == "cb":
        lines += ["| 排名 | 代码 | 名称 | 价格 | 溢价率 | 双低值 | 评分 |",
                  "|---|---|---|---:|---:|---:|---:|"]
        for r in items:
            lines.append(
                f"| {r['rank']} | {r['bond_code']} | {r['bond_name']} "
                f"| {r['cb_price']:.2f} | {r['premium_rate']:.2f}% "
                f"| {r['double_low']:.2f} | {r['score']:.3f} |"
            )
    else:
        lines += ["| 排名 | 代码 | 名称 | 总市值(亿) | PE | 扣非ROE |",
                  "|---|---|---|---:|---:|---:|"]
        for r in items:
            mc = f"{r['market_cap']:.2f}" if r['market_cap'] is not None else "-"
            pe = f"{r['pe_ttm']:.2f}" if r['pe_ttm'] is not None else "-"
            roe = f"{r['roe_ex']:.2f}%" if r['roe_ex'] is not None else "-"
            lines.append(
                f"| {r['rank']} | {r['stock_code']} | {r['stock_name']} "
                f"| {mc} | {pe} | {roe} |"
            )
    return PlainTextResponse("\n".join(lines))


@router.get("/{strategy}/{data_date}")
def get_by_date(strategy: Literal["cb", "stock"], data_date: str):
    return _build_response(strategy, data_date)


@router.post("/{strategy}/run")
def run_strategy(strategy: Literal["cb", "stock"]):
    """触发策略运行，生成最新榜单。同一策略同时只允许一个运行。"""
    lock = _RUN_LOCKS[strategy]
    if not lock.acquire(blocking=False):
        raise HTTPException(
            409,
            {
                "code": "STRATEGY_RUNNING",
                "message": f"{'转债' if strategy == 'cb' else '股票'}策略正在运行中，请稍后再试。",
            },
        )

    try:
        artifacts = _run_strategy_impl(strategy)
        run_id = getattr(artifacts, "run_id", None)
        if run_id is None:
            raise RuntimeError("Strategy persistence did not return a run_id")
        strategy_run = db.get_strategy_run(run_id, strategy)
        if strategy_run is None:
            raise RuntimeError(f"Persisted strategy run {run_id} is incomplete or missing")
        response = _build_response(
            strategy,
            strategy_run["data_date"],
            run_id=run_id,
        )
        if not response["items"]:
            raise RuntimeError(f"Persisted strategy run {run_id} has no rankings")
    except (Exception, SystemExit) as exc:
        msg = str(exc) if str(exc) else type(exc).__name__
        code = _classify_error(msg)
        raise HTTPException(500, {"code": code, "message": f"策略运行失败：{msg}"}) from exc
    finally:
        lock.release()

    return response


def _classify_error(msg: str) -> str:
    """Map exception message text to a machine-readable error code."""
    trading_hours_markers = ("请在", "前运行", "后运行", "收盘数据")
    data_source_markers = ("无法获取", "获取失败", "没有可用缓存", "数据源")
    if any(m in msg for m in trading_hours_markers):
        return "TRADING_HOURS_LOCKED"
    if any(m in msg for m in data_source_markers):
        return "DATA_SOURCE_UNAVAILABLE"
    return "STRATEGY_ERROR"


def _run_strategy_impl(strategy: str):
    if strategy == "cb":
        from strategies.cb_rotation.run import run, DEFAULT_CONFIG, DEFAULT_POSITIONS

        return run(DEFAULT_CONFIG, DEFAULT_POSITIONS)
    else:
        from strategies.stock_smallcap.run import run, DEFAULT_CONFIG, DEFAULT_POSITIONS

        return run(DEFAULT_CONFIG, DEFAULT_POSITIONS)


def _build_response(strategy: str, data_date: str, *, run_id: int | None = None) -> dict:
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
