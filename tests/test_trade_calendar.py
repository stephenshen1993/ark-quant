import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from datasource.trade_calendar import load_exchange_trading_days, resolve_effective_trading_date, resolve_next_trading_date


class EffectiveTradingDateTests(unittest.TestCase):
    def test_next_trading_date_skips_holidays_and_weekends(self) -> None:
        days = [date(2026, 9, 30), date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)]
        for current, expected in zip(days, days[1:]):
            with self.subTest(current=current):
                self.assertEqual(resolve_next_trading_date(current, trading_days=days), expected)

    def test_next_trading_date_requires_future_calendar_coverage(self) -> None:
        for days in ([], [date(2026, 9, 30)]):
            with self.subTest(days=days), self.assertRaisesRegex(RuntimeError, "之后的有效交易日"):
                resolve_next_trading_date(date(2026, 9, 30), trading_days=days)

    def test_next_trading_date_requests_coverage_beyond_weekend(self) -> None:
        with patch("datasource.trade_calendar.load_exchange_trading_days", return_value=[
            date(2026, 10, 9), date(2026, 10, 12),
        ]) as load_days:
            self.assertEqual(resolve_next_trading_date(date(2026, 10, 9)), date(2026, 10, 12))
        load_days.assert_called_once_with(as_of=date(2026, 10, 12))

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

    def test_stale_calendar_is_not_used_when_refresh_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "calendar.csv"
            cache_path.write_text("trade_date\n2026-02-13\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "不覆盖 2026-02-18"):
                load_exchange_trading_days(
                    as_of=date(2026, 2, 18),
                    cache_path=cache_path,
                    fetcher=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
                )

    def test_nonempty_refresh_must_still_cover_the_target_date(self) -> None:
        with TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "calendar.csv"

            with self.assertRaisesRegex(RuntimeError, "刷新结果不覆盖 2026-02-18"):
                load_exchange_trading_days(
                    as_of=date(2026, 2, 18),
                    cache_path=cache_path,
                    fetcher=lambda: [date(2026, 2, 13)],
                )

            self.assertFalse(cache_path.exists())

    def test_friday_cache_covers_an_offline_weekend(self) -> None:
        with TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "calendar.csv"
            cache_path.write_text("trade_date\n2026-02-13\n", encoding="utf-8")

            days = load_exchange_trading_days(
                as_of=date(2026, 2, 15),
                cache_path=cache_path,
                fetcher=lambda: self.fail("周末不应刷新已覆盖到周五的日历"),
            )

            self.assertEqual(days, [date(2026, 2, 13)])

    def test_monday_preopen_loads_calendar_for_the_completed_weekend_bound(self) -> None:
        with patch(
            "datasource.trade_calendar.load_exchange_trading_days",
            return_value=[date(2026, 2, 13)],
        ) as load_days:
            resolved = resolve_effective_trading_date(
                now=datetime(2026, 2, 16, 8, 30),
            )

        self.assertEqual(resolved, date(2026, 2, 13))
        load_days.assert_called_once_with(as_of=date(2026, 2, 15))


if __name__ == "__main__":
    unittest.main()
