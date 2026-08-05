from __future__ import annotations

import argparse
import importlib
import logging
import os
import platform
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # Keep the diagnostic runnable before dependencies are installed.
    load_dotenv = None


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "data" / "output" / "cb_data_probe_report.md"
LOG_PATH = ROOT / "logs" / "cb_data_probe.log"


@dataclass
class SourceResult:
    name: str
    category: str
    status: str
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    reason: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class StockMomentumProbe:
    sample_size: int
    requested_codes: list[str] = field(default_factory=list)
    succeeded: dict[str, float] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    endpoint: str = "AKShare stock_zh_a_hist (东方财富)"


FIELD_ALIASES = {
    "转债代码": ["债券代码", "转债代码", "代码", "bond_id", "ts_code"],
    "转债名称": ["债券简称", "转债名称", "名称", "bond_nm", "bond_short_name"],
    "转债现价": ["债现价", "现价", "最新价", "转债最新价", "price", "close"],
    "转股溢价率": ["转股溢价率", "溢价率", "premium_rt"],
    "正股代码": ["正股代码", "stock_id", "stock_code", "stk_code"],
    "正股名称": ["正股简称", "正股名称", "stock_nm", "stock_name"],
    "双低值": ["双低", "双低值", "dblow"],
    "剩余规模": ["剩余规模", "债券余额", "余额", "remain_size", "remain_size_100m"],
    "到期收益率 YTM": ["到期税前收益", "到期税后收益", "到期收益率", "ytm_rt", "ytm"],
    "信用评级": ["评级", "信用评级", "rating"],
    "强赎状态": ["强赎状态", "强赎", "redeem_flag", "redeem_status"],
    "到期日": ["到期日", "到期时间", "到期日期", "maturity_dt"],
    "剩余年限": ["剩余年限", "remain_years"],
}

REQUIRED_FIELDS = ["转债代码", "转债名称", "转债现价", "转股溢价率", "正股代码", "正股名称"]
ENHANCED_FIELDS = [
    "双低值",
    "剩余规模",
    "到期收益率 YTM",
    "信用评级",
    "强赎状态",
    "到期日",
    "剩余年限",
    "正股 20 日动量",
]


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


def load_environment() -> None:
    if load_dotenv is None:
        logging.warning("python-dotenv 未安装，跳过 .env 文件加载；仍会读取当前进程环境变量。")
        return
    load_dotenv(ROOT / ".env")


def missing_rates(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        return {str(column): 100.0 for column in frame.columns}
    normalized = frame.replace(r"^\s*$", pd.NA, regex=True)
    return {str(column): round(float(normalized[column].isna().mean() * 100), 2) for column in normalized.columns}


def calculate_momentum_20d(close: pd.Series) -> float:
    numeric = pd.to_numeric(close, errors="coerce").dropna()
    if len(numeric) < 21:
        raise ValueError(f"有效收盘价不足 21 个交易日，实际为 {len(numeric)}")
    return float(numeric.iloc[-1] / numeric.iloc[-21] - 1)


def _available_alias(frame: pd.DataFrame, aliases: Iterable[str]) -> str | None:
    columns = {str(column).strip().lower(): str(column) for column in frame.columns}
    for alias in aliases:
        matched = columns.get(alias.strip().lower())
        if matched is not None and frame[matched].replace(r"^\s*$", pd.NA, regex=True).notna().any():
            return matched
    return None


def detect_supported_fields(
    frames: dict[str, pd.DataFrame], momentum_available: bool
) -> dict[str, dict[str, object]]:
    support: dict[str, dict[str, object]] = {}
    for field_name in REQUIRED_FIELDS + ENHANCED_FIELDS:
        if field_name == "正股 20 日动量":
            support[field_name] = {
                "available": momentum_available,
                "source": "东方财富正股历史行情样本" if momentum_available else "",
            }
            continue
        sources: list[str] = []
        columns: list[str] = []
        for source_name, frame in frames.items():
            matched = _available_alias(frame, FIELD_ALIASES[field_name])
            if matched is not None:
                sources.append(source_name)
                columns.append(matched)
        support[field_name] = {"available": bool(sources), "source": ", ".join(sources), "columns": ", ".join(columns)}

    if not support["双低值"]["available"]:
        can_derive = support["转债现价"]["available"] and support["转股溢价率"]["available"]
        if can_derive:
            support["双低值"] = {"available": True, "source": "可由转债现价 + 转股溢价率计算", "columns": "派生字段"}
    return support


def probe_akshare_core(ak) -> SourceResult:
    try:
        frame = ak.bond_zh_cov()
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise RuntimeError("接口返回空表")
        return SourceResult("AKShare bond_zh_cov", "核心数据源", "成功", frame)
    except Exception as exc:
        return SourceResult("AKShare bond_zh_cov", "核心数据源", "失败", reason=str(exc))


def probe_jsl(ak, cookie: str | None) -> SourceResult:
    if not cookie:
        return SourceResult("集思录 bond_cb_jsl", "增强数据源", "跳过", reason="未配置 JSL_COOKIE")
    try:
        frame = ak.bond_cb_jsl(cookie=cookie)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise RuntimeError("接口返回空表")
        return SourceResult("集思录 bond_cb_jsl", "增强数据源", "成功", frame)
    except Exception as exc:
        logging.warning("集思录增强数据源获取失败: %s", exc)
        return SourceResult("集思录 bond_cb_jsl", "增强数据源", "失败", reason=str(exc))


def probe_tushare(token: str | None) -> list[SourceResult]:
    names = ("Tushare cb_basic", "Tushare cb_daily")
    if not token:
        return [SourceResult(name, "可选数据源", "跳过", reason="未配置 TUSHARE_TOKEN") for name in names]
    try:
        ts = importlib.import_module("tushare")
    except ImportError:
        return [SourceResult(name, "可选数据源", "跳过", reason="未安装可选依赖 tushare") for name in names]

    try:
        pro = ts.pro_api(token)
    except Exception as exc:
        logging.warning("Tushare 客户端初始化失败: %s", exc)
        return [SourceResult(name, "可选数据源", "失败", reason=str(exc)) for name in names]
    results: list[SourceResult] = []
    for name, function_name in zip(names, ("cb_basic", "cb_daily")):
        try:
            frame = getattr(pro, function_name)()
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                raise RuntimeError("接口返回空表")
            results.append(SourceResult(name, "可选数据源", "成功", frame))
        except Exception as exc:
            logging.warning("%s 获取失败: %s", name, exc)
            results.append(SourceResult(name, "可选数据源", "失败", reason=str(exc)))
    return results


def _first_column(frame: pd.DataFrame, aliases: Iterable[str]) -> str | None:
    columns = {str(column).strip().lower(): str(column) for column in frame.columns}
    for alias in aliases:
        if alias.strip().lower() in columns:
            return columns[alias.strip().lower()]
    return None


def extract_stock_codes(frame: pd.DataFrame, limit: int) -> list[str]:
    column = _first_column(frame, FIELD_ALIASES["正股代码"])
    if column is None:
        return []
    codes = (
        frame[column]
        .astype(str)
        .str.extract(r"(\d{6})", expand=False)
        .dropna()
        .drop_duplicates()
        .head(limit)
    )
    return codes.tolist()


def probe_stock_momentum(ak, core_frame: pd.DataFrame, sample_size: int) -> StockMomentumProbe:
    probe = StockMomentumProbe(sample_size=sample_size)
    probe.requested_codes = extract_stock_codes(core_frame, sample_size)
    end = date.today()
    start = end - timedelta(days=90)
    for code in probe.requested_codes:
        try:
            history = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            )
            close_column = _first_column(history, ["收盘", "close"])
            if close_column is None:
                raise ValueError("历史行情缺少收盘价字段")
            probe.succeeded[code] = calculate_momentum_20d(history[close_column].tail(30))
        except Exception as exc:
            probe.failed[code] = str(exc)
            logging.warning("正股 %s 动量样本探测失败: %s", code, exc)
    return probe


def stock_probe_source_result(probe: StockMomentumProbe) -> SourceResult:
    requested = len(probe.requested_codes)
    succeeded = len(probe.succeeded)
    if requested == 0:
        return SourceResult("东方财富正股历史行情", "增强数据源", "跳过", reason="没有可供探测的正股代码")
    if succeeded == 0:
        return SourceResult("东方财富正股历史行情", "增强数据源", "失败", reason=f"成功计算动量 {succeeded}/{requested}")
    reason = "" if succeeded == requested else f"部分成功：成功计算动量 {succeeded}/{requested}"
    return SourceResult("东方财富正股历史行情", "增强数据源", "成功", reason=reason)


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_无数据_"
    return frame.to_markdown(index=False)


def _redact(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _feasibility(field_support: dict[str, dict[str, object]]) -> list[str]:
    def available(field: str) -> bool:
        return bool(field_support.get(field, {}).get("available"))

    pure_double_low = available("转债现价") and available("转股溢价率")
    premium_momentum = available("转股溢价率") and available("正股 20 日动量")
    safety_fields = ["剩余规模", "信用评级", "强赎状态", "到期日"]
    premium_momentum_safety = premium_momentum and all(available(field) for field in safety_fields)
    degraded = [field for field in ENHANCED_FIELDS if not available(field)]
    blacklist = []
    if not available("强赎状态"):
        blacklist.append("强赎风险")
    if not available("信用评级"):
        blacklist.append("低评级或评级异常")
    return [
        f"- 能否支持纯双低策略：{'可以，但需先过滤已上市且现价、溢价率非空的标的' if pure_double_low else '不可以'}",
        f"- 能否支持低溢价 + 动量策略：{'样本验证可行，仍需全市场覆盖检查' if premium_momentum else '暂不可以'}",
        f"- 能否支持低溢价 + 动量 + 安全性策略：{'样本验证可行，仍需全市场覆盖检查' if premium_momentum_safety else '暂不可以'}",
        f"- 需要降级的字段：{', '.join(degraded) if degraded else '无'}",
        f"- 建议人工维护黑名单：{', '.join(blacklist) if blacklist else '仍建议保留停牌、退市风险、异常交易人工黑名单'}",
    ]


def render_report(
    source_results: Sequence[SourceResult],
    field_support: dict[str, dict[str, object]],
    stock_probe: StockMomentumProbe,
    akshare_version: str,
    secrets: Iterable[str] = (),
) -> str:
    now = datetime.now().astimezone()
    lines = [
        "# 可转债数据源体检报告",
        "",
        "## 运行环境",
        "",
        f"- 本次运行时间：{now.isoformat(timespec='seconds')}",
        f"- Python 版本：{platform.python_version()}",
        f"- AKShare 版本：{akshare_version}",
        "",
        "## 数据源状态",
        "",
        "| 数据源 | 分类 | 状态 | 失败或跳过原因 |",
        "| --- | --- | --- | --- |",
    ]
    for result in source_results:
        reason = result.reason.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {result.name} | {result.category} | {result.status} | {reason} |")

    succeeded = [result.name for result in source_results if result.status == "成功"]
    failed = [f"{result.name}: {result.reason}" for result in source_results if result.status == "失败"]
    lines.extend(["", f"- 成功的数据源：{', '.join(succeeded) if succeeded else '无'}"])
    lines.append(f"- 失败的数据源：{'; '.join(failed) if failed else '无'}")

    for result in source_results:
        lines.extend(["", f"## {result.name}", "", f"- 分类：{result.category}", f"- 状态：{result.status}"])
        if result.reason:
            lines.append(f"- 原因：{result.reason}")
        if result.status != "成功":
            continue
        lines.extend(["", "### 字段清单", "", ", ".join(map(str, result.frame.columns)) or "_无字段_"])
        rates = pd.DataFrame(
            [{"字段": column, "缺失率": f"{rate:.2f}%"} for column, rate in missing_rates(result.frame).items()]
        )
        lines.extend(["", "### 字段缺失率", "", _markdown_table(rates), "", "### 前 10 行样本", ""])
        lines.append(_markdown_table(result.frame.head(10)))

    lines.extend(
        [
            "",
            "## 正股 20 日动量样本探测",
            "",
            "- 正股动量仅为样本探测，不代表全市场 100% 可用。",
            f"- 行情接口：{stock_probe.endpoint}",
            f"- 配置样本数量：{stock_probe.sample_size}",
            f"- 实际请求数量：{len(stock_probe.requested_codes)}",
            f"- 成功计算动量数量：{len(stock_probe.succeeded)}",
            "",
            "### 成功样本",
            "",
            _markdown_table(
                pd.DataFrame(
                    [{"股票代码": code, "20 日动量": f"{momentum:.4%}"} for code, momentum in stock_probe.succeeded.items()]
                )
            ),
            "",
            "### 失败股票及原因",
            "",
            _markdown_table(pd.DataFrame([{"股票代码": code, "原因": reason} for code, reason in stock_probe.failed.items()])),
            "",
            "## 当前策略实际可用字段",
            "",
            "- 字段可用仅表示至少存在一个非空值；是否覆盖全部可交易标的，请结合上方缺失率判断。",
            "",
            "| 字段 | 类型 | 当前可用 | 来源或说明 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for field_name in REQUIRED_FIELDS + ENHANCED_FIELDS:
        status = field_support.get(field_name, {})
        field_type = "必需字段" if field_name in REQUIRED_FIELDS else "增强字段"
        lines.append(
            f"| {field_name} | {field_type} | {'是' if status.get('available') else '否'} | {status.get('source', '')} |"
        )
    lines.extend(["", "## 策略可行性结论", "", *_feasibility(field_support), ""])
    return _redact("\n".join(lines), secrets)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe convertible-bond data-source availability.")
    parser.add_argument("--stock-sample-size", type=int, default=5, help="number of underlying stocks to probe")
    args = parser.parse_args(argv)
    if args.stock_sample_size < 0:
        parser.error("--stock-sample-size must be >= 0")
    return args


def run_probe(stock_sample_size: int) -> int:
    setup_logging()
    load_environment()
    cookie = os.getenv("JSL_COOKIE")
    token = os.getenv("TUSHARE_TOKEN")
    logging.info("凭据状态: JSL_COOKIE=%s, TUSHARE_TOKEN=%s", "已配置" if cookie else "未配置", "已配置" if token else "未配置")

    results: list[SourceResult] = []
    stock_probe = StockMomentumProbe(sample_size=stock_sample_size)
    frames: dict[str, pd.DataFrame] = {}
    akshare_version = "未安装"
    core_ok = False
    try:
        ak = importlib.import_module("akshare")
        akshare_version = getattr(ak, "__version__", "未知")
        core = probe_akshare_core(ak)
        results.append(core)
        core_ok = core.status == "成功"
        if core_ok:
            frames[core.name] = core.frame
            stock_probe = probe_stock_momentum(ak, core.frame, stock_sample_size)
            results.append(stock_probe_source_result(stock_probe))
        jsl = probe_jsl(ak, cookie)
        results.append(jsl)
        if jsl.status == "成功":
            frames[jsl.name] = jsl.frame
    except Exception as exc:
        logging.warning("AKShare 核心依赖或探针初始化失败: %s", exc)
        results.append(SourceResult("AKShare bond_zh_cov", "核心数据源", "失败", reason=str(exc)))

    if not any(result.name == "东方财富正股历史行情" for result in results):
        results.append(stock_probe_source_result(stock_probe))

    tushare_results = probe_tushare(token)
    results.extend(tushare_results)
    for result in tushare_results:
        if result.status == "成功":
            frames[result.name] = result.frame

    support = detect_supported_fields(frames, momentum_available=bool(stock_probe.succeeded))
    report = render_report(results, support, stock_probe, akshare_version, secrets=[cookie or "", token or ""])
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logging.info("已写出数据源体检报告: %s", REPORT_PATH)
    return 0 if core_ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return run_probe(args.stock_sample_size)


if __name__ == "__main__":
    sys.exit(main())
