from __future__ import annotations

import unittest
from datetime import date, timedelta
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

    def test_latest_cache_uses_business_date_in_filename(self) -> None:
        stale_date = date.today() - timedelta(days=8)
        with TemporaryDirectory() as temp_dir:
            stale = Path(temp_dir) / f"sample_{stale_date:%Y%m%d}.csv"
            stale.write_text("value\n1\n", encoding="utf-8")
            with patch.object(run, "CACHE_DIR", Path(temp_dir)):
                self.assertIsNone(run.latest_cache("sample", max_age_days=7))


if __name__ == "__main__":
    unittest.main()
