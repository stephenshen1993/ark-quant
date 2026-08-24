import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from datasource import stock_history
from strategies.cb_rotation import run as cb_run


class _FakeAkshare:
    def __init__(self, trading_days: list[date]) -> None:
        self.trading_days = trading_days
        self.stock_history_calls = 0
        self.stock_history_requests: list[tuple[str, str, str]] = []

    def stock_zh_a_daily(self, *, symbol, start_date, end_date, adjust):
        del symbol
        self.stock_history_calls += 1
        self.stock_history_requests.append((start_date, end_date, adjust))
        scale = 1.1 if adjust == "hfq" else 1.0
        return pd.DataFrame({
            "date": [day.isoformat() for day in self.trading_days],
            "close": [(10 + index * 0.1) * scale for index in range(len(self.trading_days))],
            "volume": [1000 + index for index in range(len(self.trading_days))],
        })


def _provider_frame(scale: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2026-08-24"],
        "open": [9.9 * scale],
        "close": [10.0 * scale],
        "high": [10.2 * scale],
        "low": [9.8 * scale],
        "amount": [100.0],
    })


class _ProviderAkshare:
    def __init__(self, *, sina=(), tencent=()) -> None:
        self.sina_responses = list(sina)
        self.tencent_responses = list(tencent)
        self.sina_calls: list[str] = []
        self.tencent_calls: list[str] = []

    @staticmethod
    def _next(responses):
        if not responses:
            raise AssertionError("unexpected provider call")
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response.copy()

    def stock_zh_a_daily(self, *, symbol, start_date, end_date, adjust):
        del symbol, start_date, end_date
        self.sina_calls.append(adjust)
        return self._next(self.sina_responses)

    def stock_zh_a_hist_tx(self, *, symbol, start_date, end_date, adjust):
        del symbol, start_date, end_date
        self.tencent_calls.append(adjust)
        return self._next(self.tencent_responses)


class ConvertibleBondHistoryAdapterTests(unittest.TestCase):
    def test_stock_history_retries_sina_without_mixing_fallback_provider(self) -> None:
        ak = _ProviderAkshare(sina=[
            pd.DataFrame({"notice": ["temporary malformed response"]}),
            _provider_frame(),
            _provider_frame(1.1),
        ])
        with patch.object(stock_history, "sleep") as retry_sleep:
            result = stock_history.fetch_stock_history_with_adjustment(
                ak,
                "sina",
                "300037",
                date(2026, 8, 24),
                date(2026, 8, 24),
            )

        self.assertEqual(ak.sina_calls, ["", "", "hfq"])
        self.assertEqual(ak.tencent_calls, [])
        self.assertEqual(result.external_calls, 3)
        self.assertAlmostEqual(result.frame.iloc[0]["adjustment_factor"], 1.1)
        retry_sleep.assert_called_once()

    def test_stock_history_reports_persistent_sina_failure(self) -> None:
        ak = _ProviderAkshare(sina=[
            RuntimeError("sina rate limited") for _ in range(3)
        ])
        with (
            patch.object(stock_history, "sleep"),
            self.assertRaisesRegex(
                RuntimeError,
                "300037 新浪历史行情不复权连续 3 次失败: sina rate limited",
            ),
        ):
            stock_history.fetch_stock_history_with_adjustment(
                ak,
                "sina",
                "300037",
                date(2026, 8, 24),
                date(2026, 8, 24),
            )

        self.assertEqual(ak.sina_calls, ["", "", ""])
        self.assertEqual(ak.tencent_calls, [])

    def test_tencent_stock_history_uses_its_own_price_adapter(self) -> None:
        ak = _ProviderAkshare(tencent=[_provider_frame(), _provider_frame(1.2)])
        result = stock_history.fetch_stock_history_with_adjustment(
            ak,
            "tencent",
            "300037",
            date(2026, 8, 24),
            date(2026, 8, 24),
        )

        self.assertEqual(ak.sina_calls, [])
        self.assertEqual(ak.tencent_calls, ["", "hfq"])
        self.assertEqual(result.external_calls, 2)
        self.assertEqual(
            result.frame.columns.tolist(),
            ["stock_code", "trade_date", "raw_close", "adjustment_factor"],
        )
        self.assertAlmostEqual(result.frame.iloc[0]["adjustment_factor"], 1.2)

    def test_history_adapter_excludes_bonds_not_listed_by_effective_date(self) -> None:
        effective_date = date(2026, 8, 19)
        trading_days = [effective_date - timedelta(days=20 - index) for index in range(21)]
        universe = pd.DataFrame([
            {
                "bond_code": "113001",
                "stock_code": "600001",
                "stock_name": "已上市正股",
                "active_reference": True,
                "listing_date": date(2020, 1, 1),
                "cb_price": 110.0,
                "remaining_size_100m": 10.0,
                "remaining_years": 2.0,
                "maturity_date": date(2028, 1, 1),
                "call_status": "",
            },
            {
                "bond_code": "111026",
                "stock_code": "605123",
                "stock_name": "尚未上市正股",
                "active_reference": True,
                "listing_date": None,
                "cb_price": 100.0,
                "remaining_size_100m": 15.8,
                "remaining_years": 6.0,
                "maturity_date": date(2032, 8, 1),
                "call_status": "",
            },
            {
                "bond_code": "123283",
                "stock_code": "301459",
                "stock_name": "次日上市正股",
                "active_reference": True,
                "listing_date": effective_date + timedelta(days=1),
                "cb_price": 100.0,
                "remaining_size_100m": 6.0,
                "remaining_years": 6.0,
                "maturity_date": date(2032, 8, 1),
                "call_status": "",
            },
        ])
        config = {
            "data": {"strict_original_rules": True, "per_symbol_fetch_workers": 2},
            "filters": {
                "max_cb_price": 150.0,
                "min_remaining_size_100m": 1.0,
                "min_years_to_maturity": 1.0,
                "exclude_call_risk": True,
                "exclude_st_stock": True,
            },
        }
        ak = _FakeAkshare(trading_days)
        requested_bonds: list[str] = []

        def bond_turnover(_ak, codes, _config, target, *, include_metadata=False):
            requested_bonds.extend(codes)
            if list(codes) != ["113001"]:
                raise AssertionError(f"unexpected history scope: {list(codes)}")
            frame = pd.DataFrame([{
                "bond_code": "113001",
                "cb_close_daily": 110.0,
                "turnover_yuan_daily": 11_000_000.0,
                "turnover_trade_date": target.isoformat(),
            }])
            if include_metadata:
                return cb_run.MarketFetchResult(frame=frame, external_calls=1)
            return frame

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(cb_run, "load_exchange_trading_days", return_value=trading_days),
                patch.object(cb_run, "fetch_cb_daily_turnover", side_effect=bond_turnover),
                patch.object(
                    cb_run,
                    "fetch_stock_history_batch",
                    return_value=cb_run.MarketFetchResult(pd.DataFrame(), 0),
                ),
            ):
                result = cb_run.prepare_cb_history_inputs(
                    ak,
                    universe,
                    config,
                    effective_date,
                    history_store_path=root / "history.sqlite3",
                    derived_store_path=root / "derived.sqlite3",
                )

        self.assertEqual(requested_bonds, ["113001"])
        self.assertEqual(result.turnover["bond_code"].tolist(), ["113001"])
        self.assertEqual(result.stock_factors["stock_code"].tolist(), ["600001"])

    def test_tushare_batch_source_uses_two_physical_calls_per_missing_date(self) -> None:
        requested: list[tuple[str, str]] = []

        class FakePro:
            def daily(self, *, trade_date):
                requested.append(("daily", trade_date))
                return pd.DataFrame([{
                    "ts_code": "600001.SH",
                    "trade_date": trade_date,
                    "close": 10.0,
                    "vol": 1000.0,
                    "amount": 10_000.0,
                }])

            def adj_factor(self, *, trade_date):
                requested.append(("adj_factor", trade_date))
                return pd.DataFrame([{
                    "ts_code": "600001.SH",
                    "trade_date": trade_date,
                    "adj_factor": 1.2,
                }])

        fake_module = SimpleNamespace(pro_api=lambda _token: FakePro())
        missing_dates = [date(2026, 8, 20), date(2026, 8, 21)]
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "test-token"}),
            patch.object(cb_run.importlib, "import_module", return_value=fake_module),
        ):
            result = cb_run.fetch_stock_history_batch(["600001"], missing_dates)

        self.assertEqual(result.external_calls, 4)
        self.assertEqual(len(result.frame), 2)
        self.assertEqual(
            requested,
            [
                ("daily", "20260820"),
                ("adj_factor", "20260820"),
                ("daily", "20260821"),
                ("adj_factor", "20260821"),
            ],
        )

    def test_same_day_reuses_cache_and_next_day_fetches_only_the_gap(self) -> None:
        effective_date = date(2026, 8, 21)
        next_date = effective_date + timedelta(days=1)
        all_days = [effective_date - timedelta(days=20 - index) for index in range(22)]
        trading_days = all_days[:21]
        next_trading_days = all_days[1:]
        universe = pd.DataFrame([{
            "bond_code": "113001",
            "stock_code": "600001",
            "stock_name": "测试正股",
            "active_reference": True,
            "listing_date": date(2020, 1, 1),
            "cb_price": 110.0,
            "remaining_size_100m": 10.0,
            "remaining_years": 2.0,
            "maturity_date": date(2028, 1, 1),
            "call_status": "",
        }])
        config = {
            "data": {"per_symbol_fetch_workers": 2},
            "filters": {
                "max_cb_price": 150.0,
                "min_remaining_size_100m": 1.0,
                "min_years_to_maturity": 1.0,
                "exclude_call_risk": True,
                "exclude_st_stock": True,
            },
        }
        ak = _FakeAkshare(all_days)

        def bond_turnover(_ak, _codes, _config, target, *, include_metadata=False):
            frame = pd.DataFrame([{
                "bond_code": "113001",
                "cb_close_daily": 120.0,
                "turnover_yuan_daily": 12_000_000.0,
                "turnover_trade_date": target.isoformat(),
            }])
            if include_metadata:
                return cb_run.MarketFetchResult(frame=frame, external_calls=1)
            return frame

        with tempfile.TemporaryDirectory() as tmp:
            history_store = Path(tmp) / "history.sqlite3"
            derived_store = Path(tmp) / "derived.sqlite3"
            with (
                patch.object(
                    cb_run,
                    "load_exchange_trading_days",
                    side_effect=[trading_days, trading_days, next_trading_days],
                ),
                patch.object(
                    cb_run,
                    "fetch_cb_daily_turnover",
                    side_effect=bond_turnover,
                ) as fetch_bonds,
            ):
                first = cb_run.prepare_cb_history_inputs(
                    ak,
                    universe,
                    config,
                    effective_date,
                    history_store_path=history_store,
                    derived_store_path=derived_store,
                )
                second = cb_run.prepare_cb_history_inputs(
                    ak,
                    universe,
                    config,
                    effective_date,
                    history_store_path=history_store,
                    derived_store_path=derived_store,
                )
                third = cb_run.prepare_cb_history_inputs(
                    ak,
                    universe,
                    config,
                    next_date,
                    history_store_path=history_store,
                    derived_store_path=derived_store,
                )

        self.assertEqual(fetch_bonds.call_count, 2)
        self.assertEqual(ak.stock_history_calls, 4)
        self.assertEqual(
            ak.stock_history_requests,
            [
                (trading_days[0].strftime("%Y%m%d"), effective_date.strftime("%Y%m%d"), ""),
                (trading_days[0].strftime("%Y%m%d"), effective_date.strftime("%Y%m%d"), "hfq"),
                (next_date.strftime("%Y%m%d"), next_date.strftime("%Y%m%d"), ""),
                (next_date.strftime("%Y%m%d"), next_date.strftime("%Y%m%d"), "hfq"),
            ],
        )
        self.assertEqual(first.preparation["stock_history"]["mode"], "cold_build")
        self.assertEqual(first.preparation["bond_history"]["external_calls"], 1)
        self.assertEqual(first.preparation["stock_history"]["batch_requests"], 0)
        self.assertEqual(first.preparation["stock_history"]["fallback_symbols"], 1)
        self.assertEqual(first.preparation["stock_history"]["external_calls"], 2)
        self.assertEqual(first.preparation["momentum"]["mode"], "cold_build")
        self.assertEqual(second.preparation["bond_history"]["mode"], "cache_hit")
        self.assertEqual(second.preparation["bond_history"]["external_calls"], 0)
        self.assertEqual(second.preparation["stock_history"]["mode"], "cache_hit")
        self.assertEqual(second.preparation["momentum"]["mode"], "cache_hit")
        self.assertEqual(second.preparation["volatility"]["mode"], "cache_hit")
        self.assertEqual(second.preparation["stock_history"]["external_calls"], 0)
        self.assertEqual(second.stock_factors["stock_code"].tolist(), ["600001"])
        self.assertEqual(third.preparation["stock_history"]["mode"], "incremental")
        self.assertEqual(third.preparation["bond_history"]["external_calls"], 1)
        self.assertEqual(third.preparation["stock_history"]["refreshed_records"], 1)
        self.assertEqual(third.preparation["stock_history"]["external_calls"], 2)


if __name__ == "__main__":
    unittest.main()
