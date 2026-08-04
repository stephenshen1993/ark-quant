from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from engine import VARIANTS, eligible, score


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = ROOT / "docs/策略/证据"
PROTOCOL_VERSION = "CB-SHADOW-20260720-v1"
FIXED_CALENDAR_SIGNAL_DAYS = (10, 20, 30)
UNIVERSE_COLUMNS = [
    "bond_code",
    "bond_name",
    "stock_code",
    "signal_date",
    "close_full_price",
    "conversion_premium_pct",
    "double_low",
    "double_low_z252",
    "balance_bil",
    "contract_maturity",
    "remaining_days",
    "call_status",
    "credit_risk_state",
    "tradability_state",
    "eligible_common",
    "exclusion_reasons",
    "b0_base_score",
    "b0_base_rank",
    "c_bal_score",
    "c_bal_rank",
    "c_off_score",
    "c_off_rank",
    "selected_flags",
]
RISK_QUEUE_COLUMNS = [
    "signal_date",
    "bond_code",
    "bond_name",
    "stock_code",
    "manual_check_reason",
    "primary_source",
    "backup_source",
    "status",
    "resolution_note",
]
LIVE_TO_ENGINE_COLUMNS = {
    "close_full_price": "close_em",
    "conversion_premium_pct": "conv_premium",
}
FREE_SOURCE_INPUTS = {
    "cb_spot": {
        "purpose": "convertible bond full-price close snapshot",
        "required": True,
    },
    "security_master": {
        "purpose": "bond-stock mapping, conversion price and contract fields",
        "required": True,
    },
    "stock_close": {
        "purpose": "underlying stock close for conversion value",
        "required": True,
    },
}
SECURITY_MASTER_REQUIRED_COLUMNS = ["bond_code", "stock_code", "convert_price", "contract_maturity"]
SECURITY_MASTER_ENHANCEMENT_COLUMNS = ["double_low_z252", "balance_bil"]


def is_calendar_signal_date(signal_date: pd.Timestamp) -> bool:
    return int(pd.Timestamp(signal_date).day) in FIXED_CALENDAR_SIGNAL_DAYS


def default_source_checks() -> list[dict]:
    return [
        {
            "source_id": "akshare_sina_cb_spot",
            "purpose": "convertible bond close snapshot",
            "status": "not_run",
            "blocking_if_unavailable": True,
        },
        {
            "source_id": "akshare_jsl_redeem",
            "purpose": "call notice and current status cross-check",
            "status": "not_run",
            "blocking_if_unavailable": False,
        },
        {
            "source_id": "akshare_stock_single_daily",
            "purpose": "underlying stock close for conversion value",
            "status": "not_run",
            "blocking_if_unavailable": True,
        },
        {
            "source_id": "manual_risk_queue",
            "purpose": "ST, suspension, call, credit and issuer-risk exceptions",
            "status": "not_run",
            "blocking_if_unavailable": True,
        },
    ]


def default_probe_functions() -> dict[str, Callable[[], object]]:
    import akshare as ak

    return {
        "akshare_sina_cb_spot": ak.bond_zh_hs_cov_spot,
        "akshare_jsl_redeem": ak.bond_cb_redeem_jsl,
        "akshare_stock_single_daily": first_successful_probe([
            (
                "stock_zh_a_hist",
                lambda: ak.stock_zh_a_hist(
                    symbol="000001", period="daily", start_date="20260720", end_date="20260724", adjust=""
                ),
            ),
            (
                "stock_zh_a_daily",
                lambda: ak.stock_zh_a_daily(
                    symbol="sz000001", start_date="20260720", end_date="20260724", adjust=""
                ),
            ),
        ]),
    }


def first_successful_probe(probes: list[tuple[str, Callable[[], object]]]) -> Callable[[], object]:
    def run() -> object:
        errors = []
        for source_name, probe in probes:
            try:
                value = probe()
                if hasattr(value, "attrs"):
                    value.attrs["probe_source"] = source_name
                return value
            except Exception as exc:
                errors.append(f"{source_name}: {type(exc).__name__}: {exc}")
        raise RuntimeError("; ".join(errors))

    return run


def run_source_probes(probes: dict[str, Callable[[], object]] | None = None) -> dict[str, dict]:
    probes = probes or default_probe_functions()
    results: dict[str, dict] = {}
    for source_id, probe in probes.items():
        started = time.monotonic()
        try:
            value = probe()
            rows = len(value) if hasattr(value, "__len__") else None
            columns = "|".join(str(column) for column in list(value.columns)[:12]) if hasattr(value, "columns") else ""
            results[source_id] = {
                "status": "available",
                "rows": int(rows) if rows is not None else "",
                "columns": columns,
                "probe_source": getattr(value, "attrs", {}).get("probe_source", ""),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": "",
            }
        except Exception as exc:
            results[source_id] = {
                "status": "failed",
                "rows": "",
                "columns": "",
                "probe_source": "",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }
    return results


def universe_collection_status(source_checks: list[dict]) -> dict:
    blocking = [
        str(item.get("source_id"))
        for item in source_checks
        if bool(item.get("blocking_if_unavailable")) and item.get("status") != "available"
    ]
    if blocking:
        return {"status": "blocked_by_source_checks", "blocking_sources": blocking}
    return {"status": "ready_for_signal_collection", "blocking_sources": []}


def build_universe_template() -> pd.DataFrame:
    return pd.DataFrame(columns=UNIVERSE_COLUMNS)


def build_risk_queue_template() -> pd.DataFrame:
    return pd.DataFrame(columns=RISK_QUEUE_COLUMNS)


def validate_security_master(master: pd.DataFrame) -> dict:
    missing_columns = [column for column in SECURITY_MASTER_REQUIRED_COLUMNS if column not in master.columns]
    blocking_reasons = [f"missing_columns:{'|'.join(missing_columns)}"] if missing_columns else []
    issue_rows: list[dict] = []
    if not missing_columns:
        frame = master.copy()
        frame["bond_code"] = frame["bond_code"].map(normalize_security_code)
        frame["stock_code"] = frame["stock_code"].map(normalize_security_code)
        frame["convert_price"] = pd.to_numeric(frame["convert_price"], errors="coerce")
        frame["contract_maturity"] = pd.to_datetime(frame["contract_maturity"], errors="coerce")
        for row in frame.itertuples(index=False):
            issues: list[str] = []
            bond_code = str(getattr(row, "bond_code"))
            if not bond_code:
                issues.append("missing_bond_code")
            if not str(getattr(row, "stock_code")):
                issues.append("missing_stock_code")
            convert_price = getattr(row, "convert_price")
            if pd.isna(convert_price) or float(convert_price) <= 0:
                issues.append("missing_convert_price")
            if pd.isna(getattr(row, "contract_maturity")):
                issues.append("missing_contract_maturity")
            if issues:
                issue_rows.append({"bond_code": bond_code, "issue": "|".join(issues)})
        if issue_rows:
            blocking_reasons.append("row_level_required_values_missing")
    missing_enhancement_columns = [
        column for column in SECURITY_MASTER_ENHANCEMENT_COLUMNS if column not in master.columns
    ]
    return {
        "ready": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "missing_enhancement_columns": missing_enhancement_columns,
        "issues": pd.DataFrame(issue_rows, columns=["bond_code", "issue"]),
    }


def frame_audit_row(source_id: str, purpose: str, required: bool, frame: object, elapsed_seconds: float) -> dict:
    rows = len(frame) if hasattr(frame, "__len__") else ""
    columns = "|".join(str(column) for column in list(frame.columns)[:12]) if hasattr(frame, "columns") else ""
    return {
        "source_id": source_id,
        "purpose": purpose,
        "status": "available",
        "required": required,
        "rows": int(rows) if rows != "" else "",
        "columns": columns,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "error": "",
    }


def collect_free_source_inputs(
    providers: dict[str, Callable[[], pd.DataFrame]],
    source_specs: dict[str, dict] | None = None,
) -> dict:
    source_specs = source_specs or FREE_SOURCE_INPUTS
    frames: dict[str, pd.DataFrame] = {}
    audit_rows: list[dict] = []
    for source_id, spec in source_specs.items():
        started = time.monotonic()
        try:
            frame = providers[source_id]()
            frames[source_id] = frame.copy() if hasattr(frame, "copy") else pd.DataFrame(frame)
            audit_rows.append(frame_audit_row(
                source_id,
                str(spec["purpose"]),
                bool(spec["required"]),
                frame,
                time.monotonic() - started,
            ))
        except Exception as exc:
            audit_rows.append({
                "source_id": source_id,
                "purpose": str(spec["purpose"]),
                "status": "failed",
                "required": bool(spec["required"]),
                "rows": "",
                "columns": "",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            })
    audit = pd.DataFrame(audit_rows)
    ready = not audit[audit["required"] & audit["status"].ne("available")].any().any()
    return {"frames": frames, "audit": audit, "ready": bool(ready)}


def build_shadow_universe_bundle(
    providers: dict[str, Callable[[], pd.DataFrame]],
    signal_date: pd.Timestamp,
    risk_inputs: dict[str, pd.DataFrame] | None = None,
) -> dict:
    inputs = collect_free_source_inputs(providers)
    if not inputs["ready"]:
        return {
            "ready": False,
            "audit": inputs["audit"],
            "frames": inputs["frames"],
            "master_validation": {"ready": False, "blocking_reasons": ["source_collection_failed"]},
            "snapshot": pd.DataFrame(),
            "universe": build_universe_template(),
            "manual_risk_queue": build_risk_queue_template(),
        }

    frames = inputs["frames"]
    master_validation = validate_security_master(frames["security_master"])
    if not master_validation["ready"]:
        return {
            "ready": False,
            "audit": inputs["audit"],
            "frames": frames,
            "master_validation": master_validation,
            "snapshot": pd.DataFrame(),
            "universe": build_universe_template(),
            "manual_risk_queue": build_risk_queue_template(),
        }
    snapshot = build_snapshot_from_free_sources(
        frames["cb_spot"],
        frames["security_master"],
        frames["stock_close"],
        signal_date,
    )
    if risk_inputs is not None:
        snapshot = apply_risk_status_inputs(snapshot, risk_inputs, signal_date)
    universe = build_universe_from_snapshot(snapshot, signal_date)
    return {
        "ready": True,
        "audit": inputs["audit"],
        "frames": frames,
        "master_validation": master_validation,
        "snapshot": snapshot,
        "universe": universe,
        "manual_risk_queue": build_manual_risk_queue_from_universe(universe),
    }


def json_safe(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    if isinstance(value, pd.Series):
        return value.to_dict()
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def seal_shadow_universe_bundle(
    bundle: dict,
    signal_date: pd.Timestamp,
    generated_at: pd.Timestamp,
    output_dir: Path,
    dry_run: bool,
) -> dict[str, Path]:
    signal_date = pd.Timestamp(signal_date).normalize()
    generated_at = pd.Timestamp(generated_at)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / f"可转债-影子universe封存-{signal_date.strftime('%Y%m%d')}"

    manifest_path = prefix.with_name(prefix.name + "-manifest.json")
    audit_path = prefix.with_name(prefix.name + "-source-audit.csv")
    universe_path = prefix.with_name(prefix.name + "-universe.csv")
    risk_queue_path = prefix.with_name(prefix.name + "-manual-risk-queue.csv")
    master_issues_path = prefix.with_name(prefix.name + "-master-issues.csv")

    audit = bundle.get("audit", pd.DataFrame())
    universe = bundle.get("universe", build_universe_template())
    manual_risk_queue = bundle.get("manual_risk_queue", build_risk_queue_template())
    master_validation = bundle.get("master_validation", {})
    master_issues = master_validation.get("issues", pd.DataFrame(columns=["bond_code", "issue"]))

    audit.to_csv(audit_path, index=False)
    universe.to_csv(universe_path, index=False)
    manual_risk_queue.to_csv(risk_queue_path, index=False)
    master_issues.to_csv(master_issues_path, index=False)

    ready = bool(bundle.get("ready"))
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "snapshot_id": f"CB-SHADOW-UNIVERSE-{signal_date.strftime('%Y%m%d')}",
        "signal_date": str(signal_date.date()),
        "generated_at": generated_at.isoformat(),
        "data_status": "dry_run_invalid_for_forward_sample" if dry_run else ("pending_execution" if ready else "invalid"),
        "valid_observation_no": 0 if dry_run else None,
        "ready": ready,
        "blocking_reasons": list(master_validation.get("blocking_reasons", [])) if not ready else [],
        "missing_enhancement_columns": list(master_validation.get("missing_enhancement_columns", [])),
        "source_summary": {
            "available": int(audit["status"].eq("available").sum()) if "status" in audit.columns else 0,
            "failed": int(audit["status"].eq("failed").sum()) if "status" in audit.columns else 0,
        },
        "universe_rows": int(len(universe)),
        "manual_risk_queue_rows": int(len(manual_risk_queue)),
        "master_issue_rows": int(len(master_issues)),
        "output_files": {
            "source_audit": str(audit_path),
            "universe": str(universe_path),
            "manual_risk_queue": str(risk_queue_path),
            "master_issues": str(master_issues_path),
        },
        "candidate_ids": list(VARIANTS),
        "generates_orders": False,
        "writes_database": False,
        "modifies_v0": False,
        "not_validation_scope": [
            "candidate performance",
            "V0 replacement",
            "real trading order generation",
            "parameter tuning",
            "paid data procurement",
        ],
    }
    manifest_path.write_text(json.dumps(json_safe(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "manifest": manifest_path,
        "source_audit": audit_path,
        "universe": universe_path,
        "manual_risk_queue": risk_queue_path,
        "master_issues": master_issues_path,
    }


def build_manual_risk_queue_from_universe(universe: pd.DataFrame) -> pd.DataFrame:
    if universe.empty:
        return build_risk_queue_template()
    reasons = universe.get("exclusion_reasons", pd.Series([""] * len(universe), index=universe.index)).fillna("")
    frame = universe[reasons.ne("")].copy()
    if frame.empty:
        return build_risk_queue_template()
    queue = pd.DataFrame({
        "signal_date": frame["signal_date"],
        "bond_code": frame["bond_code"].astype(str).str.zfill(6),
        "bond_name": frame.get("bond_name", ""),
        "stock_code": frame.get("stock_code", ""),
        "manual_check_reason": frame["exclusion_reasons"],
        "primary_source": "free_source_universe",
        "backup_source": "exchange_announcement_or_logged_in_page",
        "status": "pending",
        "resolution_note": "",
    })
    return queue[RISK_QUEUE_COLUMNS].reset_index(drop=True)


def active_codes_by_date(frame: pd.DataFrame, code_col: str, date_col: str, signal_date: pd.Timestamp) -> set[str]:
    if frame.empty or code_col not in frame.columns or date_col not in frame.columns:
        return set()
    rows = frame.copy()
    rows[code_col] = rows[code_col].map(normalize_security_code)
    rows[date_col] = pd.to_datetime(rows[date_col], errors="coerce")
    signal = pd.Timestamp(signal_date).normalize()
    return set(rows[rows[date_col].le(signal)][code_col].dropna())


def apply_risk_status_inputs(
    snapshot: pd.DataFrame,
    risk_inputs: dict[str, pd.DataFrame],
    signal_date: pd.Timestamp,
) -> pd.DataFrame:
    frame = snapshot.copy()
    frame["bond_code"] = frame["bond_code"].map(normalize_security_code)
    frame["call_notice_date"] = pd.NaT
    frame["tail_risk_date"] = pd.NaT
    frame["st_risk_active"] = False
    frame["call_status"] = ""
    frame["credit_risk_state"] = ""
    frame["tradability_state"] = ""
    frame["risk_exclusion_reasons"] = ""

    call_notice = risk_inputs.get("call_notice", pd.DataFrame())
    call_codes = active_codes_by_date(call_notice, "bond_code", "notice_date", signal_date)
    if call_codes:
        mask = frame["bond_code"].isin(call_codes)
        frame.loc[mask, "call_status"] = "call_notice_active"
        if "bond_code" in call_notice.columns and "notice_date" in call_notice.columns:
            notice_dates = call_notice.copy()
            notice_dates["bond_code"] = notice_dates["bond_code"].map(normalize_security_code)
            notice_dates["notice_date"] = pd.to_datetime(notice_dates["notice_date"], errors="coerce")
            first_dates = notice_dates[notice_dates["bond_code"].isin(call_codes)].groupby("bond_code")["notice_date"].min()
            frame.loc[mask, "call_notice_date"] = frame.loc[mask, "bond_code"].map(first_dates)

    tail_risk = risk_inputs.get("tail_risk", pd.DataFrame())
    tail_codes = active_codes_by_date(tail_risk, "bond_code", "risk_date", signal_date)
    if tail_codes:
        mask = frame["bond_code"].isin(tail_codes)
        frame.loc[mask, "credit_risk_state"] = "tail_risk_active"
        risk_dates = tail_risk.copy()
        risk_dates["bond_code"] = risk_dates["bond_code"].map(normalize_security_code)
        risk_dates["risk_date"] = pd.to_datetime(risk_dates["risk_date"], errors="coerce")
        first_dates = risk_dates[risk_dates["bond_code"].isin(tail_codes)].groupby("bond_code")["risk_date"].min()
        frame.loc[mask, "tail_risk_date"] = frame.loc[mask, "bond_code"].map(first_dates)

    tradability = risk_inputs.get("tradability", pd.DataFrame())
    if not tradability.empty and "bond_code" in tradability.columns:
        trade = tradability.copy()
        trade["bond_code"] = trade["bond_code"].map(normalize_security_code)
        active = trade
        if "st_risk_active" in active.columns:
            active = active[active["st_risk_active"].fillna(False).astype(bool)]
        st_codes = set(active["bond_code"].dropna())
        if st_codes:
            mask = frame["bond_code"].isin(st_codes)
            frame.loc[mask, "st_risk_active"] = True
            frame.loc[mask, "tradability_state"] = "st_risk_active"

    def reasons(row: pd.Series) -> str:
        items = []
        if row["call_status"]:
            items.append(row["call_status"])
        if row["credit_risk_state"]:
            items.append(row["credit_risk_state"])
        if row["tradability_state"]:
            items.append(row["tradability_state"])
        return "|".join(items)

    frame["risk_exclusion_reasons"] = frame.apply(reasons, axis=1)
    return frame


def normalize_security_code(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def stock_symbol_with_exchange(stock_code: object) -> str:
    code = normalize_security_code(stock_code)
    prefix = "sh" if code.startswith("6") else "sz"
    return f"{prefix}{code}"


def fetch_stock_close_with_fallback(stock_code: object, signal_date: pd.Timestamp, ak_module: object) -> dict:
    code = normalize_security_code(stock_code)
    signal = pd.Timestamp(signal_date).normalize()
    start_end = signal.strftime("%Y%m%d")
    errors: list[str] = []
    attempts = [
        (
            "stock_zh_a_hist",
            lambda: ak_module.stock_zh_a_hist(
                symbol=code, period="daily", start_date=start_end, end_date=start_end, adjust=""
            ),
        ),
        (
            "stock_zh_a_daily",
            lambda: ak_module.stock_zh_a_daily(
                symbol=stock_symbol_with_exchange(code), start_date=start_end, end_date=start_end, adjust=""
            ),
        ),
    ]
    for source, loader in attempts:
        try:
            frame = loader()
            if frame.empty:
                raise ValueError("empty stock close frame")
            date_column = "日期" if "日期" in frame.columns else "date"
            close_column = "收盘" if "收盘" in frame.columns else "close"
            rows = frame.copy()
            rows[date_column] = pd.to_datetime(rows[date_column], errors="coerce")
            matched = rows[rows[date_column].dt.normalize().eq(signal)]
            if matched.empty:
                raise ValueError(f"no row for signal date {signal.date()}")
            close = pd.to_numeric(matched.iloc[-1][close_column], errors="coerce")
            if pd.isna(close):
                raise ValueError("missing close")
            return {"stock_code": code, "close": float(close), "probe_source": source}
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors))


def fetch_stock_closes_with_fallback(
    stock_codes: pd.Series | list,
    signal_date: pd.Timestamp,
    ak_module: object,
) -> pd.DataFrame:
    rows = [fetch_stock_close_with_fallback(code, signal_date, ak_module) for code in sorted(set(stock_codes))]
    return pd.DataFrame(rows)


def default_free_source_providers(
    signal_date: pd.Timestamp,
    security_master: pd.DataFrame,
    ak_module: object | None = None,
) -> dict[str, Callable[[], pd.DataFrame]]:
    if ak_module is None:
        import akshare as ak_module

    master = security_master.copy()
    master["stock_code"] = master["stock_code"].map(normalize_security_code)
    return {
        "cb_spot": ak_module.bond_zh_hs_cov_spot,
        "security_master": lambda: master.copy(),
        "stock_close": lambda: fetch_stock_closes_with_fallback(master["stock_code"], signal_date, ak_module),
    }


def build_snapshot_from_free_sources(
    cb_spot: pd.DataFrame,
    master: pd.DataFrame,
    stock_close: pd.DataFrame,
    signal_date: pd.Timestamp,
) -> pd.DataFrame:
    bonds = cb_spot.copy()
    bonds["bond_code"] = bonds["symbol"].map(normalize_security_code)
    bonds["close_full_price"] = pd.to_numeric(bonds["trade"], errors="coerce")
    bonds = bonds.rename(columns={"name": "bond_name"})

    security_master = master.copy()
    security_master["bond_code"] = security_master["bond_code"].map(normalize_security_code)
    security_master["stock_code"] = security_master["stock_code"].map(normalize_security_code)
    security_master["convert_price"] = pd.to_numeric(security_master["convert_price"], errors="coerce")

    stocks = stock_close.copy()
    stocks["stock_code"] = stocks["stock_code"].map(normalize_security_code)
    stocks["stock_close"] = pd.to_numeric(stocks["close"], errors="coerce")

    snapshot = bonds[["bond_code", "bond_name", "close_full_price"]].merge(
        security_master, on="bond_code", how="left"
    ).merge(stocks[["stock_code", "stock_close"]], on="stock_code", how="left")
    snapshot["signal_date"] = str(pd.Timestamp(signal_date).normalize().date())
    snapshot["conversion_value"] = 100 * snapshot["stock_close"] / snapshot["convert_price"]
    snapshot["conversion_premium_pct"] = (
        snapshot["close_full_price"] / snapshot["conversion_value"] - 1
    ) * 100
    snapshot["double_low"] = snapshot["close_full_price"] + snapshot["conversion_premium_pct"]

    for column, default in {
        "call_notice_date": pd.NaT,
        "tail_risk_date": pd.NaT,
        "st_risk_active": False,
    }.items():
        if column not in snapshot.columns:
            snapshot[column] = default
    return snapshot


def normalize_snapshot_for_engine(snapshot: pd.DataFrame, signal_date: pd.Timestamp) -> pd.DataFrame:
    frame = snapshot.copy()
    frame["bond_code"] = frame["bond_code"].astype(str).str.zfill(6)
    frame["date"] = pd.Timestamp(signal_date).normalize()
    for live_column, engine_column in LIVE_TO_ENGINE_COLUMNS.items():
        if engine_column not in frame.columns and live_column in frame.columns:
            frame[engine_column] = frame[live_column]
    for column in SECURITY_MASTER_ENHANCEMENT_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    for column in ("call_notice_date", "tail_risk_date", "contract_maturity"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    if "st_risk_active" in frame.columns:
        frame["st_risk_active"] = frame["st_risk_active"].fillna(False).astype(bool)
    return frame


def safety_eligible(snapshot: pd.DataFrame, signal_date: pd.Timestamp) -> pd.Series:
    required = ["close_em", "conv_premium", "double_low", "contract_maturity"]
    frame = normalize_snapshot_for_engine(snapshot, signal_date)
    ok = frame[required].notna().all(axis=1)
    ok &= frame["contract_maturity"] - pd.Timestamp(signal_date).normalize() >= pd.Timedelta(days=365)
    ok &= frame["call_notice_date"].isna() | frame["call_notice_date"].gt(signal_date)
    ok &= frame["tail_risk_date"].isna() | frame["tail_risk_date"].gt(signal_date)
    ok &= ~frame["st_risk_active"].fillna(False)
    return ok


def exclusion_reasons(row: pd.Series, signal_date: pd.Timestamp) -> str:
    reasons: list[str] = []
    column_labels = {
        "close_em": "close_full_price",
        "conv_premium": "conversion_premium_pct",
        "double_low": "double_low",
        "contract_maturity": "contract_maturity",
    }
    for engine_column, public_label in column_labels.items():
        if pd.isna(row.get(engine_column)):
            reasons.append(f"missing_{public_label}")
    if (
        pd.notna(row.get("contract_maturity"))
        and row["contract_maturity"] - pd.Timestamp(signal_date).normalize() < pd.Timedelta(days=365)
    ):
        reasons.append("remaining_less_than_365d")
    if pd.notna(row.get("call_notice_date")) and row["call_notice_date"] <= signal_date:
        reasons.append("call_notice_active")
    if pd.notna(row.get("tail_risk_date")) and row["tail_risk_date"] <= signal_date:
        reasons.append("tail_risk_active")
    if bool(row.get("st_risk_active", False)):
        reasons.append("st_risk_active")
    return "|".join(reasons)


def ranked_variant(snapshot: pd.DataFrame, signal_date: pd.Timestamp, variant: str) -> pd.DataFrame:
    engine_snapshot = normalize_snapshot_for_engine(snapshot, signal_date)
    universe = eligible(engine_snapshot, pd.Timestamp(signal_date).normalize(), variant)
    if universe.empty:
        return pd.DataFrame(columns=["bond_code", f"{variant.lower()}_score", f"{variant.lower()}_rank"])
    ranked = universe.assign(_score=score(universe, variant)).sort_values(
        ["_score", "bond_code"], ascending=[True, True]
    )
    return pd.DataFrame({
        "bond_code": ranked["bond_code"].astype(str).str.zfill(6),
        f"{variant.lower()}_score": ranked["_score"].to_numpy(),
        f"{variant.lower()}_rank": range(1, len(ranked) + 1),
    })


def build_universe_from_snapshot(snapshot: pd.DataFrame, signal_date: pd.Timestamp) -> pd.DataFrame:
    if snapshot.empty:
        return build_universe_template()
    signal_date = pd.Timestamp(signal_date).normalize()
    engine_snapshot = normalize_snapshot_for_engine(snapshot, signal_date)
    output = pd.DataFrame({
        "bond_code": engine_snapshot["bond_code"].astype(str).str.zfill(6),
        "bond_name": engine_snapshot.get("bond_name", ""),
        "stock_code": engine_snapshot.get("stock_code", ""),
        "signal_date": str(signal_date.date()),
        "close_full_price": engine_snapshot["close_em"],
        "conversion_premium_pct": engine_snapshot["conv_premium"],
        "double_low": engine_snapshot["double_low"],
        "double_low_z252": engine_snapshot.get("double_low_z252", pd.NA),
        "balance_bil": engine_snapshot.get("balance_bil", pd.NA),
        "contract_maturity": engine_snapshot["contract_maturity"],
        "remaining_days": (engine_snapshot["contract_maturity"] - signal_date).dt.days,
        "call_status": engine_snapshot["call_notice_date"].le(signal_date).map({True: "call_notice_active", False: ""}),
        "credit_risk_state": engine_snapshot["tail_risk_date"].le(signal_date).map({True: "tail_risk_active", False: ""}),
        "tradability_state": engine_snapshot["st_risk_active"].map({True: "st_risk_active", False: ""}),
        "eligible_common": safety_eligible(engine_snapshot, signal_date).to_numpy(),
    })
    output["exclusion_reasons"] = engine_snapshot.apply(exclusion_reasons, axis=1, signal_date=signal_date)

    for variant in VARIANTS:
        output = output.merge(ranked_variant(engine_snapshot, signal_date, variant), on="bond_code", how="left")

    def selected_flags(row: pd.Series) -> str:
        flags = []
        for variant in VARIANTS:
            rank = row.get(f"{variant.lower()}_rank")
            if pd.notna(rank) and int(rank) <= 20:
                flags.append(variant)
        return "|".join(flags)

    output["selected_flags"] = output.apply(selected_flags, axis=1)
    return output[UNIVERSE_COLUMNS].sort_values(["eligible_common", "bond_code"], ascending=[False, True])


def apply_probe_results(source_checks: list[dict], probe_results: dict[str, dict]) -> list[dict]:
    updated = []
    for check in source_checks:
        merged = dict(check)
        result = probe_results.get(str(check["source_id"]))
        if result:
            merged.update(result)
        updated.append(merged)
    return updated


def build_snapshot_manifest(
    signal_date: pd.Timestamp,
    generated_at: pd.Timestamp,
    dry_run: bool,
    source_checks: list[dict],
) -> dict:
    signal_date = pd.Timestamp(signal_date).normalize()
    generated_at = pd.Timestamp(generated_at)
    if not dry_run and not is_calendar_signal_date(signal_date):
        raise ValueError(
            f"{signal_date.date()} is not a calendar 10/20/30 signal date; use --dry-run for rehearsal"
        )
    data_status = "dry_run_invalid_for_forward_sample" if dry_run else "pending_collection"
    return {
        "protocol_version": PROTOCOL_VERSION,
        "snapshot_id": f"CB-SHADOW-SNAPSHOT-{signal_date.strftime('%Y%m%d')}",
        "signal_date": str(signal_date.date()),
        "generated_at": generated_at.isoformat(),
        "data_status": data_status,
        "valid_observation_no": 0 if dry_run else None,
        "candidate_ids": list(VARIANTS),
        "source_checks": source_checks,
        "source_summary": {
            "available": sum(1 for item in source_checks if item.get("status") == "available"),
            "failed": sum(1 for item in source_checks if item.get("status") == "failed"),
            "not_run": sum(1 for item in source_checks if item.get("status") == "not_run"),
        },
        "universe_collection": universe_collection_status(source_checks),
        "generates_orders": False,
        "writes_database": False,
        "modifies_v0": False,
        "dry_run_reason": (
            "rehearsal before the valid signal window; validates command entry and source checklist only"
            if dry_run
            else ""
        ),
        "next_required_outputs": [
            "manifest",
            "universe",
            "execution",
            "portfolio",
            "review",
        ],
        "not_validation_scope": [
            "candidate performance",
            "V0 replacement",
            "real trading order generation",
            "parameter tuning",
            "paid data procurement",
        ],
    }


def write_source_checks(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "source_id",
        "purpose",
        "status",
        "blocking_if_unavailable",
        "rows",
        "columns",
        "probe_source",
        "elapsed_seconds",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--generated-at", default=None, help="timestamp with timezone; defaults to now in local pandas")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="write rehearsal manifest that never counts as forward sample")
    parser.add_argument("--probe-sources", action="store_true", help="run lightweight read-only source probes and write status only")
    parser.add_argument("--write-universe-template", action="store_true", help="write empty universe and manual-risk queue schemas")
    args = parser.parse_args()

    signal_date = pd.Timestamp(args.signal_date).normalize()
    generated_at = pd.Timestamp(args.generated_at) if args.generated_at else pd.Timestamp.now(tz="Asia/Shanghai")
    source_checks = default_source_checks()
    if args.probe_sources:
        source_checks = apply_probe_results(source_checks, run_source_probes())
    manifest = build_snapshot_manifest(signal_date, generated_at, args.dry_run, source_checks)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / f"可转债-影子快照入口-{signal_date.strftime('%Y%m%d')}"
    manifest_path = prefix.with_name(prefix.name + "-manifest.json")
    source_path = prefix.with_name(prefix.name + "-source-checks.csv")
    universe_template_path = prefix.with_name(prefix.name + "-universe-template.csv")
    risk_queue_path = prefix.with_name(prefix.name + "-manual-risk-queue.csv")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_source_checks(source_path, source_checks)
    if args.write_universe_template:
        build_universe_template().to_csv(universe_template_path, index=False)
        build_risk_queue_template().to_csv(risk_queue_path, index=False)
    print(json.dumps({
        "manifest": str(manifest_path),
        "source_checks": str(source_path),
        "universe_template": str(universe_template_path) if args.write_universe_template else "",
        "manual_risk_queue": str(risk_queue_path) if args.write_universe_template else "",
        "signal_date": manifest["signal_date"],
        "data_status": manifest["data_status"],
        "universe_collection": manifest["universe_collection"],
        "generates_orders": manifest["generates_orders"],
        "writes_database": manifest["writes_database"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
