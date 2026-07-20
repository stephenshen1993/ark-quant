from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from engine import (
    EngineConfig, VARIANTS, build_rankings, event_returns, jsl_replica, load_inputs,
    opportunity_set_events, simulate_ranked_portfolio,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = ROOT / "data/processed/cb_research_20260720"
DEFAULT_OUTPUT = ROOT / "docs/策略/证据/可转债-统一合同重跑"
SUMMARY_FILE = ROOT / "docs/策略/证据/可转债-统一合同重跑摘要.json"
STATS_FILE = ROOT / "docs/策略/证据/可转债-统一合同统计复核.csv"
BENCHMARK_FILE = ROOT / "docs/策略/证据/可转债-统一合同基准状态.csv"
ROLE_FILE = ROOT / "docs/策略/证据/可转债-候选与小盘代理组合职责.csv"


def hac_mean_t(values: np.ndarray, lag: int = 3) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    n = len(values)
    mean = float(values.mean())
    centered = values - mean
    long_var = float(centered @ centered / n)
    for k in range(1, min(lag, n - 1) + 1):
        gamma = float(centered[k:] @ centered[:-k] / n)
        long_var += 2 * (1 - k / (lag + 1)) * gamma
    standard_error = np.sqrt(max(long_var, 0.0) / n)
    return mean, float(mean / standard_error) if standard_error > 0 else np.nan


def moving_block_ci(values: np.ndarray, block: int = 6, samples: int = 2000) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    rng = np.random.default_rng(20260720)
    n = len(values)
    means = []
    for _ in range(samples):
        draw = []
        while len(draw) < n:
            start = int(rng.integers(0, n))
            draw.extend(values[(start + offset) % n] for offset in range(block))
        means.append(float(np.mean(draw[:n])))
    return tuple(float(value) for value in np.quantile(means, [0.025, 0.975]))


def summarize_comparison(left: pd.DataFrame, right: pd.DataFrame, label: str) -> dict:
    merged = left.drop_duplicates("start_date").merge(
        right.drop_duplicates("start_date"), on="start_date", suffixes=("_left", "_right")
    )
    diff = merged["return_left"] - merged["return_right"]
    mean, t_value = hac_mean_t(diff.to_numpy())
    low, high = moving_block_ci(diff.to_numpy())
    return {
        "comparison": label, "events": int(len(diff)), "mean_difference": mean,
        "hac3_t": t_value, "block6_ci_low": low, "block6_ci_high": high,
        "win_rate": float((diff > 0).mean()),
    }


def benchmark_status(input_root: Path) -> pd.DataFrame:
    specs = [
        ("U-EW", "available", "final safe opportunity-set research control; not a formal index"),
        ("JSL-CB-EW", "available" if (input_root / "benchmarks/jsl_cb_equal_weight.csv").exists() else "missing",
         "official full history required; ordinary logged-in account exposed only one year"),
        ("JSL-CB-EW-1Y", "available" if (input_root / "benchmarks/jsl_cb_equal_weight_1y.csv").exists() else "missing",
         "official one-year logged-in extract; cross-check only, never full-history substitution"),
        ("JSL-CB-EW-REPLICA", "available", "public-rule price replica; never impersonates official history"),
        ("HS300-TR", "available" if (input_root / "benchmarks/hs300_total_return.csv").exists() else "missing",
         "official H00300 required; no price-index substitution"),
        ("SMALLCAP-PROXY", "available", "399303 price proxy only; not the formal Guorn small-cap strategy"),
    ]
    return pd.DataFrame(specs, columns=["benchmark", "status", "contract"])


def series_metrics(daily: pd.DataFrame, benchmark: pd.DataFrame) -> dict:
    candidate = daily[["date", "daily_return"]].copy()
    bench = benchmark[["date", "index"]].copy().sort_values("date")
    bench["benchmark_return"] = bench["index"].pct_change()
    common = candidate.merge(bench, on="date").dropna()
    years = (common["date"].iloc[-1] - common["date"].iloc[0]).days / 365.25
    candidate_growth = float(np.prod(1 + common["daily_return"]))
    benchmark_growth = float(np.prod(1 + common["benchmark_return"]))
    variance = float(common["benchmark_return"].var(ddof=1))
    covariance = float(common[["daily_return", "benchmark_return"]].cov().iloc[0, 1])
    return {
        "observations": int(len(common)),
        "start": str(common["date"].iloc[0].date()),
        "end": str(common["date"].iloc[-1].date()),
        "candidate_cagr": candidate_growth ** (1 / years) - 1,
        "benchmark_cagr": benchmark_growth ** (1 / years) - 1,
        "correlation": float(common["daily_return"].corr(common["benchmark_return"])),
        "beta": covariance / variance if variance > 0 else np.nan,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def event_nav_summary(frame: pd.DataFrame) -> dict:
    valid = frame.dropna(subset=["return"]).copy()
    years = (valid["end_date"].iloc[-1] - valid["start_date"].iloc[0]).days / 365.25
    nav = (1 + valid["return"]).cumprod()
    return {
        "events": int(len(valid)), "start": str(valid["start_date"].iloc[0].date()),
        "end": str(valid["end_date"].iloc[-1].date()),
        "cagr_event_boundary": float(nav.iloc[-1] ** (1 / years) - 1),
        "max_drawdown_event_boundary": float((nav / nav.cummax() - 1).min()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    input_root, output = args.input.resolve(), args.output.resolve()
    if not (input_root / "manifest.json").exists():
        raise FileNotFoundError("run prepare_frozen_inputs.py first")
    output.mkdir(parents=True, exist_ok=True)
    panel, risks, exits, coupons = load_inputs(input_root)

    summaries, total_events, price_events, price_daily = {}, {}, {}, {}
    rankings_by_variant = {}
    b0_universes = None
    for variant in VARIANTS:
        rankings, universes = build_rankings(panel, variant, minimum=20)
        if variant == "B0_BASE":
            b0_universes = universes
        rankings_by_variant[variant] = rankings
        for label, include_coupons in (("pretax_total", True), ("price_ex_coupon", False)):
            daily, trades, summary = simulate_ranked_portfolio(
                panel, rankings, risks, exits, coupons,
                EngineConfig(target=20, fee=0.001, include_coupons=include_coupons),
            )
            daily.to_csv(output / f"{variant}-{label}-daily.csv", index=False)
            trades.to_csv(output / f"{variant}-{label}-trades.csv", index=False)
            events = event_returns(daily, list(rankings))
            events.to_csv(output / f"{variant}-{label}-events.csv", index=False)
            summaries[f"{variant}:{label}"] = summary
            if include_coupons:
                total_events[variant] = events
            else:
                price_events[variant] = events
                price_daily[variant] = daily

        ranking_rows = []
        for execution, payload in rankings.items():
            ranking_rows.append({"execution_date": execution, "signal_date": payload["signal_date"],
                                 "ranked_codes": "|".join(payload["codes"])})
        pd.DataFrame(ranking_rows).to_csv(output / f"{variant}-rankings.csv", index=False)

    replica = jsl_replica(panel)
    replica.to_csv(output / "JSL-CB-EW-REPLICA.csv", index=False)

    uew_total = opportunity_set_events(panel, b0_universes, risks, exits, coupons, True)
    uew_price = opportunity_set_events(panel, b0_universes, risks, exits, coupons, False)
    uew_total.to_csv(output / "U-EW-pretax_total-events.csv", index=False)
    uew_price.to_csv(output / "U-EW-price_ex_coupon-events.csv", index=False)

    comparisons = [
        summarize_comparison(total_events["C_BAL"], total_events["B0_BASE"], "C_BAL minus B0_BASE, pretax_total"),
        summarize_comparison(total_events["C_OFF"], total_events["C_BAL"], "C_OFF minus C_BAL, pretax_total"),
        summarize_comparison(price_events["C_BAL"], price_events["B0_BASE"], "C_BAL minus B0_BASE, price_ex_coupon"),
        summarize_comparison(price_events["C_OFF"], price_events["C_BAL"], "C_OFF minus C_BAL, price_ex_coupon"),
        summarize_comparison(total_events["C_BAL"], uew_total, "C_BAL minus U-EW, pretax_total"),
        summarize_comparison(total_events["C_OFF"], uew_total, "C_OFF minus U-EW, pretax_total"),
    ]
    stats = pd.DataFrame(comparisons)
    stats.to_csv(STATS_FILE, index=False)
    status = benchmark_status(input_root)
    status.to_csv(BENCHMARK_FILE, index=False)

    smallcap = pd.read_csv(input_root / "benchmarks/smallcap_proxy_399303.csv", parse_dates=["date"])
    hs300_path = input_root / "benchmarks/hs300_total_return.csv"
    hs300 = pd.read_csv(hs300_path, parse_dates=["date"]) if hs300_path.exists() else None
    jsl_one_year_path = input_root / "benchmarks/jsl_cb_equal_weight_1y.csv"
    jsl_one_year = pd.read_csv(jsl_one_year_path, parse_dates=["date"]) if jsl_one_year_path.exists() else None
    role_rows = []
    for variant in VARIANTS:
        jsl_metrics = series_metrics(price_daily[variant], replica)
        smallcap_metrics = series_metrics(price_daily[variant], smallcap.rename(columns={"close": "index"}))
        hs300_metrics = series_metrics(price_daily[variant], hs300) if hs300 is not None else None
        jsl_one_year_metrics = series_metrics(price_daily[variant], jsl_one_year) if jsl_one_year is not None else None
        summary = summaries[f"{variant}:price_ex_coupon"]
        role_rows.append({
            "candidate": variant, "return_contract": "price_ex_coupon_net_10bp",
            "start": summary["start"], "end": summary["end"], "cagr": summary["cagr"],
            "max_drawdown": summary["max_drawdown"],
            "jsl_replica_cagr": jsl_metrics["benchmark_cagr"],
            "correlation_jsl_replica": jsl_metrics["correlation"], "beta_jsl_replica": jsl_metrics["beta"],
            "smallcap_proxy_cagr": smallcap_metrics["benchmark_cagr"],
            "correlation_smallcap_proxy": smallcap_metrics["correlation"],
            "beta_smallcap_proxy": smallcap_metrics["beta"],
            "official_jsl_status": status.set_index("benchmark").at["JSL-CB-EW", "status"],
            "official_jsl_1y_status": status.set_index("benchmark").at["JSL-CB-EW-1Y", "status"],
            "official_jsl_1y_observations": jsl_one_year_metrics["observations"] if jsl_one_year_metrics else 0,
            "candidate_cagr_common_jsl_official_1y": jsl_one_year_metrics["candidate_cagr"] if jsl_one_year_metrics else np.nan,
            "official_jsl_1y_cagr": jsl_one_year_metrics["benchmark_cagr"] if jsl_one_year_metrics else np.nan,
            "correlation_jsl_official_1y": jsl_one_year_metrics["correlation"] if jsl_one_year_metrics else np.nan,
            "beta_jsl_official_1y": jsl_one_year_metrics["beta"] if jsl_one_year_metrics else np.nan,
            "hs300_tr_status": status.set_index("benchmark").at["HS300-TR", "status"],
            "hs300_tr_observations": hs300_metrics["observations"] if hs300_metrics else 0,
            "candidate_cagr_common_hs300_tr": hs300_metrics["candidate_cagr"] if hs300_metrics else np.nan,
            "hs300_tr_cagr": hs300_metrics["benchmark_cagr"] if hs300_metrics else np.nan,
            "correlation_hs300_tr": hs300_metrics["correlation"] if hs300_metrics else np.nan,
            "beta_hs300_tr": hs300_metrics["beta"] if hs300_metrics else np.nan,
            "formal_smallcap_status": "missing",
        })
    pd.DataFrame(role_rows).to_csv(ROLE_FILE, index=False)

    result = {
        "package_version": "CB-REPRO-20260720-v1",
        "contract": {
            "signal_execution": "10/20/30 signal close, next trading-day open",
            "portfolio": "top20 equal weight; 10bp each actual buy/sell",
            "missing_open": "target slot remains cash; no redistribution to tradable targets",
            "initial_fee": "included from initial capital 1.0; never normalized away",
            "returns": "price_ex_coupon and modeled pretax coupon total return both reported",
        },
        "summaries": summaries,
        "comparisons": comparisons,
        "benchmark_status": status.to_dict("records"),
        "U-EW": {
            "pretax_total": event_nav_summary(uew_total),
            "price_ex_coupon": event_nav_summary(uew_price),
        },
        "role_proxy_metrics": role_rows,
        "remaining_limits": [
            "JSL official full history missing; official one-year extract only audits the replica and replica is not official index",
            "formal Guorn small-cap strategy NAV not supplied",
            "coupon dates are anniversary-model cash events; personal tax and nonstandard recovery remain outside primary contract",
        ],
    }
    SUMMARY_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    generated = sorted(
        path for path in output.iterdir()
        if path.is_file() and path.name != "run_manifest.json"
    )
    run_manifest = {
        "package_version": result["package_version"],
        "generated_files": {
            path.name: {"sha256": file_sha256(path), "bytes": path.stat().st_size}
            for path in generated
        },
    }
    (output / "run_manifest.json").write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"summaries": summaries, "comparisons": comparisons}, ensure_ascii=False))


if __name__ == "__main__":
    main()
