from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = ROOT / "docs/策略/证据/小市值股票策略/果仁正式小市值策略-净值与合同.json"
DEFAULT_CANDIDATE_DIR = ROOT / "docs/策略/证据/可转债-统一合同重跑"
DEFAULT_DAILY = ROOT / "docs/策略/证据/小市值股票策略/果仁正式小市值策略-日频净值.csv"
DEFAULT_SUMMARY = ROOT / "docs/策略/证据/可转债-候选与果仁正式小市值组合职责.csv"
DEFAULT_AUDIT = ROOT / "docs/策略/证据/可转债-候选与果仁正式小市值组合职责.json"

VARIANTS = ("B0_BASE", "C_BAL", "C_OFF")
ANCHORS = {
    "极低": (0.7294, 0.2353, 0.0353),
    "中性": (0.5294, 0.3529, 0.1176),
    "极高": (0.2941, 0.4706, 0.2353),
}


def maximum_drawdown(returns: pd.Series) -> tuple[float, pd.Timestamp, pd.Timestamp]:
    nav = (1 + returns).cumprod()
    drawdown = nav / nav.cummax() - 1
    trough = drawdown.idxmin()
    peak = nav.loc[:trough].idxmax()
    return float(drawdown.loc[trough]), peak, trough


def period_return(returns: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    selected = returns.loc[(returns.index >= start) & (returns.index <= end)]
    return float((1 + selected).prod() - 1)


def cagr(returns: pd.Series) -> float:
    years = (returns.index[-1] - returns.index[0]).days / 365.25
    return float((1 + returns).prod() ** (1 / years) - 1)


def frequency_correlation(candidate: pd.Series, stock: pd.Series, frequency: str) -> tuple[float, int]:
    joined = pd.concat([candidate, stock], axis=1, keys=["candidate", "stock"]).dropna()
    if frequency == "D":
        returns = joined
    else:
        returns = (1 + joined).cumprod().resample(frequency).last().pct_change(fill_method=None).dropna()
    return float(returns.corr().iloc[0, 1]), int(len(returns))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--candidate-dir", type=Path, default=DEFAULT_CANDIDATE_DIR)
    parser.add_argument("--daily-output", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--audit-output", type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()

    payload = json.loads(args.source.read_text(encoding="utf-8"))
    chart = payload["chart"]["sheet_data"]
    dates = pd.to_datetime(chart["row"][0]["data"][1])
    stock = pd.DataFrame({
        "date": dates,
        "nav": 1 + np.asarray(chart["meas_data"][1], dtype=float),
    })
    stock["daily_return"] = stock["nav"].pct_change(fill_method=None).fillna(0.0)
    stock["cumulative_return"] = stock["nav"] - 1
    args.daily_output.parent.mkdir(parents=True, exist_ok=True)
    stock.to_csv(args.daily_output, index=False)

    stock_returns = stock.set_index("date")["daily_return"]
    records: dict[str, dict] = {}
    rows = []
    for variant in VARIANTS:
        candidate_path = args.candidate_dir / f"{variant}-price_ex_coupon-daily.csv"
        candidate = pd.read_csv(candidate_path, parse_dates=["date"]).set_index("date")["daily_return"]
        common = pd.concat(
            [candidate.rename("candidate"), stock_returns.rename("stock")], axis=1
        ).dropna()
        candidate_common, stock_common = common["candidate"], common["stock"]
        variance = float(stock_common.var(ddof=1))
        beta = float(common.cov().loc["candidate", "stock"] / variance)
        stock_mdd, stock_peak, stock_trough = maximum_drawdown(stock_common)
        candidate_mdd, candidate_peak, candidate_trough = maximum_drawdown(candidate_common)
        rolling20 = (1 + stock_common).rolling(20).apply(np.prod, raw=True) - 1
        worst20_end = rolling20.idxmin()
        worst20_start = common.index[common.index.get_loc(worst20_end) - 19]
        tail_cutoff = float(stock_common.quantile(0.05))
        tail = common[common["stock"] <= tail_cutoff]

        anchor_rows = []
        for label, (stock_weight, cb_weight, cash_weight) in ANCHORS.items():
            mixed = stock_weight * stock_common + cb_weight * candidate_common
            mixed_mdd, mixed_peak, mixed_trough = maximum_drawdown(mixed)
            anchor_rows.append({
                "anchor": label,
                "stock_weight": stock_weight,
                "cb_weight": cb_weight,
                "cash_weight": cash_weight,
                "cagr": cagr(mixed),
                "max_drawdown": mixed_mdd,
                "drawdown_peak": str(mixed_peak.date()),
                "drawdown_trough": str(mixed_trough.date()),
            })

        daily_corr, daily_count = frequency_correlation(candidate_common, stock_common, "D")
        weekly_corr, weekly_count = frequency_correlation(candidate_common, stock_common, "W-FRI")
        monthly_corr, monthly_count = frequency_correlation(candidate_common, stock_common, "ME")
        record = {
            "candidate": variant,
            "observations": int(len(common)),
            "start": str(common.index[0].date()),
            "end": str(common.index[-1].date()),
            "candidate_cagr": cagr(candidate_common),
            "formal_smallcap_cagr": cagr(stock_common),
            "correlation_daily": daily_corr,
            "correlation_weekly": weekly_corr,
            "correlation_monthly": monthly_corr,
            "daily_observations": daily_count,
            "weekly_observations": weekly_count,
            "monthly_observations": monthly_count,
            "beta_daily": beta,
            "formal_smallcap_max_drawdown": stock_mdd,
            "formal_smallcap_mdd_peak": str(stock_peak.date()),
            "formal_smallcap_mdd_trough": str(stock_trough.date()),
            "candidate_return_during_smallcap_mdd": period_return(candidate_common, stock_peak, stock_trough),
            "candidate_max_drawdown": candidate_mdd,
            "candidate_mdd_peak": str(candidate_peak.date()),
            "candidate_mdd_trough": str(candidate_trough.date()),
            "smallcap_return_worst20": period_return(stock_common, worst20_start, worst20_end),
            "candidate_return_same_worst20": period_return(candidate_common, worst20_start, worst20_end),
            "worst20_start": str(worst20_start.date()),
            "worst20_end": str(worst20_end.date()),
            "smallcap_down_days": int((stock_common < 0).sum()),
            "smallcap_mean_down_day": float(stock_common[stock_common < 0].mean()),
            "candidate_mean_on_smallcap_down_days": float(candidate_common[stock_common < 0].mean()),
            "candidate_nonnegative_on_smallcap_down_days": float((candidate_common[stock_common < 0] >= 0).mean()),
            "smallcap_5pct_tail_threshold": tail_cutoff,
            "smallcap_tail_mean": float(tail["stock"].mean()),
            "candidate_mean_on_smallcap_5pct_tail": float(tail["candidate"].mean()),
            "smallcap_below_minus2_days": int((stock_common <= -0.02).sum()),
            "candidate_mean_when_smallcap_below_minus2": float(candidate_common[stock_common <= -0.02].mean()),
            "anchors": anchor_rows,
        }
        records[variant] = record
        rows.append({key: value for key, value in record.items() if key != "anchors"})

    pd.DataFrame(rows).to_csv(args.summary_output, index=False)
    audit = {
        "contract": {
            "stock_return": "pct_change(1 + Guorn chart cumulative strategy return)",
            "candidate_return": "price_ex_coupon_net_10bp",
            "mix": "daily constant weight; cash return zero; diagnostic only",
        },
        "source_file": str(args.source),
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "candidates": records,
    }
    args.audit_output.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
