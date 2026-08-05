from __future__ import annotations
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from app import strategy_runner
from datasource import db

router = APIRouter(prefix="/api/rankings", tags=["rankings"])


@router.get("/{strategy}/dates")
def get_dates(strategy: Literal["cb", "stock"]):
    return db.get_ranking_dates(strategy)


@router.get("/{strategy}/latest")
def get_latest(strategy: Literal["cb", "stock"]):
    dates = db.get_ranking_dates(strategy)
    if not dates:
        return {"data_date": None, "trade_date": None, "items": []}
    return strategy_runner.build_ranking_response(strategy, dates[0])


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
    return strategy_runner.build_ranking_response(strategy, data_date)


@router.post("/{strategy}/run")
def run_strategy(strategy: Literal["cb", "stock"]):
    """触发策略运行，生成最新榜单。同一策略同时只允许一个运行。"""
    try:
        result = strategy_runner.run_strategy(strategy)
    except strategy_runner.StrategyRunnerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return result.to_ranking_response()
