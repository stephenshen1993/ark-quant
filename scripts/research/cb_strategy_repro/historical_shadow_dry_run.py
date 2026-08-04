from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from engine import VARIANTS, eligible, score, signal_dates, load_inputs


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = ROOT / "data/processed/cb_research_20260720"
DEFAULT_OUTPUT = ROOT / "docs/策略/证据"
PROTOCOL_VERSION = "CB-SHADOW-20260720-v1"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_complete_signal(panel: pd.DataFrame) -> pd.Timestamp:
    trading_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    signals = signal_dates(trading_dates)
    complete = [date for date in signals if date < trading_dates.max()]
    if not complete:
        raise ValueError("no complete signal date before the last panel date")
    return pd.Timestamp(complete[-1]).normalize()


def next_trading_day(panel: pd.DataFrame, signal_date: pd.Timestamp) -> pd.Timestamp:
    trading_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    later = trading_dates[trading_dates > signal_date]
    if not len(later):
        raise ValueError(f"no execution date after {signal_date.date()}")
    return pd.Timestamp(later.min()).normalize()


def safety_eligible(snapshot: pd.DataFrame, signal_date: pd.Timestamp) -> pd.Series:
    required = ["close_em", "conv_premium", "double_low", "contract_maturity"]
    frame = snapshot.copy()
    ok = frame[required].notna().all(axis=1)
    ok &= frame["contract_maturity"] - signal_date >= pd.Timedelta(days=365)
    ok &= frame["call_notice_date"].isna() | frame["call_notice_date"].gt(signal_date)
    ok &= frame["tail_risk_date"].isna() | frame["tail_risk_date"].gt(signal_date)
    ok &= ~frame["st_risk_active"].fillna(False)
    return ok


def exclusion_reasons(row: pd.Series, signal_date: pd.Timestamp) -> str:
    reasons: list[str] = []
    for column in ("close_em", "conv_premium", "double_low", "contract_maturity"):
        if pd.isna(row[column]):
            reasons.append(f"missing_{column}")
    if pd.notna(row["contract_maturity"]) and row["contract_maturity"] - signal_date < pd.Timedelta(days=365):
        reasons.append("remaining_less_than_365d")
    if pd.notna(row["call_notice_date"]) and row["call_notice_date"] <= signal_date:
        reasons.append("call_notice_active")
    if pd.notna(row["tail_risk_date"]) and row["tail_risk_date"] <= signal_date:
        reasons.append("tail_risk_active")
    if bool(row["st_risk_active"]) if pd.notna(row["st_risk_active"]) else False:
        reasons.append("st_risk_active")
    return "|".join(reasons)


def ranked_variant(snapshot: pd.DataFrame, signal_date: pd.Timestamp, variant: str) -> pd.DataFrame:
    universe = eligible(snapshot, signal_date, variant)
    if universe.empty:
        return pd.DataFrame(columns=["bond_code", f"{variant.lower()}_rank"])
    ranked = universe.assign(_score=score(universe, variant)).sort_values(
        ["_score", "bond_code"], ascending=[True, True]
    )
    return pd.DataFrame({
        "bond_code": ranked["bond_code"].astype(str).str.zfill(6),
        f"{variant.lower()}_score": ranked["_score"].to_numpy(),
        f"{variant.lower()}_rank": range(1, len(ranked) + 1),
    })


def build_universe(panel: pd.DataFrame, signal_date: pd.Timestamp) -> pd.DataFrame:
    snapshot = panel[panel["date"].eq(signal_date)].copy()
    if snapshot.empty:
        raise ValueError(f"signal date {signal_date.date()} is not in panel")
    snapshot["bond_code"] = snapshot["bond_code"].astype(str).str.zfill(6)
    output = snapshot[[
        "bond_code",
        "date",
        "open_sina",
        "close_em",
        "conv_premium",
        "double_low",
        "double_low_z252",
        "balance_bil",
        "contract_maturity",
        "call_notice_date",
        "tail_risk_date",
        "st_risk_active",
    ]].copy()
    output["eligible_common"] = safety_eligible(snapshot, signal_date).to_numpy()
    output["exclusion_reasons"] = snapshot.apply(exclusion_reasons, axis=1, signal_date=signal_date)

    for variant in VARIANTS:
        ranked = ranked_variant(snapshot, signal_date, variant)
        output = output.merge(ranked, on="bond_code", how="left")

    def selected_flags(row: pd.Series) -> str:
        flags = []
        for variant in VARIANTS:
            rank = row.get(f"{variant.lower()}_rank")
            if pd.notna(rank) and int(rank) <= 20:
                flags.append(variant)
        return "|".join(flags)

    output["selected_flags"] = output.apply(selected_flags, axis=1)
    rank_cols = [
        "b0_base_score", "b0_base_rank",
        "c_bal_score", "c_bal_rank",
        "c_off_score", "c_off_rank",
        "selected_flags",
    ]
    base_cols = [column for column in output.columns if column not in rank_cols]
    return output[base_cols + rank_cols].sort_values(["eligible_common", "bond_code"], ascending=[False, True])


def build_manifest(input_root: Path, panel: pd.DataFrame, signal_date: pd.Timestamp, universe: pd.DataFrame) -> dict:
    execution_date = next_trading_day(panel, signal_date)
    input_files = {
        "frozen_panel.pkl.gz": input_root / "frozen_panel.pkl.gz",
        "manifest.json": input_root / "manifest.json",
        "risk_events.csv": input_root / "risk_events.csv",
        "standard_exits.csv": input_root / "standard_exits.csv",
        "coupon_events.csv": input_root / "coupon_events.csv",
    }
    variant_counts = {}
    top20_codes = {}
    for variant in VARIANTS:
        rank_col = f"{variant.lower()}_rank"
        selected = universe[universe[rank_col].le(20).fillna(False)]["bond_code"].tolist()
        variant_counts[variant] = int(universe[rank_col].notna().sum())
        top20_codes[variant] = selected
    return {
        "protocol_version": PROTOCOL_VERSION,
        "dry_run_id": f"CB-SHADOW-HIST-DRYRUN-{signal_date.strftime('%Y%m%d')}",
        "data_status": "dry_run_invalid_for_forward_sample",
        "invalid_for_forward_reason": "historical frozen input was already known before this dry run; use only for format and ranking-tool validation",
        "signal_date": str(signal_date.date()),
        "execution_date": str(execution_date.date()),
        "candidate_ids": list(VARIANTS),
        "input_package": str(input_root),
        "source_manifest": {
            name: {"sha256": file_sha256(path), "bytes": path.stat().st_size}
            for name, path in input_files.items()
            if path.exists()
        },
        "panel_range": {
            "start": str(pd.Timestamp(panel["date"].min()).date()),
            "end": str(pd.Timestamp(panel["date"].max()).date()),
            "rows": int(len(panel)),
            "bonds": int(panel["bond_code"].nunique()),
        },
        "eligible_common_count": int(universe["eligible_common"].sum()),
        "rankable_counts": variant_counts,
        "top20_codes": top20_codes,
        "validation_scope": [
            "manifest schema",
            "universe schema",
            "initial execution schema",
            "same-day ranking reproducibility",
            "candidate top20 coexistence",
            "missing/exclusion reason visibility",
        ],
        "not_validation_scope": [
            "forward sample performance",
            "candidate adoption",
            "V0 replacement",
            "parameter tuning",
            "real trading order generation",
        ],
    }


def build_execution(panel: pd.DataFrame, signal_date: pd.Timestamp, universe: pd.DataFrame) -> pd.DataFrame:
    execution_date = next_trading_day(panel, signal_date)
    execution_snapshot = panel[panel["date"].eq(execution_date)].copy()
    execution_snapshot["bond_code"] = execution_snapshot["bond_code"].astype(str).str.zfill(6)
    open_prices = execution_snapshot.set_index("bond_code")["open_sina"].to_dict()
    rows = []
    for variant in VARIANTS:
        rank_col = f"{variant.lower()}_rank"
        selected = universe[universe[rank_col].le(20).fillna(False)].sort_values(rank_col)
        for row in selected.itertuples(index=False):
            code = str(row.bond_code).zfill(6)
            open_price = open_prices.get(code)
            has_open = pd.notna(open_price) and float(open_price) > 0
            rows.append({
                "candidate_id": variant,
                "signal_date": signal_date.date().isoformat(),
                "execution_date": execution_date.date().isoformat(),
                "bond_code": code,
                "target_weight": 1 / 20,
                "open_price_observed": float(open_price) if has_open else "",
                "action": "buy" if has_open else "unfilled_cash",
                "one_way_cost": 0.001 if has_open else 0.0,
                "execution_note": "" if has_open else "missing_or_nonpositive_open_price",
            })
    return pd.DataFrame(rows)


def build_portfolio_review(
    execution: pd.DataFrame,
    signal_date: pd.Timestamp,
    execution_date: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    portfolio_rows = []
    for candidate_id, group in execution.groupby("candidate_id", sort=False):
        bought = group[group["action"].eq("buy")]
        unfilled = group[group["action"].eq("unfilled_cash")]
        target_weight = float(group["target_weight"].sum())
        bought_weight = float(bought["target_weight"].sum())
        portfolio_rows.append({
            "candidate_id": candidate_id,
            "date": execution_date.date().isoformat(),
            "dry_run_nav": "",
            "cash_weight": float(1.0 - bought_weight),
            "locked_weight": 0.0,
            "holdings_count": int(len(bought)),
            "target_rows": int(len(group)),
            "unfilled_cash_rows": int(len(unfilled)),
            "target_weight_sum": target_weight,
            "bought_weight_sum": bought_weight,
            "risk_events": "",
            "data_status": "dry_run_invalid_for_forward_sample",
        })
    portfolio = pd.DataFrame(portfolio_rows)
    review = pd.DataFrame([{
        "signal_date": signal_date.date().isoformat(),
        "execution_date": execution_date.date().isoformat(),
        "valid_observation_no": 0,
        "governance_stage": "historical_dry_run_toolchain_only",
        "parameter_change": False,
        "data_status": "dry_run_invalid_for_forward_sample",
        "review_note": "Historical replay validates file shape and execution facts only; never counts as a forward observation.",
    }])
    return portfolio, review


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--signal-date", help="YYYY-MM-DD; defaults to latest complete historical signal")
    args = parser.parse_args()

    input_root = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    panel, _, _, _ = load_inputs(input_root)
    signal_date = (
        pd.Timestamp(args.signal_date).normalize()
        if args.signal_date
        else latest_complete_signal(panel)
    )
    universe = build_universe(panel, signal_date)
    execution = build_execution(panel, signal_date, universe)
    execution_date = next_trading_day(panel, signal_date)
    portfolio, review = build_portfolio_review(execution, signal_date, execution_date)
    manifest = build_manifest(input_root, panel, signal_date, universe)
    prefix = output_dir / f"可转债-历史影子dryrun-{signal_date.strftime('%Y%m%d')}"
    universe_path = prefix.with_name(prefix.name + "-universe.csv")
    execution_path = prefix.with_name(prefix.name + "-execution.csv")
    portfolio_path = prefix.with_name(prefix.name + "-portfolio.csv")
    review_path = prefix.with_name(prefix.name + "-review.csv")
    manifest_path = prefix.with_name(prefix.name + "-manifest.json")
    universe.to_csv(universe_path, index=False)
    execution.to_csv(execution_path, index=False)
    portfolio.to_csv(portfolio_path, index=False)
    review.to_csv(review_path, index=False)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "manifest": str(manifest_path),
        "universe": str(universe_path),
        "execution": str(execution_path),
        "portfolio": str(portfolio_path),
        "review": str(review_path),
        "signal_date": manifest["signal_date"],
        "execution_date": manifest["execution_date"],
        "eligible_common_count": manifest["eligible_common_count"],
        "rankable_counts": manifest["rankable_counts"],
        "execution_rows": int(len(execution)),
        "unfilled_cash_rows": int(execution["action"].eq("unfilled_cash").sum()),
        "portfolio_rows": int(len(portfolio)),
        "data_status": manifest["data_status"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
