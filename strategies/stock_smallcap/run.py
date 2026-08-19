"""Small-cap A-share rotation (Stock-SmallCap v1).

Replicates a 果仁网 small-cap screen for live selection (not backtest):
- Universe: 主板 + 中小板 only (exclude 创业板/科创板/北交所/B股), exclude ST and 退市.
- Filters: 当日成交额 > 0.1亿, 非涨停, 非跌停, EP>0 (PE>0), 扣非ROE>-1%.
- Rank: 总市值 ascending (pure small-cap).
- Hold ~10; a holding is sold when its rank falls to >= sell_rank (hysteresis); refill from the
  smallest-cap names that pass every filter.

Data sources (eastmoney-free): universe list from sina spot, snapshot fields (price/昨收/成交额/PE/
总市值/涨停价/跌停价) from Tencent qt.gtimg.cn, 扣非ROE from THS quarterly data (TTM).
"""
from __future__ import annotations

import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, time
from pathlib import Path
from time import perf_counter

try:
    import pandas as pd
except ImportError as exc:
    raise SystemExit("缺少基础依赖。请先运行: python3 -m pip install -r requirements.txt") from exc

from datasource.fundamental_store import (
    DEFAULT_FUNDAMENTAL_STORE,
    FundamentalRequirements,
    expected_report_period,
    prepare_fundamentals,
)
from datasource.derived_store import prepare_strategy_ranking
from datasource.market import (
    fetch_sina_snapshot,
    fetch_tencent_snapshot,
)
from datasource.market_data_bundle import (
    DataRequirements,
    preparation_mode_label,
    prepare_market_data_bundle,
    stable_fingerprint,
)
from datasource.trade_calendar import enforce_snapshot_run_window
from strategies.cb_rotation.run import snapshot_raw_data

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "stock_smallcap.json"
DEFAULT_POSITIONS = ROOT / "portfolios" / "current_stock_positions.csv"
OUTPUT_DIR = ROOT / "outputs"
LOG_DIR = ROOT / "logs"


@dataclass(frozen=True)
class RunArtifacts:
    candidates_csv: Path
    rebalance_csv: Path
    report_md: Path
    run_id: int | None = None
    data_date: date | None = None
    preparation: dict | None = None


def setup_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"stock_smallcap_{datetime.now():%Y%m%d_%H%M%S}.log"
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
        raise SystemExit("缺少依赖 akshare。请先运行: python3 -m pip install -r requirements.txt") from exc
    return ak


def _parse_wan_yi(val) -> float | None:
    """Parse THS financial value strings like '234.23万', '-87.91万', '2.34亿', or raw float."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        import math
        return float(val) if not math.isnan(float(val)) else None
    s = str(val).strip()
    if s in ("", "nan", "None", "False", "--", "-", "NaN"):
        return None
    multiplier = 1.0
    if s.endswith("亿"):
        multiplier = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        multiplier = 1e4
        s = s[:-1]
    try:
        return float(s) * multiplier
    except ValueError:
        return None


def fetch_universe(ak) -> pd.DataFrame:
    """All A-share codes + names from the sina spot list (eastmoney-free).
    Falls back to the latest cached universe snapshot when Sina is unavailable (pre-market).
    """
    import glob as _glob

    def _load_cached_universe() -> pd.DataFrame | None:
        cached = sorted(_glob.glob("data/raw/*/stock_smallcap/universe_snapshot.csv"))
        if not cached:
            return None
        snap = pd.read_csv(cached[-1], dtype={"stock_code": str})
        snap["stock_code"] = snap["stock_code"].astype(str).str.zfill(6)
        return snap[["stock_code", "stock_name"]].drop_duplicates("stock_code").reset_index(drop=True)

    try:
        raw = ak.stock_zh_a_spot()
        code = raw["代码"].astype(str).str.extract(r"(\d{6})", expand=False)
        df = pd.DataFrame({"stock_code": code, "stock_name": raw["名称"].astype(str)})
        df = df.dropna(subset=["stock_code"]).drop_duplicates("stock_code").reset_index(drop=True)
        # Sina returns partial data during pre-auction (09:15-09:25); treat <1000 rows as unavailable.
        if len(df) < 1000:
            cached_df = _load_cached_universe()
            if cached_df is not None:
                logging.info("Sina returned only %s stocks (pre-market partial), falling back to cached universe.", len(df))
                return cached_df
        return df
    except Exception as exc:
        cached_df = _load_cached_universe()
        if cached_df is not None:
            logging.info("Sina API unavailable (%s), falling back to cached universe.", exc)
            return cached_df
        raise


def filter_board_and_st(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    prefixes = tuple(config["universe"]["board_prefixes"])
    mask = df["stock_code"].str.startswith(prefixes)
    if config["filters"].get("exclude_st", True):
        mask &= ~df["stock_name"].str.upper().str.contains("ST", na=False)
    mask &= ~df["stock_name"].str.contains("退", na=False)  # 退市整理
    filtered = df[mask].copy()
    logging.info("Board/ST filter: %s -> %s", len(df), len(filtered))
    return filtered


def apply_filters(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    fl = config["filters"]
    mask = pd.Series(True, index=df.index)
    mask &= df["price"] > 0  # 停牌价为0
    mask &= df["volume_hand"] > 0  # 停牌无成交
    mask &= df["amount_yuan"] > fl["min_amount_yuan"]
    if fl.get("exclude_limit_up_down", True):
        mask &= df["price"] < df["limit_up"] - 1e-6  # 非涨停
        mask &= df["price"] > df["limit_down"] + 1e-6  # 非跌停
    if fl.get("require_positive_pe", True):
        mask &= df["pe_ttm"] > 0  # EP>0 <=> PE>0
    filtered = df[mask].copy()
    logging.info("Snapshot filters: %s -> %s", len(df), len(filtered))
    return filtered


def fetch_snapshot_with_probe(fetcher, codes, probe_size: int) -> pd.DataFrame:
    """Avoid walking a full market when the first quote batch is unusable."""
    started_at = perf_counter()
    source_name = getattr(fetcher, "__name__", type(fetcher).__name__)
    code_list = [str(code).zfill(6) for code in dict.fromkeys(codes) if str(code).strip()]
    if not code_list:
        return pd.DataFrame()
    probe_size = max(1, int(probe_size))
    probe = fetcher(code_list[:probe_size])
    amount = pd.to_numeric(probe.get("amount_yuan"), errors="coerce") if not probe.empty else None
    if probe.empty or amount is None or not amount.fillna(0).gt(0).any():
        logging.info(
            "Snapshot source %s rejected after %.2fs probe (probe=%s, total=%s).",
            source_name,
            perf_counter() - started_at,
            min(probe_size, len(code_list)),
            len(code_list),
        )
        return probe
    remainder_codes = code_list[probe_size:]
    if not remainder_codes:
        return probe
    remainder = fetcher(remainder_codes)
    if remainder.empty:
        return probe
    result = pd.concat([probe, remainder], ignore_index=True).drop_duplicates("stock_code", keep="last")
    logging.info(
        "Snapshot source %s finished in %.2fs (requested=%s, returned=%s).",
        source_name,
        perf_counter() - started_at,
        len(code_list),
        len(result),
    )
    return result


def fetch_deducted_roe_ttm(ak, code: str) -> float | None:
    """TTM 扣非净资产收益率(%) via THS quarterly + annual data.

    TTM 扣非净利润 = sum of last 4 single-quarter 扣非净利润 (ak.stock_financial_abstract_ths 按单季度).
    净资产 estimated from most-recent annual: 净利润 / 净ROE(摊薄).
    Matches 果仁网 扣非净资产收益率 > -1% filter exactly.
    """
    try:
        q_df = ak.stock_financial_abstract_ths(symbol=code, indicator="按单季度")
        a_df = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
    except Exception as exc:
        logging.debug("扣非ROE fetch failed for %s: %s", code, exc)
        return None

    # Last 4 single-quarter 扣非净利润 → TTM
    q_df = q_df.sort_values("报告期", ascending=False).head(4)
    if len(q_df) < 4:
        logging.debug("扣非ROE: only %d quarters for %s, skipping", len(q_df), code)
        return None
    vals = [_parse_wan_yi(v) for v in q_df["扣非净利润"]]
    if any(v is None for v in vals):
        logging.debug("扣非ROE: unparseable 扣非净利润 for %s: %s", code, vals)
        return None
    ttm_deducted = sum(vals)

    # Net assets from most-recent annual: net_assets = 净利润 / 净ROE(摊薄)
    a_df = a_df.sort_values("报告期", ascending=False)
    if a_df.empty:
        return None
    for _, row in a_df.iterrows():
        net_profit = _parse_wan_yi(row.get("净利润"))
        roe_raw = str(row.get("净资产收益率-摊薄", "")).replace("%", "").strip()
        if roe_raw in ("", "nan", "None", "False", "NaN"):
            continue
        try:
            roe_pct = float(roe_raw)
        except ValueError:
            continue
        if net_profit is None or abs(roe_pct) < 0.001:
            continue
        net_assets = net_profit / (roe_pct / 100)
        if net_assets <= 0:
            continue
        return ttm_deducted / net_assets * 100
    return None


def select_smallcap(
    ak,
    df: pd.DataFrame,
    config: dict,
    *,
    effective_date: date | None = None,
    fundamental_store_path: Path | None = None,
) -> pd.DataFrame:
    """Walk smallest-cap first, applying the 扣非ROE filter, until the candidate pool is filled."""
    sel = config["selection"]
    min_roe = config["filters"]["min_roe_pct"]
    ranked = df.sort_values("total_mv_yuan", ascending=True).reset_index(drop=True)
    pool, fetched = [], 0
    max_workers = max(1, min(8, int(sel.get("roe_fetch_workers", 4))))
    candidates = ranked.head(sel["max_roe_fetch"]).to_dict("records")
    fetch_started_at = perf_counter()

    def _fetch_one(row: dict) -> tuple[dict, float | None]:
        return row, fetch_deducted_roe_ttm(ak, row["stock_code"])

    with ThreadPoolExecutor(max_workers=min(max_workers, len(candidates) or 1)) as executor:
        for start in range(0, len(candidates), max_workers):
            batch = candidates[start : start + max_workers]
            if effective_date is None:
                results = list(executor.map(_fetch_one, batch))
            else:
                report_period = expected_report_period(effective_date)
                requirements = FundamentalRequirements(
                    symbol_field="stock_code",
                    metrics=("roe_pct",),
                    caliber="deducted-profit-ttm/annual-net-assets",
                    source="ths-financial-abstract",
                    source_version="v1",
                )

                def _fetch_missing(symbols, period, _metrics) -> pd.DataFrame:
                    values = list(
                        executor.map(
                            lambda code: fetch_deducted_roe_ttm(ak, code),
                            symbols,
                        )
                    )
                    return pd.DataFrame(
                        {
                            "stock_code": list(symbols),
                            "report_period": period,
                            "roe_pct": values,
                        }
                    )

                fundamentals = prepare_fundamentals(
                    requirements,
                    [row["stock_code"] for row in batch],
                    report_period,
                    _fetch_missing,
                    effective_date=effective_date,
                    store_path=fundamental_store_path or DEFAULT_FUNDAMENTAL_STORE,
                )
                logging.info(
                    "基本面准备[%s]: %s",
                    preparation_mode_label(fundamentals.metadata.mode),
                    json.dumps(fundamentals.metadata.to_dict(), ensure_ascii=False),
                )
                values = fundamentals.frame.set_index("stock_code")["roe_pct"].to_dict()
                results = [(row, values.get(row["stock_code"])) for row in batch]
            previous_fetched = fetched
            fetched += len(batch)
            for row, roe in results:
                if len(pool) >= sel["candidate_pool"]:
                    break
                if roe is None or roe <= min_roe:
                    continue
                selected = dict(row)
                selected["roe_pct"] = roe
                pool.append(selected)
            if fetched // 25 > previous_fetched // 25:
                logging.info("扣非ROE-screened %s stocks, pool=%s", fetched, len(pool))
            if len(pool) >= sel["candidate_pool"]:
                break
    result = pd.DataFrame(pool)
    if not result.empty:
        result["rank"] = range(1, len(result) + 1)
    logging.info(
        "Small-cap pool after 扣非ROE filter: %s (fetches=%s, workers=%s, elapsed=%.2fs)",
        len(result),
        fetched,
        max_workers,
        perf_counter() - fetch_started_at,
    )
    return result


def load_current_positions(path: Path) -> pd.DataFrame:
    if not path.exists():
        logging.warning("持仓文件不存在: %s（按空仓处理）", path)
        return pd.DataFrame(columns=["stock_code", "stock_name", "shares"])
    df = pd.read_csv(path, dtype={"stock_code": str})
    if "stock_code" not in df.columns:
        raise ValueError(f"持仓文件缺少 stock_code 列: {path}")
    df["stock_code"] = df["stock_code"].astype(str).str.zfill(6)
    if "stock_name" not in df.columns:
        df["stock_name"] = ""
    return df


def build_target_and_rebalance(current: pd.DataFrame, ranked: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """果仁 II semantics: hold ~hold_n; keep held names still ranked < sell_rank; refill from smallest."""
    sel = config["selection"]
    hold_n, sell_rank = sel["hold_n"], sel["sell_rank"]
    rank_map = dict(zip(ranked["stock_code"], ranked["rank"]))
    name_map = dict(zip(ranked["stock_code"], ranked.get("stock_name_q", pd.Series(dtype=str))))
    current_codes = list(current["stock_code"]) if not current.empty else []
    for _, r in current.iterrows():
        name_map.setdefault(r["stock_code"], r.get("stock_name", ""))

    # 果仁 Model II: sell only when rank >= sell_rank (hysteresis gate).
    # If current holdings > hold_n but all pass the gate, keep them all — don't force-reduce.
    # Only buy new names when holdings < hold_n.
    kept = [c for c in current_codes if rank_map.get(c, 10**9) < sell_rank]
    target = list(kept)
    if len(target) < hold_n:
        for _, r in ranked.iterrows():
            if len(target) >= hold_n:
                break
            if r["stock_code"] not in target:
                target.append(r["stock_code"])

    rows = []
    for c in sorted(set(current_codes) - set(target)):
        rank_val = rank_map.get(c, 10**9)
        reason = f"跌出排名(第{rank_val}名≥{sell_rank})" if rank_val < 10**9 else "已出榜单"
        rows.append({"action": "SELL", "stock_code": c, "stock_name": name_map.get(c, ""), "rank": rank_val if rank_val < 10**9 else None, "note": reason})
    for c in target:
        if c in current_codes:
            rows.append({"action": "HOLD", "stock_code": c, "stock_name": name_map.get(c, ""), "rank": rank_map.get(c), "note": ""})
        else:
            rows.append({"action": "BUY", "stock_code": c, "stock_name": name_map.get(c, ""), "rank": rank_map.get(c), "note": "小市值新进"})
    rebalance = pd.DataFrame(rows, columns=["action", "stock_code", "stock_name", "rank", "note"])
    target_df = ranked[ranked["stock_code"].isin(target)].copy()
    return target_df, rebalance


def build_data_notes(config: dict) -> list[str]:
    return [
        "选股域：主板+中小板，排除创业板/科创板/北交所/B股、ST、退市。",
        "过滤：当日成交额>{:.0f}万、非涨停、非跌停、PE>0(EP>0)、扣非ROE>{}%。".format(
            config["filters"]["min_amount_yuan"] / 10_000, config["filters"]["min_roe_pct"]
        ),
        "排名：总市值从小到大。持仓约 {} 只，跌出第 {} 名卖出。".format(
            config["selection"]["hold_n"], config["selection"]["sell_rank"]
        ),
        "口径提醒：扣非ROE 为 THS 季报 TTM 口径（近4季度扣非净利润/年末净资产），与果仁网一致；总市值/成交额/涨跌停为运行时点数据，建议盘前或收盘后运行。",
    ]


def latest_completed_data_date(now: datetime | None = None) -> date:
    """Return the data date for a previous-close stock screen."""
    current = now or datetime.now()
    candidate = current.date()
    if current.weekday() >= 5 or current.time() < time(9, 25):
        candidate = candidate - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate = candidate - timedelta(days=1)
    return candidate


def persist_rankings(data_date: date, rankings: pd.DataFrame) -> int:
    from datasource.db import create_complete_strategy_run, init_db

    init_db()
    return create_complete_strategy_run("stock", data_date, None, rankings)


def rankings_for_order_sizing(ranked: pd.DataFrame, target_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Return the strategy target set that downstream order sizing should execute."""
    if target_df.empty:
        return target_df.copy()
    result = target_df.copy()
    if "rank" in result.columns:
        result = result.sort_values("rank")
    return result


def save_outputs(ranked: pd.DataFrame, rebalance: pd.DataFrame, config: dict, log_file: Path, notes: list[str], data_date: "date | None" = None) -> RunArtifacts:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidates_csv = OUTPUT_DIR / f"stock_smallcap_pool_{stamp}.csv"
    rebalance_csv = OUTPUT_DIR / f"stock_smallcap_rebalance_{stamp}.csv"
    report_md = OUTPUT_DIR / f"stock_smallcap_report_{stamp}.md"

    cols = ["rank", "stock_code", "stock_name_q", "total_mv_yuan", "amount_yuan", "pe_ttm", "roe_pct", "price"]
    view = ranked[[c for c in cols if c in ranked.columns]].copy()
    if "total_mv_yuan" in view.columns:
        view["总市值(亿)"] = (view["total_mv_yuan"] / 1e8).round(2)
    if "amount_yuan" in view.columns:
        view["成交额(亿)"] = (view["amount_yuan"] / 1e8).round(3)
    view.to_csv(candidates_csv, index=False, encoding="utf-8-sig")
    rebalance.to_csv(rebalance_csv, index=False, encoding="utf-8-sig")

    table = ranked.head(config["selection"]["candidate_pool"]).copy()
    table["总市值(亿)"] = (table["total_mv_yuan"] / 1e8).round(2)
    table["成交额(亿)"] = (table["amount_yuan"] / 1e8).round(3)
    table["扣非ROE(%)"] = table["roe_pct"].round(2)
    report_md.write_text(
        "\n".join(
            [
                f"# {config['strategy_name']} 选股报告",
                "",
                f"- 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"- 候选池: {len(ranked)} 只，目标持仓 {config['selection']['hold_n']} 只",
                f"- 日志文件: `{log_file}`",
                "",
                "## 口径提醒",
                "",
                "\n".join(f"- {n}" for n in notes),
                "",
                "## 小市值候选（按总市值升序）",
                "",
                table[["rank", "stock_code", "stock_name_q", "总市值(亿)", "成交额(亿)", "pe_ttm", "扣非ROE(%)"]].to_markdown(index=False),
                "",
                "## 调仓建议",
                "",
                rebalance.to_markdown(index=False) if not rebalance.empty else "无需调仓。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return RunArtifacts(candidates_csv, rebalance_csv, report_md)


def market_data_requirements(config: dict) -> DataRequirements:
    """Declare remote stock inputs independently from local filter and ranking parameters."""
    return DataRequirements(
        strategy="stock",
        dataset_fields={
            "merged": (
                "stock_code",
                "price",
                "total_mv_yuan",
                "amount_yuan",
                "pe_ttm",
            ),
        },
        symbol_field="stock_code",
        source="sina+tencent",
        source_version="smallcap-market-sources-v1",
        algorithm_version="smallcap-raw-input-v1",
        config_fingerprint=stable_fingerprint({
            "universe": config.get("universe", {}),
            "exclude_st": config.get("filters", {}).get("exclude_st", True),
        }),
    )


def run(
    config_path: Path,
    positions_path: Path,
    max_universe: int | None = None,
    *,
    effective_date: date | None = None,
    started_at: datetime | None = None,
) -> RunArtifacts:
    enforce_snapshot_run_window(now=started_at)
    log_file = setup_logging()
    config = load_config(config_path)

    data_date = effective_date or latest_completed_data_date(now=started_at)
    requirements = market_data_requirements(config)

    def _fetch_raw_inputs() -> dict[str, pd.DataFrame]:
        ak = require_akshare()
        universe = fetch_universe(ak)
        universe = filter_board_and_st(universe, config)
        if max_universe:
            universe = universe.head(max_universe).copy()
            logging.info("Limited universe to first %s for test run.", max_universe)

        snap = fetch_snapshot_with_probe(fetch_tencent_snapshot, universe["stock_code"], probe_size=60)
        # Fallback chain: Tencent → Sina (live) → cached snapshot.
        if snap.empty or (snap["amount_yuan"] == 0).all():
            import glob as _glob

            def _load_cached_snapshot():
                cached_snaps = sorted(_glob.glob("data/raw/*/stock_smallcap/universe_snapshot.csv"))
                if not cached_snaps:
                    return None
                cs = pd.read_csv(cached_snaps[-1], dtype={"stock_code": str})
                cs["stock_code"] = cs["stock_code"].astype(str).str.zfill(6)
                return cs

            sina_snap = fetch_snapshot_with_probe(fetch_sina_snapshot, universe["stock_code"], probe_size=50)
            if not sina_snap.empty and not (sina_snap["amount_yuan"] == 0).all():
                logging.info("Tencent unavailable, using Sina live snapshot")
                snap = sina_snap
                cached_snap = _load_cached_snapshot()
                if cached_snap is not None:
                    for col in ("pe_ttm", "total_mv_yuan", "limit_up", "limit_down"):
                        if col in cached_snap.columns and col in snap.columns:
                            snap[col] = snap["stock_code"].map(
                                cached_snap.set_index("stock_code")[col]
                            ).fillna(snap[col])
            else:
                cached_snap = _load_cached_snapshot()
                if cached_snap is not None:
                    logging.info("Both Tencent and Sina unavailable, using cached snapshot")
                    snap = cached_snap
                else:
                    logging.warning("All data sources unavailable and no cached snapshot.")

        merged = universe.merge(snap, on="stock_code", how="inner")
        logging.info("Snapshot coverage: %s/%s", len(merged), len(universe))
        merged["stock_code"] = merged["stock_code"].astype(str).str.zfill(6)
        for col in (
            "total_mv_yuan",
            "amount_yuan",
            "pe_ttm",
            "price",
            "volume_hand",
            "prev_close",
            "limit_up",
            "limit_down",
        ):
            if col in merged.columns:
                merged[col] = pd.to_numeric(merged[col], errors="coerce")

        return {"merged": merged}

    bundle = prepare_market_data_bundle(
        requirements,
        data_date,
        _fetch_raw_inputs,
    )
    logging.info(
        "数据准备[%s]: %s",
        preparation_mode_label(bundle.metadata.mode),
        json.dumps(bundle.metadata.to_dict(), ensure_ascii=False),
    )
    merged = bundle.frames["merged"].copy()
    merged["stock_code"] = merged["stock_code"].astype(str).str.zfill(6)
    for col in ("total_mv_yuan", "amount_yuan", "pe_ttm", "roe_pct", "price",
                "volume_hand", "prev_close", "limit_up", "limit_down"):
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")
    filtered = apply_filters(merged, config)
    ranking_input_fingerprint = stable_fingerprint({
        "market": bundle.input_fingerprint,
        "fundamental_report_period": expected_report_period(data_date),
        "fundamental_source_version": "v1",
    })

    def _compute_ranking() -> pd.DataFrame:
        ranked_local = select_smallcap(
            require_akshare(),
            filtered,
            config,
            effective_date=data_date,
        )
        if ranked_local.empty:
            raise RuntimeError("没有股票通过全部过滤，无法生成榜单。请检查数据源或放宽配置。")
        return ranked_local

    ranking = prepare_strategy_ranking(
        strategy="stock",
        effective_date=data_date,
        strategy_version="smallcap-v1",
        config_fingerprint=stable_fingerprint(config),
        input_fingerprint=ranking_input_fingerprint,
        compute=_compute_ranking,
    )
    logging.info(
        "榜单准备[%s]: %s",
        preparation_mode_label(ranking.metadata.mode),
        json.dumps(ranking.metadata.to_dict(), ensure_ascii=False),
    )
    ranked = ranking.frame.copy()
    ranked["stock_code"] = ranked["stock_code"].astype(str).str.zfill(6)
    if "rank" not in ranked.columns or ranked["rank"].isna().any():
        ranked["rank"] = range(1, len(ranked) + 1)

    current = load_current_positions(positions_path)
    target_df, rebalance = build_target_and_rebalance(current, ranked, config)
    notes = build_data_notes(config)
    artifacts = save_outputs(ranked, rebalance, config, log_file, notes, data_date=data_date)
    logging.info("Saved report to %s", artifacts.report_md)
    run_id = persist_rankings(data_date, rankings_for_order_sizing(ranked, target_df, config))
    artifacts = replace(
        artifacts,
        run_id=run_id,
        data_date=data_date,
        preparation={
            "market": bundle.metadata.to_dict(),
            "ranking": ranking.metadata.to_dict(),
        },
    )
    logging.info("DB write OK: stock rankings run_id=%s data_date=%s", run_id, data_date)
    try:
        snapshot_raw_data(
            data_date,
            {"universe_snapshot": merged, "merged": merged, "filtered": filtered, "smallcap_pool": ranked, "rebalance_plan": rebalance},
            config,
            subdir="stock_smallcap",
        )
    except Exception as exc:
        logging.warning("Failed to save raw snapshot: %s", exc)
    _cleanup_old_outputs("stock_smallcap_")
    _cleanup_old_raw_snapshots()
    return artifacts


def _cleanup_old_outputs(prefix: str, keep: int = 90) -> None:
    """Keep the `keep` most recent output files matching `prefix`."""
    if not OUTPUT_DIR.exists():
        return
    files = sorted(OUTPUT_DIR.glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        try:
            f.unlink()
        except OSError:
            pass


def _cleanup_old_raw_snapshots(keep_days: int = 90) -> None:
    """Remove data/raw/ directories older than `keep_days` days."""
    raw_dir = ROOT / "data" / "raw"
    if not raw_dir.exists():
        return
    import shutil
    from datetime import date as _date, timedelta as _td
    from datetime import datetime as _dt

    cutoff = _date.today() - _td(days=keep_days)
    for d in sorted(raw_dir.iterdir()):
        if not d.is_dir():
            continue
        try:
            dir_date = _dt.strptime(d.name, "%Y%m%d").date()
        except ValueError:
            continue
        if dir_date < cutoff:
            shutil.rmtree(d)
            logging.info("Cleaned up old raw snapshot: %s", d)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run small-cap A-share rotation screen.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--positions", type=Path, default=DEFAULT_POSITIONS)
    parser.add_argument("--max-universe", type=int, default=None, help="Limit universe for quick smoke tests.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    artifacts = run(args.config, args.positions, args.max_universe)
    print("小市值候选:", artifacts.candidates_csv)
    print("调仓建议:", artifacts.rebalance_csv)
    print("选股报告:", artifacts.report_md)
