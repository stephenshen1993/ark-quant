from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

try:
    import numpy as np
    import pandas as pd
except ImportError as exc:
    raise SystemExit("缺少基础依赖。请先运行: python3 -m pip install -r requirements.txt") from exc


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "cb_rotation.json"
DEFAULT_POSITIONS = ROOT / "portfolios" / "current_cb_positions.csv"
OUTPUT_DIR = ROOT / "outputs"
LOG_DIR = ROOT / "logs"


@dataclass(frozen=True)
class RunArtifacts:
    candidates_csv: Path
    candidates_xlsx: Path
    rebalance_csv: Path
    report_md: Path


def setup_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"cb_rotation_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )
    return log_file


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def require_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "缺少依赖 akshare。请先运行: python3 -m pip install -r requirements.txt"
        ) from exc
    return ak


def first_existing_col(df: pd.DataFrame, names: Iterable[str]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for name in names:
        key = name.strip().lower()
        if key in lowered:
            return lowered[key]
    return None


def as_number(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    multiplier = pd.Series(1.0, index=series.index)
    multiplier = multiplier.mask(text.str.contains("万", na=False), 10_000.0)
    multiplier = multiplier.mask(text.str.contains("亿", na=False), 100_000_000.0)
    cleaned = (
        text.str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("亿元", "", regex=False)
        .str.replace("亿", "", regex=False)
        .str.replace("万元", "", regex=False)
        .str.replace("万", "", regex=False)
        .str.replace("--", "", regex=False)
        .str.replace("-", "", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce") * multiplier


def as_100m_units(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    cleaned = (
        text.str.replace(",", "", regex=False)
        .str.replace("亿元", "", regex=False)
        .str.replace("亿", "", regex=False)
        .str.replace("万元", "", regex=False)
        .str.replace("万", "", regex=False)
        .str.replace("--", "", regex=False)
        .str.replace("-", "", regex=False)
    )
    value = pd.to_numeric(cleaned, errors="coerce")
    value = value.mask(text.str.contains("万", na=False), value / 10_000)
    return value


def as_yuan(series: pd.Series, column_name: str | None = None) -> pd.Series:
    value = as_number(series)
    name = column_name or ""
    if "万元" in name:
        return pd.to_numeric(series, errors="coerce") * 10_000
    if "亿元" in name:
        return pd.to_numeric(series, errors="coerce") * 100_000_000
    return value


def as_turnover_yuan(series: pd.Series, column_name: str | None = None) -> pd.Series:
    value = as_yuan(series, column_name)
    name = column_name or ""
    if name == "成交额" and value.max(skipna=True) < 10_000_000:
        return value * 10_000
    return value


def get_col(df: pd.DataFrame, candidates: list[str], target: str, required: bool = False) -> pd.Series:
    col = first_existing_col(df, candidates)
    if col is None:
        message = f"缺少字段 {target}，候选列: {candidates}"
        if required:
            raise ValueError(message)
        logging.warning("%s；该字段相关过滤会跳过。", message)
        return pd.Series(pd.NA, index=df.index, name=target)
    return df[col]


def fetch_cb_universe(ak) -> pd.DataFrame:
    errors: list[str] = []
    for func_name in ("bond_zh_cov", "bond_cb_jsl"):
        if not hasattr(ak, func_name):
            continue
        try:
            logging.info("Fetching convertible bond universe with ak.%s()", func_name)
            raw = getattr(ak, func_name)()
            if isinstance(raw, pd.DataFrame) and not raw.empty:
                raw.attrs["source_func"] = func_name
                return raw
        except Exception as exc:  # AKShare endpoints can change or throttle.
            errors.append(f"{func_name}: {exc}")
            logging.warning("Failed to fetch with %s: %s", func_name, exc)
    raise RuntimeError("无法获取可转债全市场数据: " + " | ".join(errors))


def enrich_cb_with_redeem_data(ak, cb: pd.DataFrame) -> pd.DataFrame:
    try:
        logging.info("Fetching redeem risk data with ak.bond_cb_redeem_jsl()")
        raw = ak.bond_cb_redeem_jsl()
    except Exception as exc:
        logging.warning("Failed to fetch redeem data: %s", exc)
        return cb
    if raw.empty:
        return cb

    redeem = pd.DataFrame(index=raw.index)
    redeem["bond_code"] = get_col(raw, ["代码", "债券代码"], "bond_code", True).astype(str).str.zfill(6)
    redeem["remaining_size_100m_redeem"] = as_100m_units(get_col(raw, ["剩余规模"], "remaining_size_100m"))
    redeem["maturity_date_redeem"] = pd.to_datetime(
        get_col(raw, ["到期日", "到期时间", "到期日期"], "maturity_date"),
        errors="coerce",
    ).dt.date
    redeem["call_status_redeem"] = get_col(raw, ["强赎状态"], "call_status").astype(str)
    redeem = redeem.drop_duplicates(subset=["bond_code"])

    merged = cb.merge(redeem, on="bond_code", how="left")
    merged["remaining_size_100m"] = merged["remaining_size_100m"].combine_first(
        merged["remaining_size_100m_redeem"]
    )
    merged["maturity_date"] = merged["maturity_date"].combine_first(merged["maturity_date_redeem"])
    merged["call_status"] = merged["call_status"].replace({"<NA>": pd.NA, "nan": pd.NA}).combine_first(
        merged["call_status_redeem"]
    )
    return merged.drop(columns=["remaining_size_100m_redeem", "maturity_date_redeem", "call_status_redeem"])


def normalize_cb_data(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(index=raw.index)
    df["bond_code"] = get_col(raw, ["债券代码", "转债代码", "代码", "bond_id"], "bond_code", True).astype(str).str.zfill(6)
    df["bond_name"] = get_col(raw, ["债券简称", "转债名称", "名称", "bond_nm"], "bond_name", True).astype(str)
    df["stock_code"] = get_col(raw, ["正股代码", "stock_id", "stock_code"], "stock_code").astype(str).str.extract(r"(\d{6})", expand=False)
    df["stock_name"] = get_col(raw, ["正股简称", "正股名称", "stock_nm", "stock_name"], "stock_name").astype(str)
    df["cb_price"] = as_number(get_col(raw, ["债现价", "现价", "最新价", "转债最新价", "price"], "cb_price", True))
    df["premium_rate"] = as_number(get_col(raw, ["转股溢价率", "溢价率", "premium_rt"], "premium_rate", True))
    remaining_col = first_existing_col(raw, ["剩余规模", "债券余额", "余额", "发行规模", "remain_size"])
    if remaining_col is None:
        logging.warning("缺少字段 remaining_size_100m；该字段相关过滤会跳过。")
        df["remaining_size_100m"] = pd.NA
    else:
        df["remaining_size_100m"] = as_100m_units(raw[remaining_col])
    turnover_col = first_existing_col(raw, ["成交额", "成交额(元)", "成交额(万元)", "amount"])
    if turnover_col is None:
        logging.warning("缺少字段 turnover_yuan；该字段相关过滤会跳过。")
        df["turnover_yuan"] = pd.NA
    else:
        df["turnover_yuan"] = as_turnover_yuan(raw[turnover_col], str(turnover_col))
    df["maturity_date"] = pd.to_datetime(
        get_col(raw, ["到期时间", "到期日期", "maturity_dt"], "maturity_date"),
        errors="coerce",
    ).dt.date
    df["remaining_years"] = as_number(get_col(raw, ["剩余年限"], "remaining_years"))
    df["call_status"] = get_col(raw, ["强赎状态", "强赎", "redeem_flag", "redeem_status"], "call_status").astype(str)
    return df.drop_duplicates(subset=["bond_code"])


def normalize_stock_code(code: str) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def stock_symbol_with_exchange(code: str) -> str:
    code = normalize_stock_code(code)
    prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}{code}"


def fetch_stock_factors(ak, stock_codes: Iterable[str], end: date) -> pd.DataFrame:
    start = end - timedelta(days=90)
    rows: list[dict] = []
    for i, code in enumerate(sorted({normalize_stock_code(c) for c in stock_codes if pd.notna(c)}), start=1):
        if not code:
            continue
        try:
            try:
                hist = ak.stock_zh_a_daily(
                    symbol=stock_symbol_with_exchange(code),
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq",
                )
            except Exception:
                hist = ak.stock_zh_a_hist_tx(
                    symbol=stock_symbol_with_exchange(code),
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq",
                )
            close_col = first_existing_col(hist, ["收盘", "close"])
            if close_col is None or len(hist) < 21:
                logging.warning("Stock %s history is insufficient; skipping factors.", code)
                continue
            close = pd.to_numeric(hist[close_col], errors="coerce").dropna()
            if len(close) < 21:
                continue
            ret = close.pct_change().dropna()
            share_col = first_existing_col(hist, ["outstanding_share"])
            market_cap = np.nan
            if share_col is not None:
                shares = pd.to_numeric(hist[share_col], errors="coerce").dropna()
                if not shares.empty:
                    market_cap = close.iloc[-1] * shares.iloc[-1]
            rows.append(
                {
                    "stock_code": code,
                    "stock_momentum_20d": close.iloc[-1] / close.iloc[-21] - 1,
                    "stock_volatility_20d": ret.tail(20).std() * np.sqrt(252),
                    "market_cap": market_cap,
                }
            )
            if i % 50 == 0:
                logging.info("Fetched stock history for %s symbols.", i)
        except Exception as exc:
            logging.warning("Failed to fetch stock history for %s: %s", code, exc)
    return pd.DataFrame(rows, columns=["stock_code", "stock_momentum_20d", "stock_volatility_20d", "market_cap"])


def apply_cb_prefilters(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    filters = config["filters"]
    mask = pd.Series(True, index=df.index)
    mask &= df["cb_price"] < filters["max_cb_price"]
    if df["remaining_size_100m"].notna().any():
        mask &= df["remaining_size_100m"] > filters["min_remaining_size_100m"]
    if df["turnover_yuan"].notna().any():
        mask &= df["turnover_yuan"] > filters["min_turnover_yuan"]
    if df["remaining_years"].notna().any():
        mask &= df["remaining_years"] > filters["min_years_to_maturity"]
    elif df["maturity_date"].notna().any():
        min_maturity = pd.Timestamp(date.today() + timedelta(days=365 * filters["min_years_to_maturity"]))
        mask &= df["maturity_date"] > min_maturity
    if filters.get("exclude_call_risk", True) and df["call_status"].notna().any():
        risk_words = ("已满足", "满足强赎", "强赎公告", "公告强赎", "即将强赎", "强赎中", "赎回登记", "最后交易", "停止交易")
        mask &= ~df["call_status"].fillna("").str.contains("|".join(risk_words), regex=True)
    if filters.get("exclude_st_stock", True):
        mask &= ~df["stock_name"].fillna("").str.upper().str.contains("ST", regex=False)
    filtered = df.loc[mask].copy()
    logging.info("CB prefiltered universe: %s -> %s", len(df), len(filtered))
    return filtered


def apply_filters(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    filters = config["filters"]
    mask = pd.Series(True, index=df.index)
    mask &= df["cb_price"] < filters["max_cb_price"]

    if df["remaining_size_100m"].notna().any():
        mask &= df["remaining_size_100m"] > filters["min_remaining_size_100m"]
    if df["turnover_yuan"].notna().any():
        mask &= df["turnover_yuan"] > filters["min_turnover_yuan"]
    if df["remaining_years"].notna().any():
        mask &= df["remaining_years"] > filters["min_years_to_maturity"]
    elif df["maturity_date"].notna().any():
        min_maturity = pd.Timestamp(date.today() + timedelta(days=365 * filters["min_years_to_maturity"]))
        mask &= df["maturity_date"] > min_maturity
    if filters.get("exclude_call_risk", True) and df["call_status"].notna().any():
        risk_words = ("已满足", "满足强赎", "强赎公告", "公告强赎", "即将强赎", "强赎中", "赎回登记", "最后交易", "停止交易")
        mask &= ~df["call_status"].fillna("").str.contains("|".join(risk_words), regex=True)
    if filters.get("exclude_st_stock", True):
        stock_name_spot = df["stock_name_spot"] if "stock_name_spot" in df.columns else pd.Series(pd.NA, index=df.index)
        name = stock_name_spot.fillna(df["stock_name"]).fillna("")
        mask &= ~name.str.upper().str.contains("ST", regex=False)

    filtered = df.loc[mask].copy()
    logging.info("Filtered universe: %s -> %s", len(df), len(filtered))
    return filtered


def rank_score(series: pd.Series, higher_is_better: bool) -> pd.Series:
    valid = series.dropna()
    if valid.empty:
        return pd.Series(0.0, index=series.index)
    ranks = series.rank(ascending=not higher_is_better, method="average", na_option="bottom")
    if len(series) == 1:
        return pd.Series(1.0, index=series.index)
    return 1 - (ranks - 1) / (len(series) - 1)


def score_candidates(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    weights = config["weights"]
    scored = df.copy()
    scored["double_low"] = scored["cb_price"] + scored["premium_rate"]
    scored["score_double_low"] = rank_score(scored["double_low"], higher_is_better=False)
    scored["score_momentum"] = rank_score(scored["stock_momentum_20d"], higher_is_better=True)
    scored["score_low_volatility"] = rank_score(scored["stock_volatility_20d"], higher_is_better=False)
    scored["score_small_market_cap"] = rank_score(scored["market_cap"], higher_is_better=False)
    scored["score"] = (
        weights["double_low"] * scored["score_double_low"]
        + weights["stock_momentum_20d"] * scored["score_momentum"]
        + weights["stock_low_volatility_20d"] * scored["score_low_volatility"]
        + weights["stock_small_market_cap"] * scored["score_small_market_cap"]
    )
    return scored.sort_values(["score", "double_low"], ascending=[False, True])


def assign_equal_weight(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    top_n = min(config["top_n"], len(df))
    result = df.head(top_n).copy()
    if top_n == 0:
        result["target_weight"] = pd.Series(dtype=float)
        return result
    equal_weight = min(1 / top_n, config["position"]["max_single_weight"])
    result["target_weight"] = equal_weight
    return result


def load_current_positions(path: Path) -> pd.DataFrame:
    if not path.exists():
        logging.warning("Current positions file does not exist: %s", path)
        return pd.DataFrame(columns=["bond_code", "bond_name", "target_weight"])
    df = pd.read_csv(path, dtype={"bond_code": str})
    if "bond_code" not in df.columns:
        raise ValueError(f"当前持仓文件缺少 bond_code 列: {path}")
    df["bond_code"] = df["bond_code"].astype(str).str.zfill(6)
    return df


def build_rebalance_plan(current: pd.DataFrame, target: pd.DataFrame) -> pd.DataFrame:
    current_codes = set(current["bond_code"].dropna().astype(str))
    target_codes = set(target["bond_code"].dropna().astype(str))
    rows: list[dict] = []
    target_lookup = target.set_index("bond_code").to_dict("index")
    current_lookup = current.set_index("bond_code").to_dict("index") if not current.empty else {}

    for code in sorted(current_codes - target_codes):
        item = current_lookup.get(code, {})
        rows.append({"action": "SELL", "bond_code": code, "bond_name": item.get("bond_name", ""), "target_weight": 0.0})
    for code in sorted(target_codes - current_codes):
        item = target_lookup.get(code, {})
        rows.append({"action": "BUY", "bond_code": code, "bond_name": item.get("bond_name", ""), "target_weight": item.get("target_weight")})
    for code in sorted(current_codes & target_codes):
        item = target_lookup.get(code, {})
        rows.append({"action": "HOLD", "bond_code": code, "bond_name": item.get("bond_name", ""), "target_weight": item.get("target_weight")})
    return pd.DataFrame(rows, columns=["action", "bond_code", "bond_name", "target_weight"])


def save_outputs(target: pd.DataFrame, rebalance: pd.DataFrame, config: dict, log_file: Path) -> RunArtifacts:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidates_csv = OUTPUT_DIR / f"cb_rotation_top{config['top_n']}_{stamp}.csv"
    candidates_xlsx = OUTPUT_DIR / f"cb_rotation_top{config['top_n']}_{stamp}.xlsx"
    rebalance_csv = OUTPUT_DIR / f"cb_rotation_rebalance_{stamp}.csv"
    report_md = OUTPUT_DIR / f"cb_rotation_report_{stamp}.md"

    columns = [
        "bond_code",
        "bond_name",
        "stock_code",
        "stock_name",
        "cb_price",
        "premium_rate",
        "double_low",
        "stock_momentum_20d",
        "stock_volatility_20d",
        "market_cap",
        "remaining_size_100m",
        "turnover_yuan",
        "maturity_date",
        "score",
        "target_weight",
    ]
    available = [c for c in columns if c in target.columns]
    target[available].to_csv(candidates_csv, index=False, encoding="utf-8-sig")
    target[available].to_excel(candidates_xlsx, index=False)
    rebalance.to_csv(rebalance_csv, index=False, encoding="utf-8-sig")

    report_md.write_text(
        "\n".join(
            [
                f"# {config['strategy_name']} 运行报告",
                "",
                f"- 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"- 候选数量: {len(target)}",
                f"- 单债目标权重: {target['target_weight'].iloc[0]:.2%}" if len(target) else "- 单债目标权重: N/A",
                f"- 日志文件: `{log_file}`",
                "",
                "## Top 候选",
                "",
                target[["bond_code", "bond_name", "cb_price", "premium_rate", "double_low", "score", "target_weight"]]
                .to_markdown(index=False),
                "",
                "## 调仓建议",
                "",
                rebalance.to_markdown(index=False) if not rebalance.empty else "无需调仓。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return RunArtifacts(candidates_csv, candidates_xlsx, rebalance_csv, report_md)


def run(config_path: Path, positions_path: Path, max_bonds: int | None = None) -> RunArtifacts:
    log_file = setup_logging()
    config = load_config(config_path)
    ak = require_akshare()

    raw_cb = fetch_cb_universe(ak)
    cb = normalize_cb_data(raw_cb)
    if max_bonds:
        cb = cb.head(max_bonds).copy()
        logging.info("Limited universe to first %s bonds for test run.", max_bonds)

    cb = enrich_cb_with_redeem_data(ak, cb)
    cb = apply_cb_prefilters(cb, config)
    stock_factors = fetch_stock_factors(ak, cb["stock_code"].dropna(), end=date.today())
    merged = cb.merge(stock_factors, on="stock_code", how="left")

    filtered = apply_filters(merged, config)
    scored = score_candidates(filtered, config)
    target = assign_equal_weight(scored, config)
    current = load_current_positions(positions_path)
    rebalance = build_rebalance_plan(current, target)
    artifacts = save_outputs(target, rebalance, config, log_file)
    logging.info("Saved report to %s", artifacts.report_md)
    return artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run convertible bond multi-factor rotation strategy.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config JSON.")
    parser.add_argument("--positions", type=Path, default=DEFAULT_POSITIONS, help="Current positions CSV.")
    parser.add_argument("--max-bonds", type=int, default=None, help="Limit bonds for quick smoke tests.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    artifacts = run(args.config, args.positions, args.max_bonds)
    print("候选持仓:", artifacts.candidates_csv)
    print("调仓建议:", artifacts.rebalance_csv)
    print("运行报告:", artifacts.report_md)
