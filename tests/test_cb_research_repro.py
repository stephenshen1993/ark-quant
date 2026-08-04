from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "scripts/research/cb_strategy_repro/engine.py"
SPEC = importlib.util.spec_from_file_location("cb_repro_engine", ENGINE_PATH)
ENGINE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ENGINE
SPEC.loader.exec_module(ENGINE)

DRY_RUN_PATH = ROOT / "scripts/research/cb_strategy_repro/historical_shadow_dry_run.py"
DRY_RUN_SPEC = importlib.util.spec_from_file_location("cb_shadow_dry_run", DRY_RUN_PATH)
DRY_RUN = importlib.util.module_from_spec(DRY_RUN_SPEC)
sys.modules[DRY_RUN_SPEC.name] = DRY_RUN
sys.path.insert(0, str(DRY_RUN_PATH.parent))
DRY_RUN_SPEC.loader.exec_module(DRY_RUN)

SNAPSHOT_PATH = ROOT / "scripts/research/cb_strategy_repro/shadow_snapshot.py"
SNAPSHOT_SPEC = importlib.util.spec_from_file_location("cb_shadow_snapshot", SNAPSHOT_PATH)
SNAPSHOT = importlib.util.module_from_spec(SNAPSHOT_SPEC)
sys.modules[SNAPSHOT_SPEC.name] = SNAPSHOT
SNAPSHOT_SPEC.loader.exec_module(SNAPSHOT)


class StatefulExecutionContractTest(unittest.TestCase):
    def make_panel(self, missing_second_open: bool) -> pd.DataFrame:
        dates = pd.to_datetime(["2026-01-12", "2026-01-13"])
        rows = []
        for code in ("110001", "110002"):
            for date in dates:
                rows.append({
                    "bond_code": code, "date": date,
                    "open_sina": np.nan if missing_second_open and code == "110002" and date == dates[0] else 100.0,
                    "close_em": 100.0,
                })
        return pd.DataFrame(rows)

    def run_engine(self, missing_second_open: bool):
        rankings = {
            pd.Timestamp("2026-01-12"): {
                "signal_date": pd.Timestamp("2026-01-09"),
                "codes": ["110001", "110002"],
            }
        }
        empty_risks = pd.DataFrame(columns=["bond_code", "risk_date", "risk_end", "risk_type"])
        empty_exits = pd.DataFrame(columns=["bond_code", "holder_cash_date", "execute_price"])
        empty_coupons = pd.DataFrame(columns=["bond_code", "payment_date", "coupon_per_100_pretax"])
        return ENGINE.simulate_ranked_portfolio(
            self.make_panel(missing_second_open), rankings, empty_risks, empty_exits,
            empty_coupons, ENGINE.EngineConfig(target=2, fee=0.001, include_coupons=False),
        )

    def test_missing_target_slot_remains_cash(self):
        daily, trades, summary = self.run_engine(True)
        first = daily.iloc[0]
        self.assertEqual(summary["missed_target_buys"], 1)
        self.assertEqual(int(first["holdings"]), 1)
        self.assertGreater(first["cash_weight"], 0.49)
        bought = trades.loc[trades["side"].eq("buy"), "notional"].sum()
        self.assertLess(bought, 0.51)

    def test_initial_fee_is_not_normalized_away(self):
        daily, _, summary = self.run_engine(False)
        self.assertLess(float(daily.iloc[0]["nav"]), 1.0)
        self.assertLess(summary["ending_nav"], 1.0)
        self.assertGreater(summary["fees_paid"], 0.0)

    def test_standard_cash_exit_branch(self):
        panel = self.make_panel(False)
        panel.loc[(panel["bond_code"] == "110001") & (panel["date"] == pd.Timestamp("2026-01-13")),
                  ["open_sina", "close_em"]] = np.nan
        rankings = {pd.Timestamp("2026-01-12"): {
            "signal_date": pd.Timestamp("2026-01-09"), "codes": ["110001", "110002"]}}
        risks = pd.DataFrame(columns=["bond_code", "risk_date", "risk_end", "risk_type"])
        exits = pd.DataFrame([{"bond_code": "110001", "holder_cash_date": pd.Timestamp("2026-01-13"),
                               "execute_price": 105.0}])
        coupons = pd.DataFrame(columns=["bond_code", "payment_date", "coupon_per_100_pretax"])
        _, trades, summary = ENGINE.simulate_ranked_portfolio(
            panel, rankings, risks, exits, coupons,
            ENGINE.EngineConfig(target=2, fee=0.001, include_coupons=False),
        )
        self.assertEqual(summary["standard_cash_exits"], 1)
        self.assertTrue((trades["reason"] == "standard_cash_exit").any())

    def test_st_risk_can_end_without_permanent_blacklist(self):
        panel = self.make_panel(False)
        rankings = {pd.Timestamp("2026-01-12"): {
            "signal_date": pd.Timestamp("2026-01-09"), "codes": ["110001", "110002"]}}
        risks = pd.DataFrame([{"bond_code": "110001", "risk_date": pd.Timestamp("2025-01-01"),
                               "risk_end": pd.Timestamp("2025-06-01"), "risk_type": "st"}])
        exits = pd.DataFrame(columns=["bond_code", "holder_cash_date", "execute_price"])
        coupons = pd.DataFrame(columns=["bond_code", "payment_date", "coupon_per_100_pretax"])
        daily, _, summary = ENGINE.simulate_ranked_portfolio(
            panel, rankings, risks, exits, coupons,
            ENGINE.EngineConfig(target=2, fee=0.001, include_coupons=False),
        )
        self.assertEqual(int(daily.iloc[0]["holdings"]), 2)
        self.assertEqual(summary["risk_exits"], 0)


class HistoricalShadowDryRunContractTest(unittest.TestCase):
    def test_portfolio_review_is_marked_as_dry_run_not_forward_sample(self):
        signal_date = pd.Timestamp("2026-01-09")
        execution_date = pd.Timestamp("2026-01-12")
        execution = pd.DataFrame([
            {
                "candidate_id": "B0_BASE",
                "signal_date": signal_date.date().isoformat(),
                "execution_date": execution_date.date().isoformat(),
                "bond_code": "110001",
                "target_weight": 0.05,
                "open_price_observed": 100.0,
                "action": "buy",
                "one_way_cost": 0.001,
                "execution_note": "",
            },
            {
                "candidate_id": "B0_BASE",
                "signal_date": signal_date.date().isoformat(),
                "execution_date": execution_date.date().isoformat(),
                "bond_code": "110002",
                "target_weight": 0.05,
                "open_price_observed": "",
                "action": "unfilled_cash",
                "one_way_cost": 0.0,
                "execution_note": "missing_or_nonpositive_open_price",
            },
        ])

        portfolio, review = DRY_RUN.build_portfolio_review(execution, signal_date, execution_date)

        self.assertEqual(list(portfolio["candidate_id"].unique()), ["B0_BASE"])
        self.assertEqual(float(portfolio.loc[0, "cash_weight"]), 0.95)
        self.assertEqual(int(portfolio.loc[0, "holdings_count"]), 1)
        self.assertEqual(review.loc[0, "data_status"], "dry_run_invalid_for_forward_sample")
        self.assertEqual(int(review.loc[0, "valid_observation_no"]), 0)
        self.assertFalse(bool(review.loc[0, "parameter_change"]))


class LiveShadowSnapshotGuardTest(unittest.TestCase):
    def test_non_signal_date_requires_dry_run(self):
        with self.assertRaises(ValueError):
            SNAPSHOT.build_snapshot_manifest(
                signal_date=pd.Timestamp("2026-07-24"),
                generated_at=pd.Timestamp("2026-07-24 21:00:00+0800"),
                dry_run=False,
                source_checks=[],
            )

    def test_dry_run_manifest_cannot_count_as_forward_observation(self):
        manifest = SNAPSHOT.build_snapshot_manifest(
            signal_date=pd.Timestamp("2026-07-24"),
            generated_at=pd.Timestamp("2026-07-24 21:00:00+0800"),
            dry_run=True,
            source_checks=[{"source_id": "akshare_sina_cb_spot", "status": "not_run"}],
        )

        self.assertEqual(manifest["data_status"], "dry_run_invalid_for_forward_sample")
        self.assertEqual(manifest["valid_observation_no"], 0)
        self.assertEqual(manifest["candidate_ids"], list(ENGINE.VARIANTS))
        self.assertFalse(manifest["generates_orders"])

    def test_source_probe_records_status_without_raw_market_rows(self):
        probes = {
            "ok_source": lambda: pd.DataFrame({"code": ["110001"], "price": [100.0]}),
            "bad_source": lambda: (_ for _ in ()).throw(RuntimeError("network failed")),
        }

        results = SNAPSHOT.run_source_probes(probes)

        self.assertEqual(results["ok_source"]["status"], "available")
        self.assertEqual(results["ok_source"]["rows"], 1)
        self.assertEqual(results["ok_source"]["columns"], "code|price")
        self.assertNotIn("110001", str(results["ok_source"]))
        self.assertEqual(results["bad_source"]["status"], "failed")
        self.assertIn("network failed", results["bad_source"]["error"])

    def test_first_successful_probe_uses_backup_source(self):
        probe = SNAPSHOT.first_successful_probe([
            ("primary", lambda: (_ for _ in ()).throw(RuntimeError("primary failed"))),
            ("backup", lambda: pd.DataFrame({"date": ["2026-07-24"], "close": [10.0]})),
        ])

        result = probe()

        self.assertEqual(result.attrs["probe_source"], "backup")
        self.assertEqual(list(result.columns), ["date", "close"])

    def test_universe_status_blocks_when_required_source_failed(self):
        source_checks = [
            {"source_id": "required", "status": "failed", "blocking_if_unavailable": True},
            {"source_id": "optional", "status": "failed", "blocking_if_unavailable": False},
        ]

        status = SNAPSHOT.universe_collection_status(source_checks)

        self.assertEqual(status["status"], "blocked_by_source_checks")
        self.assertEqual(status["blocking_sources"], ["required"])

    def test_universe_template_has_required_columns_and_no_rows(self):
        universe = SNAPSHOT.build_universe_template()
        risk_queue = SNAPSHOT.build_risk_queue_template()

        self.assertEqual(len(universe), 0)
        self.assertIn("bond_code", universe.columns)
        self.assertIn("selected_flags", universe.columns)
        self.assertIn("manual_check_reason", risk_queue.columns)

    def test_universe_builder_preserves_three_candidate_rankings(self):
        signal_date = pd.Timestamp("2026-07-30")
        snapshot = pd.DataFrame([
            {
                "bond_code": "123001", "bond_name": "甲转债", "stock_code": "300001",
                "close_full_price": 110.0, "conversion_premium_pct": 20.0,
                "double_low": 130.0, "double_low_z252": -0.5, "balance_bil": 1.0,
                "contract_maturity": pd.Timestamp("2030-01-01"),
                "call_notice_date": pd.NaT, "tail_risk_date": pd.NaT, "st_risk_active": False,
            },
            {
                "bond_code": "123002", "bond_name": "乙转债", "stock_code": "300002",
                "close_full_price": 100.0, "conversion_premium_pct": 10.0,
                "double_low": 110.0, "double_low_z252": 0.2, "balance_bil": 3.0,
                "contract_maturity": pd.Timestamp("2030-01-01"),
                "call_notice_date": pd.NaT, "tail_risk_date": pd.NaT, "st_risk_active": False,
            },
            {
                "bond_code": "123003", "bond_name": "丙转债", "stock_code": "300003",
                "close_full_price": np.nan, "conversion_premium_pct": 15.0,
                "double_low": 115.0, "double_low_z252": -1.0, "balance_bil": 0.5,
                "contract_maturity": pd.Timestamp("2030-01-01"),
                "call_notice_date": pd.NaT, "tail_risk_date": pd.NaT, "st_risk_active": False,
            },
        ])

        universe = SNAPSHOT.build_universe_from_snapshot(snapshot, signal_date)

        self.assertEqual(list(universe.columns), SNAPSHOT.UNIVERSE_COLUMNS)
        first = universe[universe["bond_code"].eq("123002")].iloc[0]
        self.assertTrue(bool(first["eligible_common"]))
        self.assertEqual(int(first["b0_base_rank"]), 1)
        self.assertIn("B0_BASE", first["selected_flags"])
        excluded = universe[universe["bond_code"].eq("123003")].iloc[0]
        self.assertFalse(bool(excluded["eligible_common"]))
        self.assertIn("missing_close_full_price", excluded["exclusion_reasons"])

    def test_free_source_snapshot_computes_premium_and_double_low(self):
        signal_date = pd.Timestamp("2026-07-30")
        cb_spot = pd.DataFrame([
            {"symbol": "sz123001", "name": "甲转债", "trade": "120.00"},
            {"symbol": "sh113002", "name": "乙转债", "trade": "105.00"},
        ])
        master = pd.DataFrame([
            {
                "bond_code": "123001", "stock_code": "300001", "convert_price": 10.0,
                "contract_maturity": pd.Timestamp("2030-01-01"),
                "double_low_z252": -0.5, "balance_bil": 1.0,
            },
            {
                "bond_code": "113002", "stock_code": "600002", "convert_price": 20.0,
                "contract_maturity": pd.Timestamp("2030-01-01"),
                "double_low_z252": 0.2, "balance_bil": 3.0,
            },
        ])
        stock_close = pd.DataFrame([
            {"stock_code": "300001", "close": 12.0},
            {"stock_code": "600002", "close": 21.0},
        ])

        snapshot = SNAPSHOT.build_snapshot_from_free_sources(cb_spot, master, stock_close, signal_date)

        row = snapshot[snapshot["bond_code"].eq("123001")].iloc[0]
        self.assertEqual(row["signal_date"], "2026-07-30")
        self.assertAlmostEqual(float(row["conversion_value"]), 120.0)
        self.assertAlmostEqual(float(row["conversion_premium_pct"]), 0.0)
        self.assertAlmostEqual(float(row["double_low"]), 120.0)
        self.assertEqual(row["bond_name"], "甲转债")

    def test_free_source_snapshot_feeds_universe_contract(self):
        signal_date = pd.Timestamp("2026-07-30")
        cb_spot = pd.DataFrame([
            {"symbol": "sz123001", "name": "甲转债", "trade": "120.00"},
            {"symbol": "sh113002", "name": "乙转债", "trade": "105.00"},
        ])
        master = pd.DataFrame([
            {
                "bond_code": "123001", "stock_code": "300001", "convert_price": 10.0,
                "contract_maturity": pd.Timestamp("2030-01-01"),
                "double_low_z252": -0.5, "balance_bil": 1.0,
            },
            {
                "bond_code": "113002", "stock_code": "600002", "convert_price": 20.0,
                "contract_maturity": pd.Timestamp("2030-01-01"),
                "double_low_z252": 0.2, "balance_bil": 3.0,
            },
        ])
        stock_close = pd.DataFrame([
            {"stock_code": "300001", "close": 12.0},
            {"stock_code": "600002", "close": 21.0},
        ])

        snapshot = SNAPSHOT.build_snapshot_from_free_sources(cb_spot, master, stock_close, signal_date)
        universe = SNAPSHOT.build_universe_from_snapshot(snapshot, signal_date)

        self.assertEqual(list(universe.columns), SNAPSHOT.UNIVERSE_COLUMNS)
        self.assertEqual(len(universe), 2)
        self.assertTrue(universe["eligible_common"].all())
        self.assertEqual(int(universe.loc[universe["bond_code"].eq("123001"), "c_bal_rank"].iloc[0]), 1)

    def test_manual_risk_queue_lists_excluded_or_uncertain_bonds(self):
        universe = pd.DataFrame([
            {
                "signal_date": "2026-07-30", "bond_code": "123001", "bond_name": "甲转债",
                "stock_code": "300001", "eligible_common": True, "exclusion_reasons": "",
            },
            {
                "signal_date": "2026-07-30", "bond_code": "123002", "bond_name": "乙转债",
                "stock_code": "300002", "eligible_common": False,
                "exclusion_reasons": "call_notice_active|st_risk_active",
            },
        ])

        queue = SNAPSHOT.build_manual_risk_queue_from_universe(universe)

        self.assertEqual(list(queue.columns), SNAPSHOT.RISK_QUEUE_COLUMNS)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue.loc[0, "bond_code"], "123002")
        self.assertEqual(queue.loc[0, "manual_check_reason"], "call_notice_active|st_risk_active")
        self.assertEqual(queue.loc[0, "status"], "pending")

    def test_collect_free_source_inputs_records_audit_without_raw_rows(self):
        providers = {
            "cb_spot": lambda: pd.DataFrame({"symbol": ["sz123001"], "trade": [120.0]}),
            "security_master": lambda: pd.DataFrame({"bond_code": ["123001"], "stock_code": ["300001"]}),
            "stock_close": lambda: pd.DataFrame({"stock_code": ["300001"], "close": [12.0]}),
        }

        bundle = SNAPSHOT.collect_free_source_inputs(providers)

        self.assertEqual(set(bundle["frames"]), {"cb_spot", "security_master", "stock_close"})
        self.assertEqual(bundle["audit"].loc[bundle["audit"]["source_id"].eq("cb_spot"), "status"].iloc[0], "available")
        self.assertEqual(int(bundle["audit"].loc[bundle["audit"]["source_id"].eq("cb_spot"), "rows"].iloc[0]), 1)
        self.assertNotIn("123001", bundle["audit"].to_csv(index=False))

    def test_collect_free_source_inputs_marks_required_failure(self):
        providers = {
            "cb_spot": lambda: (_ for _ in ()).throw(RuntimeError("source down")),
            "security_master": lambda: pd.DataFrame({"bond_code": ["123001"], "stock_code": ["300001"]}),
            "stock_close": lambda: pd.DataFrame({"stock_code": ["300001"], "close": [12.0]}),
        }

        bundle = SNAPSHOT.collect_free_source_inputs(providers)

        self.assertFalse(bundle["ready"])
        failed = bundle["audit"].loc[bundle["audit"]["source_id"].eq("cb_spot")].iloc[0]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("source down", failed["error"])

    def test_build_shadow_universe_bundle_stops_when_required_source_failed(self):
        providers = {
            "cb_spot": lambda: (_ for _ in ()).throw(RuntimeError("source down")),
            "security_master": lambda: pd.DataFrame({"bond_code": ["123001"], "stock_code": ["300001"]}),
            "stock_close": lambda: pd.DataFrame({"stock_code": ["300001"], "close": [12.0]}),
        }

        bundle = SNAPSHOT.build_shadow_universe_bundle(providers, pd.Timestamp("2026-07-30"))

        self.assertFalse(bundle["ready"])
        self.assertEqual(len(bundle["universe"]), 0)
        self.assertEqual(len(bundle["manual_risk_queue"]), 0)

    def test_build_shadow_universe_bundle_uses_same_universe_contract(self):
        providers = {
            "cb_spot": lambda: pd.DataFrame([
                {"symbol": "sz123001", "name": "甲转债", "trade": "120.00"},
            ]),
            "security_master": lambda: pd.DataFrame([
                {
                    "bond_code": "123001", "stock_code": "300001", "convert_price": 10.0,
                    "contract_maturity": pd.Timestamp("2030-01-01"),
                    "double_low_z252": -0.5, "balance_bil": 1.0,
                },
            ]),
            "stock_close": lambda: pd.DataFrame([
                {"stock_code": "300001", "close": 12.0},
            ]),
        }

        bundle = SNAPSHOT.build_shadow_universe_bundle(providers, pd.Timestamp("2026-07-30"))

        self.assertTrue(bundle["ready"])
        self.assertEqual(list(bundle["universe"].columns), SNAPSHOT.UNIVERSE_COLUMNS)
        self.assertEqual(len(bundle["universe"]), 1)
        self.assertEqual(len(bundle["manual_risk_queue"]), 0)

    def test_default_free_source_providers_use_akshare_without_network_in_tests(self):
        fake_ak = types.SimpleNamespace()
        fake_ak.bond_zh_hs_cov_spot = lambda: pd.DataFrame([
            {"symbol": "sz123001", "name": "甲转债", "trade": "120.00"},
        ])
        fake_ak.stock_zh_a_hist = lambda **_: (_ for _ in ()).throw(RuntimeError("primary failed"))
        fake_ak.stock_zh_a_daily = lambda **_: pd.DataFrame([
            {"date": pd.Timestamp("2026-07-30"), "close": 12.0},
        ])
        master = pd.DataFrame([
            {
                "bond_code": "123001", "stock_code": "300001", "convert_price": 10.0,
                "contract_maturity": pd.Timestamp("2030-01-01"),
                "double_low_z252": -0.5, "balance_bil": 1.0,
            },
        ])

        providers = SNAPSHOT.default_free_source_providers(
            signal_date=pd.Timestamp("2026-07-30"),
            security_master=master,
            ak_module=fake_ak,
        )
        bundle = SNAPSHOT.build_shadow_universe_bundle(providers, pd.Timestamp("2026-07-30"))

        self.assertTrue(bundle["ready"])
        self.assertEqual(len(bundle["universe"]), 1)
        self.assertAlmostEqual(float(bundle["universe"].loc[0, "double_low"]), 120.0)
        self.assertEqual(bundle["frames"]["stock_close"].loc[0, "probe_source"], "stock_zh_a_daily")

    def test_stock_close_fallback_rejects_wrong_date(self):
        fake_ak = types.SimpleNamespace()
        fake_ak.stock_zh_a_hist = lambda **_: pd.DataFrame([
            {"日期": pd.Timestamp("2026-07-29"), "收盘": 12.0},
        ])
        fake_ak.stock_zh_a_daily = lambda **_: pd.DataFrame([
            {"date": pd.Timestamp("2026-07-29"), "close": 12.0},
        ])

        with self.assertRaisesRegex(RuntimeError, "no row for signal date"):
            SNAPSHOT.fetch_stock_close_with_fallback("300001", pd.Timestamp("2026-07-30"), fake_ak)

    def test_security_master_contract_blocks_missing_required_columns(self):
        master = pd.DataFrame([
            {"bond_code": "123001", "stock_code": "300001", "contract_maturity": pd.Timestamp("2030-01-01")},
        ])

        result = SNAPSHOT.validate_security_master(master)

        self.assertFalse(result["ready"])
        self.assertIn("missing_columns:convert_price", result["blocking_reasons"])

    def test_security_master_contract_reports_row_level_missing_values(self):
        master = pd.DataFrame([
            {
                "bond_code": "123001", "stock_code": "300001", "convert_price": 10.0,
                "contract_maturity": pd.Timestamp("2030-01-01"),
                "double_low_z252": -0.5, "balance_bil": 1.0,
            },
            {
                "bond_code": "123002", "stock_code": "", "convert_price": None,
                "contract_maturity": pd.NaT,
                "double_low_z252": None, "balance_bil": None,
            },
        ])

        result = SNAPSHOT.validate_security_master(master)

        self.assertFalse(result["ready"])
        issues = result["issues"]
        self.assertEqual(set(issues["bond_code"]), {"123002"})
        self.assertIn("missing_stock_code", issues.loc[0, "issue"])
        self.assertIn("missing_convert_price", issues.loc[0, "issue"])
        self.assertIn("missing_contract_maturity", issues.loc[0, "issue"])

    def test_shadow_bundle_stops_when_security_master_contract_fails(self):
        providers = {
            "cb_spot": lambda: pd.DataFrame([
                {"symbol": "sz123001", "name": "甲转债", "trade": "120.00"},
            ]),
            "security_master": lambda: pd.DataFrame([
                {"bond_code": "123001", "stock_code": "300001", "contract_maturity": pd.Timestamp("2030-01-01")},
            ]),
            "stock_close": lambda: pd.DataFrame([
                {"stock_code": "300001", "close": 12.0},
            ]),
        }

        bundle = SNAPSHOT.build_shadow_universe_bundle(providers, pd.Timestamp("2026-07-30"))

        self.assertFalse(bundle["ready"])
        self.assertEqual(len(bundle["universe"]), 0)
        self.assertEqual(bundle["master_validation"]["blocking_reasons"], ["missing_columns:convert_price"])

    def test_shadow_bundle_allows_missing_enhancement_columns_but_leaves_enhanced_ranks_blank(self):
        providers = {
            "cb_spot": lambda: pd.DataFrame([
                {"symbol": "sz123001", "name": "甲转债", "trade": "120.00"},
            ]),
            "security_master": lambda: pd.DataFrame([
                {
                    "bond_code": "123001", "stock_code": "300001", "convert_price": 10.0,
                    "contract_maturity": pd.Timestamp("2030-01-01"),
                },
            ]),
            "stock_close": lambda: pd.DataFrame([
                {"stock_code": "300001", "close": 12.0},
            ]),
        }

        bundle = SNAPSHOT.build_shadow_universe_bundle(providers, pd.Timestamp("2026-07-30"))

        self.assertTrue(bundle["ready"])
        self.assertEqual(
            bundle["master_validation"]["missing_enhancement_columns"],
            ["double_low_z252", "balance_bil"],
        )
        row = bundle["universe"].iloc[0]
        self.assertEqual(int(row["b0_base_rank"]), 1)
        self.assertTrue(pd.isna(row["c_bal_rank"]))
        self.assertTrue(pd.isna(row["c_off_rank"]))

    def test_apply_risk_status_inputs_maps_states_and_exclusions(self):
        signal_date = pd.Timestamp("2026-07-30")
        snapshot = pd.DataFrame([
            {"bond_code": "123001", "bond_name": "甲转债", "stock_code": "300001"},
            {"bond_code": "123002", "bond_name": "乙转债", "stock_code": "300002"},
            {"bond_code": "123003", "bond_name": "丙转债", "stock_code": "300003"},
            {"bond_code": "123004", "bond_name": "丁转债", "stock_code": "300004"},
        ])
        risk_inputs = {
            "call_notice": pd.DataFrame([
                {"bond_code": "123001", "notice_date": pd.Timestamp("2026-07-29"), "status": "announced"},
            ]),
            "tail_risk": pd.DataFrame([
                {"bond_code": "123002", "risk_date": pd.Timestamp("2026-07-20"), "risk_type": "default_watch"},
            ]),
            "tradability": pd.DataFrame([
                {"bond_code": "123003", "st_risk_active": True, "tradability_state": "st_risk_active"},
            ]),
        }

        result = SNAPSHOT.apply_risk_status_inputs(snapshot, risk_inputs, signal_date)

        call_row = result[result["bond_code"].eq("123001")].iloc[0]
        self.assertEqual(call_row["call_status"], "call_notice_active")
        self.assertIn("call_notice_active", call_row["risk_exclusion_reasons"])
        credit_row = result[result["bond_code"].eq("123002")].iloc[0]
        self.assertEqual(credit_row["credit_risk_state"], "tail_risk_active")
        self.assertIn("tail_risk_active", credit_row["risk_exclusion_reasons"])
        st_row = result[result["bond_code"].eq("123003")].iloc[0]
        self.assertTrue(bool(st_row["st_risk_active"]))
        self.assertEqual(st_row["tradability_state"], "st_risk_active")
        self.assertIn("st_risk_active", st_row["risk_exclusion_reasons"])
        clean_row = result[result["bond_code"].eq("123004")].iloc[0]
        self.assertEqual(clean_row["risk_exclusion_reasons"], "")

    def test_shadow_bundle_applies_risk_inputs_before_ranking(self):
        providers = {
            "cb_spot": lambda: pd.DataFrame([
                {"symbol": "sz123001", "name": "甲转债", "trade": "120.00"},
                {"symbol": "sz123002", "name": "乙转债", "trade": "110.00"},
            ]),
            "security_master": lambda: pd.DataFrame([
                {
                    "bond_code": "123001", "stock_code": "300001", "convert_price": 10.0,
                    "contract_maturity": pd.Timestamp("2030-01-01"),
                    "double_low_z252": -0.5, "balance_bil": 1.0,
                },
                {
                    "bond_code": "123002", "stock_code": "300002", "convert_price": 10.0,
                    "contract_maturity": pd.Timestamp("2030-01-01"),
                    "double_low_z252": -1.0, "balance_bil": 0.5,
                },
            ]),
            "stock_close": lambda: pd.DataFrame([
                {"stock_code": "300001", "close": 12.0},
                {"stock_code": "300002", "close": 11.0},
            ]),
        }
        risk_inputs = {
            "call_notice": pd.DataFrame([
                {"bond_code": "123002", "notice_date": pd.Timestamp("2026-07-29")},
            ]),
        }

        bundle = SNAPSHOT.build_shadow_universe_bundle(
            providers, pd.Timestamp("2026-07-30"), risk_inputs=risk_inputs
        )

        risky = bundle["universe"][bundle["universe"]["bond_code"].eq("123002")].iloc[0]
        self.assertFalse(bool(risky["eligible_common"]))
        self.assertEqual(risky["call_status"], "call_notice_active")
        self.assertIn("call_notice_active", risky["exclusion_reasons"])
        self.assertTrue(pd.isna(risky["b0_base_rank"]))
        self.assertEqual(bundle["manual_risk_queue"].loc[0, "bond_code"], "123002")

    def test_seal_shadow_universe_bundle_writes_research_outputs_when_ready(self):
        providers = {
            "cb_spot": lambda: pd.DataFrame([
                {"symbol": "sz123001", "name": "甲转债", "trade": "120.00"},
            ]),
            "security_master": lambda: pd.DataFrame([
                {
                    "bond_code": "123001", "stock_code": "300001", "convert_price": 10.0,
                    "contract_maturity": pd.Timestamp("2030-01-01"),
                    "double_low_z252": -0.5, "balance_bil": 1.0,
                },
            ]),
            "stock_close": lambda: pd.DataFrame([
                {"stock_code": "300001", "close": 12.0},
            ]),
        }
        bundle = SNAPSHOT.build_shadow_universe_bundle(providers, pd.Timestamp("2026-07-30"))

        with tempfile.TemporaryDirectory() as tmpdir:
            outputs = SNAPSHOT.seal_shadow_universe_bundle(
                bundle,
                signal_date=pd.Timestamp("2026-07-30"),
                generated_at=pd.Timestamp("2026-07-30 21:00:00+0800"),
                output_dir=Path(tmpdir),
                dry_run=True,
            )

            manifest = pd.read_json(outputs["manifest"], typ="series")
            universe = pd.read_csv(outputs["universe"])
            audit = pd.read_csv(outputs["source_audit"])

            self.assertTrue(bool(manifest["ready"]))
            self.assertEqual(manifest["data_status"], "dry_run_invalid_for_forward_sample")
            self.assertFalse(bool(manifest["generates_orders"]))
            self.assertFalse(bool(manifest["writes_database"]))
            self.assertEqual(len(universe), 1)
            self.assertEqual(len(audit), 3)

    def test_seal_shadow_universe_bundle_writes_empty_outputs_when_blocked(self):
        providers = {
            "cb_spot": lambda: pd.DataFrame([
                {"symbol": "sz123001", "name": "甲转债", "trade": "120.00"},
            ]),
            "security_master": lambda: pd.DataFrame([
                {"bond_code": "123001", "stock_code": "300001", "contract_maturity": pd.Timestamp("2030-01-01")},
            ]),
            "stock_close": lambda: pd.DataFrame([
                {"stock_code": "300001", "close": 12.0},
            ]),
        }
        bundle = SNAPSHOT.build_shadow_universe_bundle(providers, pd.Timestamp("2026-07-30"))

        with tempfile.TemporaryDirectory() as tmpdir:
            outputs = SNAPSHOT.seal_shadow_universe_bundle(
                bundle,
                signal_date=pd.Timestamp("2026-07-30"),
                generated_at=pd.Timestamp("2026-07-30 21:00:00+0800"),
                output_dir=Path(tmpdir),
                dry_run=True,
            )

            manifest = pd.read_json(outputs["manifest"], typ="series")
            universe = pd.read_csv(outputs["universe"])
            queue = pd.read_csv(outputs["manual_risk_queue"])

            self.assertFalse(bool(manifest["ready"]))
            self.assertIn("missing_columns:convert_price", manifest["blocking_reasons"])
            self.assertEqual(len(universe), 0)
            self.assertEqual(len(queue), 0)

    def test_seal_shadow_universe_bundle_does_not_write_raw_snapshot_or_frames(self):
        providers = {
            "cb_spot": lambda: pd.DataFrame([
                {"symbol": "sz123001", "name": "甲转债", "trade": "120.00"},
            ]),
            "security_master": lambda: pd.DataFrame([
                {
                    "bond_code": "123001", "stock_code": "300001", "convert_price": 10.0,
                    "contract_maturity": pd.Timestamp("2030-01-01"),
                    "double_low_z252": -0.5, "balance_bil": 1.0,
                },
            ]),
            "stock_close": lambda: pd.DataFrame([
                {"stock_code": "300001", "close": 12.0},
            ]),
        }
        bundle = SNAPSHOT.build_shadow_universe_bundle(providers, pd.Timestamp("2026-07-30"))

        with tempfile.TemporaryDirectory() as tmpdir:
            SNAPSHOT.seal_shadow_universe_bundle(
                bundle,
                signal_date=pd.Timestamp("2026-07-30"),
                generated_at=pd.Timestamp("2026-07-30 21:00:00+0800"),
                output_dir=Path(tmpdir),
                dry_run=True,
            )
            names = {path.name for path in Path(tmpdir).iterdir()}

            self.assertFalse(any("snapshot" in name for name in names))
            self.assertFalse(any("frame" in name for name in names))


if __name__ == "__main__":
    unittest.main()
