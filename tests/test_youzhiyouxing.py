import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from multiprocessing.synchronize import Event as EventType
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from datasource import db
from datasource.youzhiyouxing import (
    DATA_URL,
    MarketTemperature,
    TemperatureFetchError,
    get_or_fetch_market_temperature,
    parse_market_temperature,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 12, 12, 0, tzinfo=SHANGHAI)
_PROCESS_FETCH_COUNT = None
_PROCESS_FETCH_FAILS = False


def _counted_process_fetch(timeout: float) -> MarketTemperature:
    with _PROCESS_FETCH_COUNT.get_lock():
        _PROCESS_FETCH_COUNT.value += 1
    time.sleep(0.1)
    if _PROCESS_FETCH_FAILS:
        raise TemperatureFetchError("offline")
    return MarketTemperature(45.0, "正常", "2026-07-11T15:00")


def _refresh_in_process(database: str, start: EventType, results) -> None:
    import datasource.youzhiyouxing as temperature_source

    db._TEST_CONN = None
    db.DB_PATH = Path(database)
    temperature_source._now_shanghai = lambda: NOW
    temperature_source.fetch_market_temperature = _counted_process_fetch
    start.wait()
    try:
        result = temperature_source.get_or_fetch_market_temperature()
        results.put(("ok", result.id))
    except Exception as exc:
        results.put(("error", repr(exc)))


class TestMarketTemperature(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()

    def tearDown(self):
        if db._TEST_CONN is not None:
            db._TEST_CONN.close()
        db._TEST_CONN = None

    def _seed(
        self,
        *,
        age: timedelta,
        temperature: float = 45.0,
        label: str = "正常",
        source: str = DATA_URL,
        updated_at: str = "2026-07-11T15:00",
    ) -> int:
        fetched_at = (NOW - age).replace(tzinfo=None).isoformat(timespec="seconds")
        cursor = db._TEST_CONN.execute(
            """INSERT INTO market_temperatures
               (temperature, label, source_updated_at, source, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            (temperature, label, updated_at, source, fetched_at),
        )
        db._TEST_CONN.commit()
        return cursor.lastrowid

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_cache_just_below_ttl_is_reused_without_network(self, fetch, _now):
        row_id = self._seed(age=timedelta(hours=1) - timedelta(seconds=1))

        result = get_or_fetch_market_temperature()

        fetch.assert_not_called()
        self.assertEqual(result.id, row_id)
        self.assertEqual(result.temperature, 45.0)

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_cache_at_ttl_is_expired(self, fetch, _now):
        row_id = self._seed(age=timedelta(hours=1))
        fetch.return_value = MarketTemperature(45.0, "正常", "2026-07-11T15:00")

        result = get_or_fetch_market_temperature()

        fetch.assert_called_once_with(timeout=10.0)
        self.assertEqual(result.id, row_id)

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_cache_older_than_ttl_fetches_once_and_refreshes_same_input(self, fetch, _now):
        row_id = self._seed(age=timedelta(hours=1, seconds=1))
        fetch.return_value = MarketTemperature(45.0, "正常", "2026-07-11T15:00")

        result = get_or_fetch_market_temperature()

        fetch.assert_called_once_with(timeout=10.0)
        self.assertEqual(result.id, row_id)
        row = db._TEST_CONN.execute("SELECT * FROM market_temperatures").fetchone()
        self.assertEqual(row["fetched_at"], "2026-07-12T12:00:00")
        self.assertEqual(row["temperature"], 45.0)

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_cache_ignores_fresher_rows_from_untrusted_sources(self, fetch, _now):
        official_id = self._seed(age=timedelta(minutes=10))
        self._seed(
            age=timedelta(minutes=1),
            temperature=99.0,
            source="https://example.invalid/temperature",
            updated_at="2026-07-12T11:59",
        )

        result = get_or_fetch_market_temperature()

        fetch.assert_not_called()
        self.assertEqual(result.id, official_id)
        self.assertEqual(result.source, DATA_URL)

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_invalid_fresh_cached_value_is_not_returned(self, fetch, _now):
        self._seed(age=timedelta(minutes=1), temperature=101.0)

        with self.assertRaisesRegex(TemperatureFetchError, "0.*100"):
            get_or_fetch_market_temperature()

        fetch.assert_not_called()

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_malformed_cached_temperature_raises_fetch_error_not_value_error(self, fetch, _now):
        self._seed(age=timedelta(minutes=1), temperature="bad")

        with self.assertRaisesRegex(TemperatureFetchError, "温度无效"):
            get_or_fetch_market_temperature()

        fetch.assert_not_called()

    def test_database_rejects_malformed_temperature(self):
        with self.assertRaises(ValueError):
            db.insert_market_temperature(
                temperature="bad",
                label="正常",
                source_updated_at="2026-07-11T15:00",
                source=DATA_URL,
            )

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_cached_offset_timestamp_is_canonicalized_before_return(self, fetch, _now):
        self._seed(
            age=timedelta(minutes=1),
            updated_at="2026-07-12T03:30:45+00:00",
        )

        result = get_or_fetch_market_temperature()

        fetch.assert_not_called()
        self.assertEqual(result.updated_at, "2026-07-12T11:30")

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_expired_cache_and_fetch_failure_uses_recent_official_snapshot(self, fetch, _now):
        self._seed(age=timedelta(hours=2))
        fetch.side_effect = TemperatureFetchError("offline")

        result = get_or_fetch_market_temperature()

        fetch.assert_called_once_with(timeout=10.0)
        self.assertEqual(result.temperature, 45.0)

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_expired_cache_beyond_fallback_window_still_fails_closed(self, fetch, _now):
        self._seed(age=timedelta(days=3, seconds=1))
        fetch.side_effect = TemperatureFetchError("offline")

        with self.assertRaisesRegex(TemperatureFetchError, "offline"):
            get_or_fetch_market_temperature()

        fetch.assert_called_once_with(timeout=10.0)

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_explicit_refresh_bypasses_recent_failure_backoff(self, fetch, _now):
        self._seed(age=timedelta(hours=2))
        fetch.side_effect = [
            TemperatureFetchError("offline"),
            MarketTemperature(45.0, "正常", "2026-07-11T15:00"),
        ]
        cached = get_or_fetch_market_temperature()

        result = get_or_fetch_market_temperature(refresh=True)

        self.assertEqual(cached.temperature, 45.0)
        self.assertEqual(result.temperature, 45.0)
        self.assertEqual(fetch.call_count, 2)

    def test_malformed_html_is_rejected(self):
        with self.assertRaises(TemperatureFetchError):
            parse_market_temperature("<html><body>no market data</body></html>")

    def test_temperature_outside_zero_to_one_hundred_is_rejected(self):
        html = "全市场温度 101° 过热 温度更新时间：2026年7月12日 11:30"
        with self.assertRaisesRegex(TemperatureFetchError, "0.*100"):
            parse_market_temperature(html)

    def test_invalid_source_timestamp_is_rejected(self):
        html = "全市场温度 45° 正常 温度更新时间：2026年2月30日 11:30"
        with self.assertRaisesRegex(TemperatureFetchError, "时间"):
            parse_market_temperature(html)

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_offset_source_timestamp_is_normalized_to_naive_shanghai_minutes(self, fetch, _now):
        fetch.return_value = MarketTemperature(
            45.0, "正常", "2026-07-12T03:30:45+00:00"
        )

        result = get_or_fetch_market_temperature(refresh=True)

        self.assertEqual(result.updated_at, "2026-07-12T11:30")
        row = db._TEST_CONN.execute("SELECT source_updated_at FROM market_temperatures").fetchone()
        self.assertEqual(row["source_updated_at"], "2026-07-12T11:30")

    @patch("datasource.youzhiyouxing._now_shanghai", return_value=NOW)
    @patch("datasource.youzhiyouxing.fetch_market_temperature")
    def test_same_source_timestamp_with_changed_data_is_a_conflict(self, fetch, _now):
        for temperature, label in ((46.0, "正常"), (45.0, "偏热")):
            with self.subTest(temperature=temperature, label=label):
                db._TEST_CONN.execute("DELETE FROM market_temperatures")
                row_id = self._seed(age=timedelta(hours=2))
                fetch.return_value = MarketTemperature(
                    temperature, label, "2026-07-11T15:00"
                )

                with self.assertRaisesRegex(TemperatureFetchError, "source conflict"):
                    get_or_fetch_market_temperature()

                row = db._TEST_CONN.execute(
                    "SELECT * FROM market_temperatures WHERE id=?", (row_id,)
                ).fetchone()
                self.assertEqual((row["temperature"], row["label"]), (45.0, "正常"))

    def test_concurrent_duplicate_same_value_refresh_is_safe(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "temperature.db"
            with patch.object(db, "DB_PATH", database):
                db.init_db()

                def save():
                    return db.insert_market_temperature(
                        temperature=45.0,
                        label="正常",
                        source_updated_at="2026-07-11T15:00",
                        source=DATA_URL,
                        fetched_at="2026-07-12T12:00:00",
                    )["id"]

                with ThreadPoolExecutor(max_workers=2) as pool:
                    ids = list(pool.map(lambda _: save(), range(2)))

                conn = sqlite3.connect(database)
                try:
                    count = conn.execute("SELECT COUNT(*) FROM market_temperatures").fetchone()[0]
                finally:
                    conn.close()

        self.assertEqual(ids[0], ids[1])
        self.assertEqual(count, 1)
        db._TEST_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row

    def test_concurrent_stale_cache_refresh_fetches_once_across_processes(self):
        import multiprocessing

        global _PROCESS_FETCH_COUNT, _PROCESS_FETCH_FAILS
        db._TEST_CONN.close()
        db._TEST_CONN = None
        context = multiprocessing.get_context("fork")
        _PROCESS_FETCH_COUNT = context.Value("i", 0)
        _PROCESS_FETCH_FAILS = False
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "temperature.db"
            with patch.object(db, "DB_PATH", database):
                db.init_db()
                db.insert_market_temperature(
                    temperature=45.0,
                    label="正常",
                    source_updated_at="2026-07-11T15:00",
                    source=DATA_URL,
                    fetched_at="2026-07-12T10:00:00",
                )

            start = context.Event()
            results = context.Queue()
            processes = [
                context.Process(
                    target=_refresh_in_process,
                    args=(str(database), start, results),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            start.set()
            outcomes = [results.get(timeout=5) for _ in processes]
            for process in processes:
                process.join(timeout=5)

        self.assertEqual(outcomes[0][0], "ok")
        self.assertEqual(outcomes[1][0], "ok")
        self.assertEqual(outcomes[0][1], outcomes[1][1])
        self.assertEqual(_PROCESS_FETCH_COUNT.value, 1)
        db._TEST_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row

    def test_concurrent_failed_refresh_uses_cached_snapshot_across_processes(self):
        import multiprocessing

        global _PROCESS_FETCH_COUNT, _PROCESS_FETCH_FAILS
        db._TEST_CONN.close()
        db._TEST_CONN = None
        context = multiprocessing.get_context("fork")
        _PROCESS_FETCH_COUNT = context.Value("i", 0)
        _PROCESS_FETCH_FAILS = True
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "temperature.db"
            with patch.object(db, "DB_PATH", database):
                db.init_db()
                db.insert_market_temperature(
                    temperature=45.0,
                    label="正常",
                    source_updated_at="2026-07-11T15:00",
                    source=DATA_URL,
                    fetched_at="2026-07-12T10:00:00",
                )

            start = context.Event()
            results = context.Queue()
            processes = [
                context.Process(
                    target=_refresh_in_process,
                    args=(str(database), start, results),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            start.set()
            outcomes = [results.get(timeout=5) for _ in processes]
            for process in processes:
                process.join(timeout=5)

        self.assertEqual([outcome[0] for outcome in outcomes], ["ok", "ok"])
        self.assertEqual(outcomes[0][1], outcomes[1][1])
        self.assertEqual(_PROCESS_FETCH_COUNT.value, 1)
        db._TEST_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row


if __name__ == "__main__":
    unittest.main()
