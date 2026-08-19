"""Persistent, validated strategy inputs keyed by an effective trading date."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from time import perf_counter
from typing import Callable, Mapping, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_ROOT = ROOT / "data" / "cache" / "market_data_bundles"


@dataclass(frozen=True)
class DataRequirements:
    strategy: str
    dataset_fields: Mapping[str, tuple[str, ...]]
    symbol_field: str
    source: str
    source_version: str
    algorithm_version: str
    config_fingerprint: str = ""
    lookback_trading_days: int = 1

    @property
    def fingerprint(self) -> str:
        payload = {
            "strategy": self.strategy,
            "dataset_fields": {
                name: list(fields) for name, fields in sorted(self.dataset_fields.items())
            },
            "symbol_field": self.symbol_field,
            "source": self.source,
            "source_version": self.source_version,
            "algorithm_version": self.algorithm_version,
            "config_fingerprint": self.config_fingerprint,
            "lookback_trading_days": self.lookback_trading_days,
        }
        return stable_fingerprint(payload)


@dataclass(frozen=True)
class PreparationMetadata:
    effective_date: str
    mode: str
    reused_records: int = 0
    refreshed_records: int = 0
    external_calls: int = 0
    batch_requests: int = 0
    fallback_symbols: int = 0
    missing_trading_days: tuple[str, ...] = ()
    invalidation_reasons: tuple[str, ...] = ()
    stage_timings_ms: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MarketDataBundle:
    strategy: str
    effective_date: date
    frames: Mapping[str, pd.DataFrame] = field(repr=False)
    manifest: Mapping[str, object]
    metadata: PreparationMetadata
    input_fingerprint: str


def prepare_market_data_bundle(
    requirements: DataRequirements,
    effective_date: date,
    fetcher: Callable[[], Mapping[str, pd.DataFrame]],
    *,
    cache_root: Path | None = None,
    expected_symbols: Sequence[str] = (),
) -> MarketDataBundle:
    """Return an exact-date complete bundle, fetching only when proof is absent."""
    started = perf_counter()
    root = cache_root or DEFAULT_CACHE_ROOT
    bundle_dir = _bundle_dir(root, requirements, effective_date)
    cached, invalidation_reason = _load_complete_bundle(
        bundle_dir,
        requirements,
        effective_date,
        expected_symbols,
    )
    if cached is not None:
        frames, manifest = cached
        elapsed = int((perf_counter() - started) * 1000)
        metadata = PreparationMetadata(
            effective_date=effective_date.isoformat(),
            mode="cache_hit",
            reused_records=sum(len(frame) for frame in frames.values()),
            stage_timings_ms={"total": elapsed},
        )
        return _bundle(requirements, effective_date, frames, manifest, metadata, bundle_dir)

    fetch_started = perf_counter()
    fetched = {name: frame.copy() for name, frame in fetcher().items()}
    fetch_elapsed = int((perf_counter() - fetch_started) * 1000)
    frames = _validate_and_stamp_frames(fetched, requirements, effective_date)
    manifest = _publish_complete_bundle(
        bundle_dir,
        requirements,
        effective_date,
        frames,
        expected_symbols,
    )
    elapsed = int((perf_counter() - started) * 1000)
    metadata = PreparationMetadata(
        effective_date=effective_date.isoformat(),
        mode="cold_build",
        refreshed_records=sum(len(frame) for frame in frames.values()),
        external_calls=1,
        invalidation_reasons=(invalidation_reason,) if invalidation_reason else (),
        stage_timings_ms={"fetch": fetch_elapsed, "total": elapsed},
    )
    return _bundle(requirements, effective_date, frames, manifest, metadata, bundle_dir)


def _bundle(
    requirements: DataRequirements,
    effective_date: date,
    frames: Mapping[str, pd.DataFrame],
    manifest: Mapping[str, object],
    metadata: PreparationMetadata,
    bundle_dir: Path,
) -> MarketDataBundle:
    exposed_manifest = dict(manifest)
    exposed_manifest["manifest_path"] = str(bundle_dir / "manifest.json")
    return MarketDataBundle(
        strategy=requirements.strategy,
        effective_date=effective_date,
        frames=frames,
        manifest=exposed_manifest,
        metadata=metadata,
        input_fingerprint=str(manifest["input_fingerprint"]),
    )


def _bundle_dir(root: Path, requirements: DataRequirements, effective_date: date) -> Path:
    return root / requirements.strategy / effective_date.isoformat() / requirements.fingerprint


def _load_complete_bundle(
    bundle_dir: Path,
    requirements: DataRequirements,
    effective_date: date,
    expected_symbols: Sequence[str],
) -> tuple[dict[str, pd.DataFrame], dict] | tuple[None, str | None]:
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        return None, None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "manifest_corrupt"
    if manifest.get("status") != "complete":
        return None, "manifest_incomplete"
    if manifest.get("requirements_fingerprint") != requirements.fingerprint:
        return None, "requirements_mismatch"
    expected_date = effective_date.isoformat()
    if manifest.get("expected_data_date") != expected_date:
        return None, "data_date_mismatch"
    if manifest.get("actual_data_dates") != [expected_date]:
        return None, "data_date_mismatch"
    normalized_expected = sorted({str(symbol) for symbol in expected_symbols})
    if normalized_expected and manifest.get("expected_symbols") != normalized_expected:
        return None, "symbol_coverage_mismatch"

    frames: dict[str, pd.DataFrame] = {}
    files = manifest.get("files") or {}
    for name, required_fields in requirements.dataset_fields.items():
        file_meta = files.get(name)
        if not isinstance(file_meta, dict):
            return None, "dataset_missing"
        path = bundle_dir / str(file_meta.get("name", ""))
        if not path.is_file() or _file_sha256(path) != file_meta.get("sha256"):
            return None, "dataset_corrupt"
        try:
            frame = pd.read_json(path, orient="table")
        except (OSError, ValueError):
            return None, "dataset_corrupt"
        missing = [field for field in required_fields if field not in frame.columns]
        if missing:
            return None, "field_coverage_mismatch"
        dates = sorted(set(frame["_effective_date"].astype(str))) if not frame.empty else []
        if dates and dates != [expected_date]:
            return None, "data_date_mismatch"
        frames[name] = frame
    actual_symbols = _symbols(frames, requirements.symbol_field)
    if manifest.get("actual_symbols") != actual_symbols:
        return None, "symbol_coverage_mismatch"
    if normalized_expected and not set(normalized_expected).issubset(actual_symbols):
        return None, "symbol_coverage_mismatch"
    return (frames, manifest), None


def _validate_and_stamp_frames(
    frames: Mapping[str, pd.DataFrame],
    requirements: DataRequirements,
    effective_date: date,
) -> dict[str, pd.DataFrame]:
    stamped: dict[str, pd.DataFrame] = {}
    for name, required_fields in requirements.dataset_fields.items():
        frame = frames.get(name)
        if not isinstance(frame, pd.DataFrame):
            raise RuntimeError(f"策略输入缺少数据集 {name}")
        missing = [field for field in required_fields if field not in frame.columns]
        if missing:
            raise RuntimeError(f"策略输入 {name} 缺少关键字段: {', '.join(missing)}")
        copy = frame.copy()
        copy["_effective_date"] = effective_date.isoformat()
        stamped[name] = copy
    return stamped


def _publish_complete_bundle(
    bundle_dir: Path,
    requirements: DataRequirements,
    effective_date: date,
    frames: Mapping[str, pd.DataFrame],
    expected_symbols: Sequence[str],
) -> dict:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    for name, frame in frames.items():
        path = bundle_dir / f"{name}.json"
        temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
        frame.to_json(temporary, orient="table", date_format="iso", force_ascii=False)
        os.replace(temporary, path)
        files[name] = {
            "name": path.name,
            "sha256": _file_sha256(path),
            "records": len(frame),
            "fields": list(frame.columns),
        }

    actual_symbols = _symbols(frames, requirements.symbol_field)
    normalized_expected = sorted({str(symbol) for symbol in expected_symbols}) or actual_symbols
    if not set(normalized_expected).issubset(actual_symbols):
        missing = sorted(set(normalized_expected) - set(actual_symbols))
        raise RuntimeError(f"策略输入缺少证券覆盖: {', '.join(missing)}")
    input_fingerprint = stable_fingerprint(
        {name: meta["sha256"] for name, meta in sorted(files.items())}
    )
    manifest = {
        "schema_version": 1,
        "status": "complete",
        "strategy": requirements.strategy,
        "expected_data_date": effective_date.isoformat(),
        "actual_data_dates": [effective_date.isoformat()],
        "expected_symbols": normalized_expected,
        "actual_symbols": actual_symbols,
        "required_fields": {
            name: list(fields) for name, fields in sorted(requirements.dataset_fields.items())
        },
        "source": requirements.source,
        "source_version": requirements.source_version,
        "algorithm_version": requirements.algorithm_version,
        "config_fingerprint": requirements.config_fingerprint,
        "requirements_fingerprint": requirements.fingerprint,
        "input_fingerprint": input_fingerprint,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "files": files,
    }
    manifest_path = bundle_dir / "manifest.json"
    temporary = manifest_path.with_suffix(f"{manifest_path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, manifest_path)
    return manifest


def _symbols(frames: Mapping[str, pd.DataFrame], symbol_field: str) -> list[str]:
    symbols: set[str] = set()
    for frame in frames.values():
        if symbol_field in frame.columns:
            symbols.update(str(value) for value in frame[symbol_field].dropna())
    return sorted(symbols)


def stable_fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
