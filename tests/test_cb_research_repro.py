from __future__ import annotations

import importlib.util
import sys
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


if __name__ == "__main__":
    unittest.main()
