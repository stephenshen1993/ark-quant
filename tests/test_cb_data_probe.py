from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from strategies.convertible_bond import data_probe


class ConvertibleBondDataProbeTests(unittest.TestCase):
    def test_missing_rates_counts_blank_strings_as_missing(self) -> None:
        frame = pd.DataFrame({"代码": ["1", "2"], "评级": ["AA", ""]})

        self.assertEqual(data_probe.missing_rates(frame), {"代码": 0.0, "评级": 50.0})

    def test_calculate_momentum_20d_uses_twenty_trading_day_interval(self) -> None:
        close = pd.Series(range(100, 121))

        self.assertAlmostEqual(data_probe.calculate_momentum_20d(close), 0.2)

    def test_detect_supported_fields_maps_available_columns(self) -> None:
        frame = pd.DataFrame(
            {
                "债券代码": ["123456"],
                "债券简称": ["示例转债"],
                "债现价": [120.0],
                "转股溢价率": [8.0],
                "正股代码": ["000001"],
                "正股简称": ["示例股份"],
            }
        )

        support = data_probe.detect_supported_fields({"AKShare bond_zh_cov": frame}, momentum_available=False)

        self.assertTrue(support["转债代码"]["available"])
        self.assertTrue(support["正股名称"]["available"])
        self.assertFalse(support["正股 20 日动量"]["available"])

    def test_render_report_does_not_include_secret_values(self) -> None:
        report = data_probe.render_report(
            source_results=[],
            field_support={},
            stock_probe=data_probe.StockMomentumProbe(sample_size=0),
            akshare_version="test",
            secrets=["cookie-secret", "token-secret"],
        )

        self.assertNotIn("cookie-secret", report)
        self.assertNotIn("token-secret", report)
        self.assertIn("字段可用仅表示至少存在一个非空值", report)

    def test_parse_args_accepts_stock_sample_size(self) -> None:
        args = data_probe.parse_args(["--stock-sample-size", "20"])

        self.assertEqual(args.stock_sample_size, 20)

    def test_stock_probe_result_reports_failed_eastmoney_sample(self) -> None:
        stock_probe = data_probe.StockMomentumProbe(
            sample_size=1,
            requested_codes=["000001"],
            failed={"000001": "remote closed connection"},
        )

        result = data_probe.stock_probe_source_result(stock_probe)

        self.assertEqual(result.name, "东方财富正股历史行情")
        self.assertEqual(result.status, "失败")
        self.assertIn("0/1", result.reason)

    def test_tushare_client_initialization_failure_is_recoverable(self) -> None:
        class BrokenTushare:
            @staticmethod
            def pro_api(token: str):
                raise RuntimeError("permission denied")

        with patch.object(data_probe.importlib, "import_module", return_value=BrokenTushare()):
            results = data_probe.probe_tushare("configured-token")

        self.assertEqual([result.status for result in results], ["失败", "失败"])
        self.assertTrue(all("permission denied" in result.reason for result in results))


if __name__ == "__main__":
    unittest.main()
