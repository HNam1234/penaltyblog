"""Persistence and at-most-once outbox tests; no network or real money."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from store import SCHEMA_VERSION, Store


NOW = "2026-10-25T07:00:00Z"


def settled(**changes):
    return {"event_id": "fixture", "sport": "soccer", "market": "1x2", "period": "regulation", "line": None,
            "outcome": "home", "status": "final", "settled_at": NOW, "known_at": NOW,
            "settlement_verified": True, **changes}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sports.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_forecast_first_copy_is_immutable_and_persisted(self):
        candidate = {"id": "same", "probability": 0.8, "label": "chưa kiểm định"}
        self.assertTrue(self.store.forecast(candidate))
        candidate["probability"] = 0.99
        self.assertFalse(self.store.forecast(candidate))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.predictions()[0]["probability"], 0.8)
        received = self.store.predictions()
        received[0]["probability"] = 0.1
        self.assertEqual(self.store.predictions()[0]["probability"], 0.8)

    def test_snapshot_content_identity_and_immutable_rows(self):
        first = self.store.snapshot("fixture-provider", {"odds": [1.5, 3]}, NOW)
        self.assertEqual(first, self.store.snapshot("fixture-provider", {"odds": [1.5, 3]}, "2026-10-25T14:00:00+07:00"))
        other = self.store.snapshot("fixture-provider", {"odds": [1.6, 3]}, NOW)
        self.assertNotEqual(first, other)
        self.assertNotEqual(first, self.store.snapshot("other-provider", {"odds": [1.5, 3]}, NOW))
        row = self.store.db.execute("SELECT provider,fetched_at,body FROM scout_snapshots WHERE id=?", (first,)).fetchone()
        self.assertEqual(row["provider"], "fixture-provider")
        self.assertEqual(json.loads(row["body"]), {"odds": [1.5, 3]})

    def test_invalid_json_is_never_written(self):
        for invalid in ({"bad": float("nan")}, {"bad": float("inf")}, {"bad": {1, 2}}, {"bad": object()}, {"bad": "\ud800"}):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(ValueError):
                self.store.snapshot("fixture", invalid, NOW)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM scout_snapshots").fetchone()[0], 0)

    def test_serialized_utf8_limits(self):
        with self.assertRaisesRegex(ValueError, "json_too_large"):
            self.store.forecast({"id": "too-large", "data": "x" * 32768})
        with self.assertRaisesRegex(ValueError, "json_too_large"):
            self.store.snapshot("fixture", {"data": "x" * (2 * 1024 * 1024)}, NOW)
        with self.assertRaisesRegex(ValueError, "json_too_large"):
            self.store.forecast({"id": "unicode-too-large", "data": "ộ" * 16000})
        self.assertTrue(self.store.forecast({"id": "fits", "data": "x" * 32000}))

    def test_snapshot_near_limit_with_escaped_characters(self):
        payload = {"data": '"' * 900000}
        identity = self.store.snapshot("fixture", payload, NOW)
        self.assertEqual(len(identity), 64)

    def test_result_first_payload_cannot_be_rewritten(self):
        self.assertTrue(self.store.result(settled(known_at="2026-10-25T07:01:00Z")))
        self.assertFalse(self.store.result(settled(known_at="2026-10-25T09:00:00Z")))
        self.assertEqual(self.store.results()[0]["known_at"], "2026-10-25T07:01:00Z")

    def test_conflicting_result_is_preserved_for_ambiguous_scoring(self):
        self.assertTrue(self.store.result(settled()))
        self.assertTrue(self.store.result(settled(outcome="away")))
        self.assertEqual({row["outcome"] for row in self.store.results()}, {"home", "away"})
        self.assertTrue(self.store.result(settled(period="qualification")))
        self.assertEqual(len(self.store.results()), 3)

    def test_verification_is_not_invented_from_truthy_values(self):
        for index, value in enumerate((None, "true", 1, {}, False, True)):
            self.store.result(settled(event_id=f"fixture-{index}", settlement_verified=value))
        flags = [row["settlement_verified"] for row in self.store.results()]
        self.assertEqual(flags, [False, False, False, False, False, True])

    def test_result_missing_contract_or_invalid_line_rejected(self):
        for changes in ({"event_id": ""}, {"period": None}, {"line": "2.5"}, {"line": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.result(settled(**changes))

    def test_latest_run_persisted(self):
        self.assertIsNone(self.store.latest_run())
        self.store.record_run("no_candidates", {"count": 0}, NOW)
        self.store.record_run("provider_error", {"code": "timeout"}, "2026-10-25T07:01:00Z")
        self.store.close()
        self.store = Store(self.path)
        last = self.store.latest_run()
        self.assertEqual(last["status"], "provider_error")
        self.assertEqual(last["detail"], {"code": "timeout"})

    def test_outbox_enqueue_idempotent_immutable_fifo(self):
        self.assertTrue(self.store.enqueue("one", "First"))
        self.assertFalse(self.store.enqueue("one", "Changed", expires_at="2026-10-25T08:00:00Z"))
        self.assertTrue(self.store.enqueue("two", "Second"))
        self.assertEqual(self.store.next_message(NOW), {"key": "one", "text": "First", "expires_at": None, "state": "pending"})
        self.assertTrue(self.store.claim("one"))
        self.assertEqual(self.store.next_message(NOW)["key"], "two")

    def test_expired_message_never_selected_including_exact_boundary(self):
        self.store.enqueue("expired", "Old", expires_at=NOW)
        self.store.enqueue("future", "New", expires_at="2026-10-25T14:00:01+07:00")
        self.assertEqual(self.store.next_message(NOW)["key"], "future")
        self.assertIsNone(self.store.next_message("2026-10-25T07:00:01Z"))

    def test_claim_is_atomic_across_connections(self):
        self.store.enqueue("one", "One")
        other = Store(self.path)
        try:
            self.assertTrue(self.store.claim("one"))
            self.assertFalse(other.claim("one"))
        finally:
            other.close()

    def test_sent_is_terminal_and_cannot_be_overwritten(self):
        self.store.enqueue("one", "One")
        self.assertFalse(self.store.mark_sent("one", 1))
        self.assertTrue(self.store.claim("one"))
        self.assertTrue(self.store.mark_sent("one", 17))
        self.assertFalse(self.store.mark_sent("one", 18))
        self.assertFalse(self.store.mark_uncertain("one"))
        self.assertFalse(self.store.claim("one"))
        self.assertIsNone(self.store.next_message(NOW))
        row = self.store.db.execute("SELECT state,message_id FROM scout_outbox WHERE key='one'").fetchone()
        self.assertEqual(tuple(row), ("sent", 17))

    def test_timeout_uncertain_is_never_retried(self):
        self.store.enqueue("one", "One")
        self.store.claim("one")
        self.assertTrue(self.store.mark_uncertain("one"))
        self.assertFalse(self.store.claim("one"))
        self.assertIsNone(self.store.next_message(NOW))

    def test_failed_is_terminal(self):
        self.store.enqueue("one", "One")
        self.store.claim("one")
        self.assertTrue(self.store.mark_failed("one", "telegram_rejected"))
        self.assertFalse(self.store.claim("one"))
        self.assertFalse(self.store.mark_sent("one", 17))
        self.assertIsNone(self.store.next_message(NOW))

    def test_recovery_after_crash_does_not_resend(self):
        self.store.enqueue("in-flight", "May have been delivered")
        self.store.enqueue("untouched", "Not attempted")
        self.store.claim("in-flight")
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.recover_sending(), 1)
        self.assertEqual(self.store.recover_sending(), 0)
        self.assertFalse(self.store.claim("in-flight"))
        self.assertEqual(self.store.next_message(NOW)["key"], "untouched")

    def test_reader_open_does_not_recover_another_active_publisher(self):
        self.store.enqueue("one", "One")
        self.store.claim("one")
        other = Store(self.path)
        try:
            self.assertIsNone(other.next_message(NOW))
            self.assertTrue(self.store.mark_sent("one", 17))
        finally:
            other.close()

    def test_invalid_metadata_fails_before_write(self):
        for action in (lambda: self.store.snapshot("fixture", {}, "2026-10-25T07:00:00"),
                       lambda: self.store.enqueue("", "message"), lambda: self.store.forecast({"id": None}),
                       lambda: self.store.mark_sent("x", True), lambda: self.store.mark_sent("x", 0)):
            with self.assertRaises(ValueError):
                action()

    def test_schema_wal_busytimeout_and_version(self):
        self.assertEqual(self.store.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.store.db.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        self.assertEqual(self.store.db.execute("PRAGMA synchronous").fetchone()[0], 2)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_different_application_database_rejected_without_wal_change(self):
        path = Path(self.temp.name) / "other.sqlite3"
        with closing(sqlite3.connect(path)) as database:
            database.execute("CREATE TABLE unrelated_ledger(id INTEGER PRIMARY KEY)")
            database.commit()
            self.assertEqual(database.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        with self.assertRaisesRegex(ValueError, "not_supported_scout_database"):
            Store(path)
        with closing(sqlite3.connect(path)) as database:
            self.assertEqual(database.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("unrelated_ledger",)])

    def test_unknown_schema_version_rejected(self):
        self.store.db.execute("PRAGMA user_version=999")
        with self.assertRaisesRegex(ValueError, "not_supported_scout_database"):
            Store(self.path)

    def test_default_quota_caps_at_80_and_persists(self):
        self.assertEqual(self.store.request_count("fixture", "2026-10-25"), 0)
        for _ in range(80):
            self.assertTrue(self.store.reserve_request("fixture", "2026-10-25"))
        self.assertFalse(self.store.reserve_request("fixture", "2026-10-25"))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.request_count("fixture", "2026-10-25"), 80)

    def test_quota_provider_and_day_separate(self):
        self.assertTrue(self.store.reserve_request("one", "2026-10-25", limit=1))
        self.assertFalse(self.store.reserve_request("one", "2026-10-25", limit=1))
        self.assertTrue(self.store.reserve_request("two", "2026-10-25", limit=1))
        self.assertTrue(self.store.reserve_request("one", "2026-10-26", limit=1))
        self.assertFalse(self.store.reserve_request("zero", "2026-10-25", limit=0))

    def test_quota_atomic_across_workers(self):
        def worker(_):
            local = Store(self.path)
            try:
                return sum(local.reserve_request("fixture", "2026-10-25", limit=7) for _ in range(10))
            finally:
                local.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(worker, range(4))), 7)
        self.assertEqual(self.store.request_count("fixture", "2026-10-25"), 7)

    def test_invalid_quota_limits_and_days_rejected(self):
        for limit in (-1, True, 1.5, "80"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.reserve_request("fixture", "2026-10-25", limit=limit)
        for day in ("2026-02-30", "20261025", "2026-10-25T00:00:00Z", None):
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.store.request_count("fixture", day)

    def test_two_credit_reservation_fails_without_consuming_last_credit(self):
        self.assertTrue(self.store.reserve_request("fixture", "2026-10-24", limit=1000, units=449))
        self.assertFalse(self.store.reserve_window("fixture", "2026-10-25", units=2))
        self.assertEqual(self.store.window_count("fixture", "2026-10-25"), 449)
        self.assertEqual(self.store.request_count("fixture", "2026-10-25"), 0)
        self.assertTrue(self.store.reserve_window("fixture", "2026-10-25", units=1))
        self.assertFalse(self.store.reserve_window("fixture", "2026-10-25", units=1))
        self.assertEqual(self.store.window_count("fixture", "2026-10-25"), 450)

    def test_window_budget_crosses_month_and_year_without_reset(self):
        self.store.reserve_request("fixture", "2026-12-31", limit=1000, units=449)
        self.assertFalse(self.store.reserve_window("fixture", "2027-01-01", units=2))
        self.assertTrue(self.store.reserve_window("fixture", "2027-01-01", units=1))
        self.assertEqual(self.store.window_count("fixture", "2027-01-01"), 450)
        self.assertEqual(self.store.request_count("fixture", "2027-01-01"), 1)

    def test_inclusive_31_day_window_ignores_expired_and_future_days(self):
        for day, units in (("2026-12-31", 9), ("2027-01-01", 10), ("2027-01-31", 20), ("2027-02-01", 40)):
            self.store.reserve_request("fixture", day, limit=1000, units=units)
        self.assertEqual(self.store.window_count("fixture", "2027-01-31"), 30)
        self.assertEqual(self.store.window_count("fixture", "2027-01-31", window_days=1), 20)
        self.assertTrue(self.store.reserve_window("fixture", "2027-01-31", limit=32, units=2))
        self.assertFalse(self.store.reserve_window("fixture", "2027-01-31", limit=32))
        self.assertEqual(self.store.window_count("fixture", "2027-01-31"), 32)

    def test_window_provider_separation_and_persistence(self):
        self.store.reserve_window("one", "2026-10-24", limit=10, units=10)
        self.assertTrue(self.store.reserve_window("two", "2026-10-25", limit=10, units=2))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.window_count("one", "2026-10-25"), 10)
        self.assertEqual(self.store.window_count("two", "2026-10-25"), 2)
        self.assertFalse(self.store.reserve_window("one", "2026-10-25", limit=10))

    def test_concurrent_window_reservations_cannot_cross_budget(self):
        self.store.reserve_request("fixture", "2026-10-24", limit=1000, units=443)
        def worker(_):
            local = Store(self.path)
            try:
                return sum(local.reserve_window("fixture", "2026-10-25", units=2) for _ in range(10))
            finally:
                local.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(worker, range(4))), 3)
        self.assertEqual(self.store.window_count("fixture", "2026-10-25"), 449)
        self.assertTrue(self.store.reserve_window("fixture", "2026-10-25"))
        self.assertEqual(self.store.window_count("fixture", "2026-10-25"), 450)

    def test_invalid_window_arguments_rejected(self):
        for days in (0, -1, True, 1.5, "31", 10**20):
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.store.window_count("fixture", "2026-10-25", window_days=days)
        with self.assertRaises(ValueError):
            self.store.window_count("fixture", "0001-01-01")
        for limit in (-1, True, 1.5, "450", 2**63):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.reserve_window("fixture", "2026-10-25", limit=limit)
        for units in (0, -1, True, 1.5, "2", 2**63):
            with self.subTest(units=units), self.assertRaises(ValueError):
                self.store.reserve_window("fixture", "2026-10-25", units=units)
        for day in ("2026-02-30", "20261025", "2026-10-25T00:00:00Z", None):
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.store.reserve_window("fixture", day)

    def test_daily_multi_unit_reservation_preserved(self):
        self.assertTrue(self.store.reserve_request("fixture", "2026-10-25", limit=5, units=4))
        self.assertFalse(self.store.reserve_request("fixture", "2026-10-25", limit=5, units=2))
        self.assertTrue(self.store.reserve_request("fixture", "2026-10-25", limit=5))
        self.assertEqual(self.store.request_count("fixture", "2026-10-25"), 5)


if __name__ == "__main__":
    unittest.main()
