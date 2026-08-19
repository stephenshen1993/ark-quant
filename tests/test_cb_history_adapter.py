import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

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


class ConvertibleBondHistoryAdapterTests(unittest.TestCase):
    def test_same_day_reuses_cache_and_next_day_fetches_only_the_gap(self) -> None:
        effective_date = date(2026, 8, 21)
        next_date = effective_date + timedelta(days=1)
        all_days = [effective_date - timedelta(days=20 - index) for index in range(22)]
        trading_days = all_days[:21]
        next_trading_days = all_days[1:]
        universe = pd.DataFrame([{"bond_code": "113001", "stock_code": "600001"}])
        config = {"data": {"per_symbol_fetch_workers": 2}}
        ak = _FakeAkshare(all_days)

        def bond_turnover(_ak, _codes, _config, target):
            return pd.DataFrame([{
                "bond_code": "113001",
                "cb_close_daily": 120.0,
                "turnover_yuan_daily": 12_000_000.0,
                "turnover_trade_date": target.isoformat(),
            }])

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
        self.assertEqual(first.preparation["momentum"]["mode"], "cold_build")
        self.assertEqual(second.preparation["bond_history"]["mode"], "cache_hit")
        self.assertEqual(second.preparation["stock_history"]["mode"], "cache_hit")
        self.assertEqual(second.preparation["momentum"]["mode"], "cache_hit")
        self.assertEqual(second.preparation["volatility"]["mode"], "cache_hit")
        self.assertEqual(second.preparation["stock_history"]["external_calls"], 0)
        self.assertEqual(second.stock_factors["stock_code"].tolist(), ["600001"])
        self.assertEqual(third.preparation["stock_history"]["mode"], "incremental")
        self.assertEqual(third.preparation["stock_history"]["refreshed_records"], 1)


if __name__ == "__main__":
    unittest.main()
