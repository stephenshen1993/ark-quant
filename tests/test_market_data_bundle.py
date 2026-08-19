import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd

from datasource.market_data_bundle import (
    DataRequirements,
    prepare_market_data_bundle,
)


class MarketDataBundlePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.cache_root = Path(self.temp_dir.name)
        self.requirements = DataRequirements(
            strategy="stock",
            dataset_fields={
                "merged": ("stock_code", "price", "total_mv_yuan"),
                "ranked": ("stock_code", "roe_pct", "rank"),
            },
            symbol_field="stock_code",
            source="tencent+ths",
            source_version="2026-08",
            algorithm_version="smallcap-input-v1",
            config_fingerprint="config-a",
        )

    @staticmethod
    def _frames() -> dict[str, pd.DataFrame]:
        return {
            "merged": pd.DataFrame([
                {"stock_code": "600001", "price": 8.0, "total_mv_yuan": 100.0},
                {"stock_code": "600002", "price": 9.0, "total_mv_yuan": 200.0},
            ]),
            "ranked": pd.DataFrame([
                {"stock_code": "600001", "roe_pct": 5.0, "rank": 1},
            ]),
        }

    def test_complete_bundle_is_reused_across_store_instances_without_fetching(self) -> None:
        calls = 0

        def fetch() -> dict[str, pd.DataFrame]:
            nonlocal calls
            calls += 1
            return self._frames()

        first = prepare_market_data_bundle(
            self.requirements,
            date(2026, 8, 18),
            fetch,
            cache_root=self.cache_root,
            expected_symbols=("600001", "600002"),
        )
        second = prepare_market_data_bundle(
            self.requirements,
            date(2026, 8, 18),
            lambda: self.fail("完整缓存命中时不应调用外部适配器"),
            cache_root=self.cache_root,
            expected_symbols=("600001", "600002"),
        )

        self.assertEqual(calls, 1)
        self.assertEqual(first.metadata.mode, "cold_build")
        self.assertEqual(second.metadata.mode, "cache_hit")
        self.assertEqual(second.metadata.external_calls, 0)
        self.assertEqual(second.metadata.to_dict()["mode_label"], "完整缓存命中")
        self.assertIn("total", second.metadata.stage_timings_ms)
        self.assertEqual(second.manifest["status"], "complete")
        self.assertEqual(second.manifest["expected_data_date"], "2026-08-18")
        self.assertEqual(second.manifest["actual_data_dates"], ["2026-08-18"])
        self.assertEqual(second.manifest["expected_symbols"], ["600001", "600002"])
        self.assertEqual(second.manifest["actual_symbols"], ["600001", "600002"])

    def test_manifest_without_exact_date_proof_is_rebuilt(self) -> None:
        first = prepare_market_data_bundle(
            self.requirements,
            date(2026, 8, 18),
            self._frames,
            cache_root=self.cache_root,
        )
        manifest_path = Path(first.manifest["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["actual_data_dates"] = ["2026-08-15"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        calls = 0

        def rebuild() -> dict[str, pd.DataFrame]:
            nonlocal calls
            calls += 1
            return self._frames()

        result = prepare_market_data_bundle(
            self.requirements,
            date(2026, 8, 18),
            rebuild,
            cache_root=self.cache_root,
        )

        self.assertEqual(calls, 1)
        self.assertEqual(result.metadata.mode, "cold_build")
        self.assertIn("data_date_mismatch", result.metadata.invalidation_reasons)

    def test_missing_required_field_never_publishes_complete_manifest(self) -> None:
        broken = self._frames()
        broken["ranked"] = broken["ranked"].drop(columns=["roe_pct"])

        with self.assertRaisesRegex(RuntimeError, "ranked.*roe_pct"):
            prepare_market_data_bundle(
                self.requirements,
                date(2026, 8, 18),
                lambda: broken,
                cache_root=self.cache_root,
            )

        self.assertEqual(list(self.cache_root.rglob("manifest.json")), [])

    def test_declared_dataset_coverage_cannot_hide_symbols_lost_by_inner_merge(self) -> None:
        requirements = DataRequirements(
            strategy="stock",
            dataset_fields={
                "universe": ("stock_code",),
                "merged": ("stock_code", "price"),
            },
            symbol_field="stock_code",
            source="tencent",
            source_version="v1",
            algorithm_version="raw-v1",
            expected_symbols_dataset="universe",
            coverage_datasets=("merged",),
        )
        frames = {
            "universe": pd.DataFrame({"stock_code": ["600001", "600002"]}),
            "merged": pd.DataFrame({"stock_code": ["600001"], "price": [8.0]}),
        }

        with self.assertRaisesRegex(RuntimeError, "merged.*600002"):
            prepare_market_data_bundle(
                requirements,
                date(2026, 8, 18),
                lambda: frames,
                cache_root=self.cache_root,
            )

        self.assertEqual(list(self.cache_root.rglob("manifest.json")), [])

    def test_corrupt_frame_is_rejected_and_rebuilt(self) -> None:
        first = prepare_market_data_bundle(
            self.requirements,
            date(2026, 8, 18),
            self._frames,
            cache_root=self.cache_root,
        )
        manifest = first.manifest
        frame_path = Path(manifest["manifest_path"]).parent / manifest["files"]["merged"]["name"]
        frame_path.write_text("broken", encoding="utf-8")
        calls = 0

        def rebuild() -> dict[str, pd.DataFrame]:
            nonlocal calls
            calls += 1
            return self._frames()

        result = prepare_market_data_bundle(
            self.requirements,
            date(2026, 8, 18),
            rebuild,
            cache_root=self.cache_root,
        )

        self.assertEqual(calls, 1)
        self.assertIn("dataset_corrupt", result.metadata.invalidation_reasons)

    def test_concurrent_same_identity_fetches_once_and_publishes_one_complete_bundle(self) -> None:
        calls = 0

        def fetch() -> dict[str, pd.DataFrame]:
            nonlocal calls
            calls += 1
            time.sleep(0.05)
            return self._frames()

        def prepare():
            return prepare_market_data_bundle(
                self.requirements,
                date(2026, 8, 18),
                fetch,
                cache_root=self.cache_root,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: prepare(), range(2)))

        self.assertEqual(calls, 1)
        self.assertEqual(sorted(result.metadata.mode for result in results), ["cache_hit", "cold_build"])
        self.assertEqual(len(list(self.cache_root.rglob("manifest.json"))), 1)
        self.assertEqual(len(list(self.cache_root.rglob("incomplete.json"))), 0)

    def test_failed_build_remains_incomplete_and_next_run_can_publish(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "source down"):
            prepare_market_data_bundle(
                self.requirements,
                date(2026, 8, 18),
                lambda: (_ for _ in ()).throw(RuntimeError("source down")),
                cache_root=self.cache_root,
            )

        self.assertEqual(len(list(self.cache_root.rglob("incomplete.json"))), 1)
        self.assertEqual(len(list(self.cache_root.rglob("manifest.json"))), 0)

        result = prepare_market_data_bundle(
            self.requirements,
            date(2026, 8, 18),
            self._frames,
            cache_root=self.cache_root,
        )

        self.assertEqual(result.manifest["status"], "complete")
        self.assertEqual(len(list(self.cache_root.rglob("incomplete.json"))), 0)


if __name__ == "__main__":
    unittest.main()
