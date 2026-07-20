from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = ROOT / "data/processed/cb_research_20260720"
EVIDENCE_MANIFEST = ROOT / "docs/策略/证据/可转债-可复现包清单.json"

RATING_RISK_DATES = {
    "128062": "2021-03-06", "128085": "2021-01-26", "113595": "2021-08-14",
    "113576": "2021-11-04", "128100": "2022-02-19", "128022": "2022-06-16",
    "113578": "2022-12-31", "123096": "2023-06-27", "113596": "2024-02-09",
    "123162": "2024-06-26", "113601": "2024-06-29", "118027": "2025-02-22",
    "118020": "2025-06-24", "110081": "2026-05-28", "123142": "2026-06-26",
}
ADDITIONAL_HARD_RISK_DATES = {
    "128114": "2022-06-09", "128044": "2024-06-22", "128119": "2026-06-06",
}
SH_INTERVALS = {
    "600370": [("2026-04-29", None)], "600388": [("2022-04-30", "2023-07-14")],
    "600745": [("2026-04-30", None)], "600831": [("2024-07-06", "2025-11-07")],
    "601020": [("2021-04-29", "2022-05-25")], "603007": [("2021-04-30", "2026-04-18")],
    "603023": [("2024-04-30", "2025-05-31")], "603030": [("2023-04-29", "2024-07-09")],
    "603363": [("2024-04-30", "2025-05-17")], "603557": [("2021-04-29", None)],
    "603608": [("2024-04-30", "2025-04-30")], "603810": [("2019-07-15", "2019-11-13")],
    "603822": [("2025-12-10", None)], "688066": [("2026-04-30", None)],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def is_risk_name(value: object) -> bool:
    text = str(value).upper()
    return "ST" in text or "PT" in text


def build_sz_intervals(path: Path, issuers: set[str]):
    changes = pd.read_pickle(path).copy()
    changes["issuer"] = changes["证券代码"].astype(str).str.zfill(6)
    changes["date"] = pd.to_datetime(changes["变更日期"]).dt.normalize()
    changes = changes[changes["issuer"].isin(issuers)].sort_values(["issuer", "date"])
    output: dict[str, list[tuple[pd.Timestamp, pd.Timestamp | None]]] = defaultdict(list)
    for issuer, frame in changes.groupby("issuer"):
        active = False
        start = None
        for row in frame.itertuples(index=False):
            before = is_risk_name(getattr(row, "变更前简称"))
            after = is_risk_name(getattr(row, "变更后简称"))
            if not before and after and not active:
                active, start = True, pd.Timestamp(row.date)
            elif before and not after and active and start is not None:
                output[issuer].append((start, pd.Timestamp(row.date)))
                active, start = False, None
        if active and start is not None:
            output[issuer].append((start, None))
    return output


def coupon_rates(text: object) -> list[float]:
    return [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*%", str(text))]


def parse_smallcap(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for line in payload["data"]["klines"]:
        values = line.split(",")
        rows.append({"date": values[0], "close": values[2]})
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame.dropna().drop_duplicates("date").sort_values("date")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--temp-root", type=Path, default=Path("/private/tmp"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    temp = args.temp_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "benchmarks").mkdir(exist_ok=True)

    candidate_script = temp / "ark_cb_candidate_execution_diag.py"
    master_path = temp / "ark_cb_master_expanded.pkl"
    terms_path = temp / "ark_cb_terms_expanded.pkl"
    sz_changes_path = temp / "ark_cb_szse_name_changes.pkl"
    exits_path = ROOT / "docs/策略/证据/可转债-标准退出现金可用日.csv"
    smallcap_path = temp / "index_399303.json"
    required = [candidate_script, master_path, terms_path, sz_changes_path, exits_path, smallcap_path]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"freeze inputs missing: {missing}")

    candidate = load_module("cb_freeze_candidate", candidate_script)
    original_loader = candidate.load_module

    def patched_loader(name: str, path: str):
        module = original_loader(name, path)
        if name == "homework":
            module.TAIL_RISK_KNOWN_DATES.update(RATING_RISK_DATES)
            module.TAIL_RISK_KNOWN_DATES.update(ADDITIONAL_HARD_RISK_DATES)
        return module

    candidate.load_module = patched_loader
    _, panel, source_coverage = candidate.build_panel()
    panel["bond_code"] = panel["bond_code"].astype(str).str.zfill(6)
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()

    master = pd.read_pickle(master_path).copy()
    master["bond_code"] = master["SECURITY_CODE"].astype(str).str.zfill(6)
    master["issuer"] = master["CONVERT_STOCK_CODE"].astype(str).str.zfill(6)
    issuers = set(master.loc[master["bond_code"].isin(panel["bond_code"]), "issuer"])
    intervals = build_sz_intervals(sz_changes_path, issuers)
    for issuer, values in SH_INTERVALS.items():
        intervals[issuer].extend(
            (pd.Timestamp(start), pd.Timestamp(end) if end else None) for start, end in values
        )

    intervals_by_bond: dict[str, list[tuple[pd.Timestamp, pd.Timestamp | None]]] = defaultdict(list)
    for issuer, values in intervals.items():
        for bond in master.loc[master["issuer"].eq(issuer), "bond_code"]:
            intervals_by_bond[bond].extend(values)

    panel["st_risk_active"] = False
    risk_rows = []
    for bond, values in intervals_by_bond.items():
        bond_mask = panel["bond_code"].eq(bond)
        for start, end in values:
            mask = bond_mask & panel["date"].ge(start)
            if end is not None:
                mask &= panel["date"].lt(end)
            panel.loc[mask, "st_risk_active"] = True
            risk_rows.append({"bond_code": bond, "risk_date": start, "risk_type": "st", "risk_end": end})

    static_risks = panel[["bond_code", "call_notice_date", "tail_risk_date"]].drop_duplicates("bond_code")
    for row in static_risks.itertuples(index=False):
        if pd.notna(row.call_notice_date):
            risk_rows.append({"bond_code": row.bond_code, "risk_date": row.call_notice_date,
                              "risk_type": "call_notice", "risk_end": pd.NaT})
        if pd.notna(row.tail_risk_date):
            risk_rows.append({"bond_code": row.bond_code, "risk_date": row.tail_risk_date,
                              "risk_type": "hard_credit", "risk_end": pd.NaT})

    keep = [
        "bond_code", "date", "open_sina", "close_em", "conv_premium", "double_low",
        "double_low_z252", "balance_bil", "contract_maturity", "call_notice_date",
        "tail_risk_date", "st_risk_active",
    ]
    frozen = panel[keep].copy().drop_duplicates(["bond_code", "date"], keep="last")
    frozen.sort_values(["bond_code", "date"], inplace=True)
    panel_file = output / "frozen_panel.pkl.gz"
    frozen.to_pickle(panel_file, compression="gzip")

    risks = pd.DataFrame(risk_rows).drop_duplicates().sort_values(["risk_date", "bond_code"])
    risk_file = output / "risk_events.csv"
    risks.to_csv(risk_file, index=False)

    exits = pd.read_csv(exits_path, dtype={"bond_code": str})
    exits["bond_code"] = exits["bond_code"].str.zfill(6)
    exit_file = output / "standard_exits.csv"
    exits.to_csv(exit_file, index=False)

    trading_by_bond = {
        bond: pd.DatetimeIndex(group["date"].sort_values().unique())
        for bond, group in frozen.groupby("bond_code", sort=False)
    }
    coupons = []
    for row in master.itertuples(index=False):
        rates = coupon_rates(row.INTEREST_RATE_EXPLAIN)
        value_date = pd.Timestamp(row.VALUE_DATE).normalize()
        contract_maturity = value_date + pd.DateOffset(years=len(rates))
        dates = trading_by_bond.get(row.bond_code)
        if dates is None:
            continue
        for year_no, rate in enumerate(rates, start=1):
            nominal = value_date + pd.DateOffset(years=year_no)
            if nominal >= contract_maturity:
                continue
            choices = dates[dates >= nominal]
            if len(choices):
                coupons.append({
                    "bond_code": row.bond_code, "payment_date": choices.min(),
                    "nominal_date": nominal, "coupon_per_100_pretax": rate,
                    "source": "INTEREST_RATE_EXPLAIN anniversary model",
                })
    coupon_file = output / "coupon_events.csv"
    pd.DataFrame(coupons).drop_duplicates(["bond_code", "nominal_date"]).to_csv(coupon_file, index=False)

    smallcap_file = output / "benchmarks/smallcap_proxy_399303.csv"
    parse_smallcap(smallcap_path).to_csv(smallcap_file, index=False)

    benchmark_readme = output / "benchmarks/README.md"
    benchmark_readme.write_text(
        "# External benchmark inputs\n\n"
        "- `jsl_cb_equal_weight.csv`: optional official full-history JSL-CB-EW, columns `date,index`.\n"
        "- `jsl_cb_equal_weight_1y.csv`: optional official one-year logged-in extract, cross-check only.\n"
        "- `hs300_total_return.csv`: optional official H00300, columns `date,index`.\n"
        "- `smallcap_proxy_399303.csv`: frozen SMALLCAP-PROXY, never the formal stock strategy.\n\n"
        "Absent optional files must be reported as `missing`; the runner never substitutes another series silently.\n",
        encoding="utf-8",
    )

    generated = [panel_file, risk_file, exit_file, coupon_file, smallcap_file, benchmark_readme]
    optional_benchmarks = {
        "JSL-CB-EW": output / "benchmarks/jsl_cb_equal_weight.csv",
        "JSL-CB-EW-1Y": output / "benchmarks/jsl_cb_equal_weight_1y.csv",
        "HS300-TR": output / "benchmarks/hs300_total_return.csv",
    }
    generated.extend(path for path in optional_benchmarks.values() if path.exists())
    manifest = {
        "package_version": "CB-REPRO-20260720-v1",
        "created_from_temporary_research": True,
        "contract": {
            "variants": ["B0_BASE", "C_BAL", "C_OFF"],
            "signal": "calendar 10/20/30, next legal trading day; execute following open",
            "target": 20,
            "fee_each_side": 0.001,
            "missing_open": "target slot remains cash; locked old position keeps occupying capital",
            "returns": ["price_ex_coupon", "pretax_coupon_model"],
        },
        "coverage": {
            **source_coverage,
            "frozen_rows": int(len(frozen)),
            "frozen_bonds": int(frozen["bond_code"].nunique()),
            "risk_events": int(len(risks)),
            "coupon_events": int(len(coupons)),
        },
        "benchmark_status_at_freeze": {
            "U-EW": "available_from_frozen_panel",
            "JSL-CB-EW": "available_official_full_history" if optional_benchmarks["JSL-CB-EW"].exists()
            else "missing_full_history; logged-in ordinary account exposes at most one year",
            "JSL-CB-EW-1Y": "available_official_one_year_crosscheck" if optional_benchmarks["JSL-CB-EW-1Y"].exists()
            else "missing",
            "JSL-CB-EW-REPLICA": "derivable_from_frozen_panel_using_public_rules",
            "HS300-TR": "available_official_H00300" if optional_benchmarks["HS300-TR"].exists()
            else "missing; official H00300 required",
            "SMALLCAP-PROXY": "available; not formal stock strategy",
        },
        "environment": {
            "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__,
        },
        "source_files": {
            str(path): {"sha256": sha256(path), "bytes": path.stat().st_size} for path in required
        },
        "generated_files": {
            str(path.relative_to(output)): {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in generated
        },
    }
    manifest_file = output / "manifest.json"
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    EVIDENCE_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "coverage": manifest["coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
