from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from strategies.cb_rotation import run


class ConvertibleBondRotationTests(unittest.TestCase):
    def test_as_number_preserves_negative_values(self) -> None:
        values = run.as_number(pd.Series(["-3.28", "--", "-", "1.25%"]))

        self.assertEqual(values.iloc[0], -3.28)
        self.assertTrue(pd.isna(values.iloc[1]))
        self.assertTrue(pd.isna(values.iloc[2]))
        self.assertEqual(values.iloc[3], 1.25)

    def test_normalize_cb_data_does_not_treat_issuance_as_remaining_size(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "债券代码": "123456",
                    "债券简称": "样例转债",
                    "正股代码": "000001",
                    "正股简称": "样例股份",
                    "债现价": "120",
                    "转股溢价率": "-2.5",
                    "发行规模": "8",
                }
            ]
        )

        normalized = run.normalize_cb_data(raw)

        self.assertTrue(pd.isna(normalized.loc[0, "remaining_size_100m"]))
        self.assertEqual(normalized.loc[0, "premium_rate"], -2.5)

    def test_redeem_remaining_size_overrides_existing_value(self) -> None:
        class FakeAk:
            @staticmethod
            def bond_cb_redeem_jsl() -> pd.DataFrame:
                return pd.DataFrame(
                    [
                        {
                            "代码": "123456",
                            "剩余规模": "2.5",
                            "到期日": "2030-01-01",
                            "强赎状态": "公告不强赎",
                        }
                    ]
                )

        cb = pd.DataFrame(
            [
                {
                    "bond_code": "123456",
                    "remaining_size_100m": 8.0,
                    "maturity_date": pd.NaT,
                    "call_status": pd.NA,
                }
            ]
        )

        with patch.object(run, "fetch_with_cache", return_value=FakeAk.bond_cb_redeem_jsl()):
            merged = run.enrich_cb_with_redeem_data(FakeAk(), cb, {"data": {"use_cache_on_failure": False}})

        self.assertEqual(merged.loc[0, "remaining_size_100m"], 2.5)

    def test_call_risk_filter_excludes_notice_to_redeem(self) -> None:
        cb = pd.DataFrame(
            [
                {
                    "cb_price": 129.85,
                    "premium_rate": -0.95,
                    "remaining_size_100m": 8.961,
                    "maturity_date": date(2028, 4, 15),
                    "listing_date": date(2024, 4, 15),
                    "remaining_years": pd.NA,
                    "call_status": "公告要强赎",
                    "stock_name": "艾迪精密",
                    "active_reference": True,
                }
            ]
        )
        config = {
            "filters": {
                "max_cb_price": 130,
                "min_remaining_size_100m": 3,
                "min_years_to_maturity": 1,
                "exclude_call_risk": True,
                "exclude_st_stock": True,
            },
            "data": {"strict_original_rules": True},
        }

        self.assertTrue(run.apply_cb_prefilters(cb, config).empty)

    def test_strict_filter_coverage_rejects_missing_remaining_size(self) -> None:
        cb = pd.DataFrame(
            [
                {
                    "cb_price": 120.0,
                    "premium_rate": 10.0,
                    "remaining_size_100m": pd.NA,
                    "maturity_date": date(2030, 1, 1),
                    "listing_date": date(2024, 1, 1),
                    "call_status": "无强赎提示",
                    "active_reference": True,
                }
            ]
        )
        config = {"filters": {"max_cb_price": 130}, "data": {"strict_original_rules": True}}

        with self.assertRaisesRegex(RuntimeError, "remaining_size_100m"):
            run.enforce_cb_filter_coverage(cb, config)

    def test_require_fresh_dates_rejects_stale_market_data(self) -> None:
        stale = pd.DataFrame({"trade_date": [(date.today() - timedelta(days=5)).isoformat()]})

        with self.assertRaisesRegex(RuntimeError, "包含过期数据"):
            run.require_fresh_dates(stale, "trade_date", 4, "测试行情")

    def test_require_single_trade_date_rejects_mixed_dates(self) -> None:
        mixed = pd.DataFrame({"trade_date": ["2026-05-27", "2026-05-28"]})

        with self.assertRaisesRegex(RuntimeError, "同一个已完成交易日"):
            run.require_single_trade_date(mixed, "trade_date", "测试行情")

    def test_snapshot_window_allows_pre_open_run(self) -> None:
        config = {"data": {"selection_data_mode": "previous_close", "block_intraday_runs": True}}

        run.enforce_snapshot_run_window(config, now=datetime(2026, 6, 2, 8, 30))

    def test_snapshot_window_rejects_intraday_run(self) -> None:
        config = {"data": {"selection_data_mode": "previous_close", "block_intraday_runs": True}}

        with self.assertRaisesRegex(RuntimeError, "09:25 前"):
            run.enforce_snapshot_run_window(config, now=datetime(2026, 6, 2, 10, 0))

    def test_daily_bond_market_data_overrides_snapshot_price(self) -> None:
        class FakeAk:
            @staticmethod
            def bond_zh_hs_cov_daily(symbol: str) -> pd.DataFrame:
                return pd.DataFrame([{"date": "2026-05-28", "close": 119.5, "volume": 300000}])

        cb = pd.DataFrame([{"bond_code": "123456", "cb_price": 121.0, "turnover_yuan": pd.NA}])
        with TemporaryDirectory() as temp_dir, patch.object(run, "CACHE_DIR", Path(temp_dir)):
            enriched = run.enrich_cb_with_daily_market_data(FakeAk(), cb, {"data": {}})

        self.assertEqual(enriched.loc[0, "cb_price"], 119.5)
        self.assertEqual(enriched.loc[0, "turnover_yuan"], 35_850_000)

    def test_daily_bond_market_data_rejects_old_cache_without_close(self) -> None:
        cached = pd.DataFrame(
            [{"bond_code": "123456", "turnover_yuan_daily": 35_850_000, "turnover_trade_date": "2026-05-28"}]
        )
        cb = pd.DataFrame([{"bond_code": "123456", "cb_price": 121.0, "turnover_yuan": pd.NA}])
        with patch.object(run, "fetch_cb_daily_turnover", return_value=cached):
            with self.assertRaisesRegex(RuntimeError, "cb_close_daily"):
                run.enrich_cb_with_daily_market_data(object(), cb, {"data": {"strict_original_rules": True}})

    def test_drop_uncovered_cb_market_data_removes_missing_turnover_rows(self) -> None:
        cb = pd.DataFrame([
            {"bond_code": "113703", "turnover_yuan": pd.NA, "turnover_trade_date": pd.NA},
            {"bond_code": "110084", "turnover_yuan": 59_237_310.78, "turnover_trade_date": "2026-07-06"},
        ])

        cleaned = run.drop_uncovered_cb_market_data(cb)

        self.assertEqual(cleaned["bond_code"].tolist(), ["110084"])

    def test_fetch_stock_factors_with_cache_backfills_missing_symbols_from_recent_cache(self) -> None:
        as_of = date.today()
        fallback_date = as_of - timedelta(days=1)
        with TemporaryDirectory() as temp_dir, patch.object(run, "CACHE_DIR", Path(temp_dir)):
            fallback = pd.DataFrame(
                [
                    {
                        "stock_code": "000002",
                        "stock_momentum_20d": 0.02,
                        "stock_volatility_20d": 0.18,
                        "market_cap_estimate": 2.0e9,
                        "stock_factor_trade_date": fallback_date.isoformat(),
                    }
                ]
            )
            fallback.to_csv(
                Path(temp_dir) / f"stock_factors_{fallback_date:%Y%m%d}_latest.csv",
                index=False,
            )
            fetched = pd.DataFrame(
                [
                    {
                        "stock_code": "000001",
                        "stock_momentum_20d": 0.01,
                        "stock_volatility_20d": 0.16,
                        "market_cap_estimate": 1.0e9,
                        "stock_factor_trade_date": as_of.isoformat(),
                    }
                ]
            )

            with patch.object(run, "fetch_stock_factors", return_value=fetched):
                out = run.fetch_stock_factors_with_cache(
                    object(),
                    ["000001", "000002"],
                    as_of,
                    {"data": {"use_cache_on_failure": True, "max_cache_age_days": 7}},
                )

        self.assertEqual(sorted(out["stock_code"].astype(str).str.zfill(6).tolist()), ["000001", "000002"])

    def test_drop_uncovered_factor_data_removes_missing_factor_rows(self) -> None:
        candidates = pd.DataFrame(
            [
                {
                    "bond_code": "113001",
                    "stock_code": "000001",
                    "stock_momentum_20d": 0.01,
                    "stock_volatility_20d": 0.16,
                    "market_cap": 1.0e9,
                },
                {
                    "bond_code": "113002",
                    "stock_code": "000002",
                    "stock_momentum_20d": pd.NA,
                    "stock_volatility_20d": 0.18,
                    "market_cap": 2.0e9,
                },
            ]
        )

        cleaned = run.drop_uncovered_factor_data(candidates)

        self.assertEqual(cleaned["bond_code"].tolist(), ["113001"])

    def test_latest_cache_uses_business_date_in_filename(self) -> None:
        stale_date = date.today() - timedelta(days=8)
        with TemporaryDirectory() as temp_dir:
            stale = Path(temp_dir) / f"sample_{stale_date:%Y%m%d}.csv"
            stale.write_text("value\n1\n", encoding="utf-8")
            with patch.object(run, "CACHE_DIR", Path(temp_dir)):
                self.assertIsNone(run.latest_cache("sample", max_age_days=7))


    def test_market_cap_estimate_fills_missing_and_dates_it(self) -> None:
        merged = pd.DataFrame(
            [
                {
                    "stock_code": "000001",
                    "market_cap": pd.NA,
                    "market_cap_estimate": 5.0e9,
                    "market_cap_as_of_date": pd.NA,
                    "stock_factor_trade_date": "2026-06-04",
                }
            ]
        )
        out = run.apply_market_cap_estimate(merged, {"data": {"allow_market_cap_estimate": True}})

        self.assertEqual(out.loc[0, "market_cap"], 5.0e9)
        self.assertEqual(out.loc[0, "market_cap_as_of_date"], "2026-06-04")
        self.assertEqual(out.loc[0, "market_cap_source"], run.MARKET_CAP_ESTIMATE_SOURCE)

    def test_market_cap_estimate_prefers_fresh_over_stale_cap(self) -> None:
        merged = pd.DataFrame(
            [
                {
                    "stock_code": "000001",
                    "market_cap": 9.9e9,
                    "market_cap_estimate": 5.0e9,
                    "market_cap_as_of_date": "2026-06-01",
                    "stock_factor_trade_date": "2026-06-04",
                }
            ]
        )
        out = run.apply_market_cap_estimate(merged, {"data": {"allow_market_cap_estimate": True}})

        self.assertEqual(out.loc[0, "market_cap"], 5.0e9)
        self.assertEqual(out.loc[0, "market_cap_as_of_date"], "2026-06-04")

    def test_market_cap_estimate_keeps_fresh_real_cap(self) -> None:
        merged = pd.DataFrame(
            [
                {
                    "stock_code": "000001",
                    "market_cap": 9.9e9,
                    "market_cap_estimate": 5.0e9,
                    "market_cap_as_of_date": "2026-06-05",
                    "stock_factor_trade_date": "2026-06-04",
                }
            ]
        )
        out = run.apply_market_cap_estimate(merged, {"data": {"allow_market_cap_estimate": True}})

        self.assertEqual(out.loc[0, "market_cap"], 9.9e9)

    def test_size_rebalance_lots_sells_and_respects_cash(self) -> None:
        from strategies.cb_rotation import size_orders

        target = pd.DataFrame(
            [
                {"bond_code": "100001", "bond_name": "甲转债"},
                {"bond_code": "100002", "bond_name": "乙转债"},
            ]
        )
        positions = pd.DataFrame(
            [
                {"bond_code": "100002", "bond_name": "乙转债", "shares": 50},
                {"bond_code": "100003", "bond_name": "丙转债", "shares": 100},
            ]
        )
        prices = {"100001": 100.0, "100002": 200.0, "100003": 50.0}
        sheet, summary = size_orders.size_rebalance(target, positions, cash=5000.0, prices=prices)

        actions = dict(zip(sheet["bond_code"], sheet["action"]))
        deltas = dict(zip(sheet["bond_code"], sheet["delta_shares"]))
        self.assertEqual(actions["100003"], "SELL")
        self.assertEqual(deltas["100003"], -100)  # 不在目标里，全清
        self.assertEqual(actions["100001"], "BUY")
        self.assertEqual(summary["total_value"], 20000.0)  # 持仓15000 + 现金5000
        self.assertGreaterEqual(summary["cash_left"], 0)  # 永不超支
        self.assertTrue((sheet["target_shares"] % size_orders.LOT == 0).all())  # 张数都是10的整数倍

    def test_snapshot_raw_data_writes_frames_and_manifest(self) -> None:
        import json

        frames = {
            "enriched_universe": pd.DataFrame([{"bond_code": "123456", "score": 0.8}]),
            "empty_frame": pd.DataFrame(),
        }
        with TemporaryDirectory() as temp_dir, patch.object(run, "RAW_DIR", Path(temp_dir)):
            out = run.snapshot_raw_data(date(2026, 6, 4), frames, {"data": {}})

            self.assertEqual(out, Path(temp_dir) / "20260604")
            self.assertTrue((out / "enriched_universe.csv").exists())
            self.assertFalse((out / "empty_frame.csv").exists())  # 空表跳过
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["trade_date"], "2026-06-04")
            self.assertEqual(manifest["rows"], {"enriched_universe": 1})
            self.assertTrue((out / "config.json").exists())

    def test_snapshot_raw_data_disabled_by_config(self) -> None:
        with TemporaryDirectory() as temp_dir, patch.object(run, "RAW_DIR", Path(temp_dir)):
            out = run.snapshot_raw_data(date(2026, 6, 4), {}, {"data": {"save_raw_snapshot": False}})
            self.assertIsNone(out)

    def test_resolve_trade_date_uses_latest_trade_date_column(self) -> None:
        df = pd.DataFrame({"turnover_trade_date": ["2026-06-03", "2026-06-04"]})
        self.assertEqual(run.resolve_trade_date(df), date(2026, 6, 4))

    def test_persist_rankings_propagates_database_failure(self) -> None:
        with patch("datasource.db.init_db"), patch(
            "datasource.db.create_complete_strategy_run",
            side_effect=RuntimeError("forced persistence failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced persistence failure"):
                run.persist_rankings(date(2026, 6, 4), pd.DataFrame([{"bond_code": "113062"}]))

    def test_market_cap_estimate_noop_when_disabled(self) -> None:
        merged = pd.DataFrame(
            [
                {
                    "stock_code": "000001",
                    "market_cap": pd.NA,
                    "market_cap_estimate": 5.0e9,
                    "market_cap_as_of_date": pd.NA,
                    "stock_factor_trade_date": "2026-06-04",
                }
            ]
        )
        out = run.apply_market_cap_estimate(merged, {"data": {"allow_market_cap_estimate": False}})

        self.assertTrue(pd.isna(out.loc[0, "market_cap"]))


if __name__ == "__main__":
    unittest.main()
