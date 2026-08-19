import unittest
from datetime import date, datetime

from datasource.trade_calendar import resolve_effective_trading_date


class EffectiveTradingDateTests(unittest.TestCase):
    def test_pre_open_and_after_close_can_resolve_different_dates(self) -> None:
        trading_days = [date(2026, 2, 13), date(2026, 2, 18)]

        pre_open = resolve_effective_trading_date(
            now=datetime(2026, 2, 18, 8, 30),
            trading_days=trading_days,
        )
        after_close = resolve_effective_trading_date(
            now=datetime(2026, 2, 18, 15, 30),
            trading_days=trading_days,
        )

        self.assertEqual(pre_open, date(2026, 2, 13))
        self.assertEqual(after_close, date(2026, 2, 18))

    def test_exchange_holiday_is_not_treated_as_a_trading_day(self) -> None:
        trading_days = [date(2026, 2, 13), date(2026, 2, 18)]

        resolved = resolve_effective_trading_date(
            now=datetime(2026, 2, 17, 16, 0),
            trading_days=trading_days,
        )

        self.assertEqual(resolved, date(2026, 2, 13))


if __name__ == "__main__":
    unittest.main()
