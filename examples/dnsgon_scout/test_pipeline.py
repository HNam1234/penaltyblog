"""Synthetic end-to-end data contracts; never uses live API or Telegram."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adapters import FeedResult, ProviderError
from football import final_results, normalize
from publisher import DeliveryError, flush, send
from scout import scan
from store import Store


NOW = datetime(2026, 10, 25, 7, tzinfo=timezone.utc)
TOKEN = "123456789:" + "x" * 30  # Nonfunctional test token; no network is used.
CHANNEL = "-1001234567890"


def fixture(status="NS", start=None, identity=101):
    return {"fixture": {"id": identity, "date": (start or NOW + timedelta(hours=1)).isoformat(), "status": {"short": status}},
            "teams": {"home": {"name": "Synthetic Home"}, "away": {"name": "Synthetic Away"}},
            "league": {"id": 39, "name": "Synthetic League"}, "score": {"fulltime": {"home": 2, "away": 1}}}


def odds(as_of=None):
    prices = [(1.4, 6, 8), (1.2, 8, 14), (1.21, 8.2, 14.2), (1.19, 8.1, 14.1)]
    books = []
    for index, values in enumerate(prices, start=1):
        books.append({"id": index, "name": f"Synthetic Book {index}", "bets": [
            {"id": 1, "name": "Match Winner", "values": [{"value": name, "odd": str(price)} for name, price in zip(("Home", "Draw", "Away"), values)]}]})
    return {"fixture": {"id": 101}, "update": (as_of or NOW - timedelta(minutes=1)).isoformat(), "bookmakers": books}


def rules():
    return {str(i): {"period": "regulation", "evidence_url": "https://example.invalid/reviewed-fixture-rules",
                     "reviewed_at": (NOW - timedelta(days=1)).isoformat()} for i in range(1, 5)}


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.catalog = [{"league": {"id": 39}, "seasons": [{"year": 2026, "current": True, "coverage": {"odds": True}}]}]
        self.matches = [fixture()]
        self.odds_rows = [odds()]
        self.fetched_at = NOW + timedelta(seconds=2)
        self.quota = {"daily_remaining": 99}
        self.error = None
        self.after_catalog = None

    def response(self, method, rows):
        self.calls.append(method)
        if self.error:
            raise self.error
        if method == "leagues" and self.after_catalog:
            self.after_catalog()
        return FeedResult({"errors": [], "response": deepcopy(rows), "paging": {"current": 1, "total": 1}},
                          dict(self.quota), self.fetched_at.isoformat(), "api_sports_football")

    def leagues(self, **kwargs):
        return self.response("leagues", self.catalog)

    def fixtures(self, day, **kwargs):
        return self.response("fixtures:" + day, self.matches if day == NOW.date().isoformat() else [])

    def odds(self, day, **kwargs):
        return self.response("odds", self.odds_rows)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "scout.sqlite3"
        self.store = Store(self.path)
        self.api = FakeAPI()
        self.config = {"mode": "api_football", "league_ids": [39], "book_rules": rules()}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def scan(self, **kwargs):
        return scan(self.config, self.store, now=NOW, api=self.api, clock=lambda: NOW, **kwargs)

    def test_blocked_plan_uses_no_api_key_file_or_network(self):
        result = scan({"mode": "blocked_plan", "api_key_file": "missing-file", "blocked_reason": "Plan restriction"}, self.store,
                      now=NOW, api=self.api)
        self.assertEqual(result["status"], "blocked_plan")
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.store.predictions(), [])
        self.assertIn("chưa có đề xuất", self.store.next_message(NOW)["text"].lower())
        scan({"mode": "blocked_plan"}, self.store, now=NOW, api=self.api)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM scout_outbox").fetchone()[0], 1)

    def test_valid_pipeline_keeps_real_decision_time_and_immutable_forecast(self):
        result = self.scan()
        self.assertGreater(result["new_forecasts"], 0)
        first = self.store.predictions()[0]
        self.assertEqual(first["created_at"], "2026-10-25T07:00:02Z")
        self.assertEqual(first["label"], "chưa kiểm định")
        self.assertTrue(first["event_id"].startswith("api_football:"))
        self.assertTrue(all(book.startswith("api_football_book:") for book in first["reference_books"]))
        self.assertEqual(self.store.request_count("api_football", "2026-10-25"), len(self.api.calls))
        self.api.odds_rows[0]["bookmakers"][0]["bets"][0]["values"][0]["odd"] = "1.5"
        repeat = self.scan()
        self.assertEqual(repeat["new_forecasts"], 0)
        self.assertEqual(self.store.predictions()[0], first)

    def test_card_expires_at_oldest_quote_deadline(self):
        self.scan()
        message = self.store.next_message(NOW)
        self.assertEqual(message["expires_at"], "2026-10-25T07:04:00.000000Z")
        self.assertIsNone(self.store.next_message("2026-10-25T07:04:00Z"))

    def test_card_expires_at_kickoff_if_earlier_than_quote_deadline(self):
        self.api.matches = [fixture(start=NOW + timedelta(seconds=30))]
        self.scan()
        self.assertEqual(self.store.next_message(NOW)["expires_at"], "2026-10-25T07:00:30.000000Z")

    def test_stale_or_future_odds_never_become_forecasts(self):
        for as_of in (NOW - timedelta(minutes=6), NOW + timedelta(seconds=20)):
            self.api.odds_rows = [odds(as_of)]
            with self.subTest(as_of=as_of):
                self.assertEqual(self.scan()["new_forecasts"], 0)
                self.assertEqual(self.store.predictions(), [])

    def test_unreviewed_rules_or_other_market_id_not_silently_used(self):
        self.config["book_rules"] = {}
        self.assertEqual(self.scan()["new_forecasts"], 0)
        self.config["book_rules"] = rules()
        for book in self.api.odds_rows[0]["bookmakers"]:
            book["bets"][0]["id"] = 2
        self.assertEqual(self.scan()["new_forecasts"], 0)

    def test_provider_failure_still_consumes_local_attempt(self):
        self.api.error = ProviderError("network_unavailable")
        result = self.scan()
        self.assertEqual(result["status"], "network_unavailable")
        self.assertEqual(result["network_requests"], 1)
        self.assertEqual(self.store.request_count("api_football", "2026-10-25"), 1)

    def test_low_vendor_quota_stops_after_current_attempt(self):
        self.api.quota = {"daily_remaining": 5}
        result = self.scan()
        self.assertEqual(result["status"], "provider_quota_reserve_reached")
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM scout_snapshots").fetchone()[0], 1)

    def test_malformed_nested_catalog_is_a_recorded_error(self):
        for catalog in ([None], [{"league": {"id": 39}, "seasons": [None]}]):
            self.api.catalog = catalog
            with self.subTest(catalog=catalog):
                result = self.scan()
                self.assertEqual(result["status"], "invalid_or_unavailable_data")
                self.assertEqual(self.store.latest_run()["status"], "invalid_or_unavailable_data")

    def test_utc_midnight_uses_actual_attempt_day_for_budget(self):
        started = datetime(2026, 10, 25, 23, 59, 59, tzinfo=timezone.utc)
        actual = [started]
        self.api.after_catalog = lambda: actual.__setitem__(0, started + timedelta(seconds=2))
        for _ in range(79):
            self.store.reserve_request("api_football", "2026-10-26")
        result = scan(self.config, self.store, now=started, api=self.api, clock=lambda: actual[0])
        self.assertEqual(result["status"], "local_daily_budget_exhausted")
        self.assertEqual(self.store.request_count("api_football", "2026-10-25"), 1)
        self.assertEqual(self.store.request_count("api_football", "2026-10-26"), 80)
        self.assertEqual(len(self.api.calls), 2)

    def test_conflicting_duplicate_book_contract_not_first_wins(self):
        original = odds()
        conflicting = deepcopy(original["bookmakers"][0])
        conflicting["bets"][0]["values"][0]["odd"] = "1.9"
        original["bookmakers"].append(conflicting)
        events, rejected = normalize([fixture()], [original], rules())
        self.assertFalse(any(row["bookmaker"] == "api_football_book:1" for row in events[0]["markets"]))
        self.assertTrue(any(value > 0 for value in rejected.values()))

    def test_fulltime_result_uses_observation_not_kickoff(self):
        observed = NOW + timedelta(hours=3)
        result = final_results([fixture("FT")], observed.isoformat())[0]
        self.assertEqual(result["settled_at"], observed.isoformat())
        self.assertEqual(result["known_at"], observed.isoformat())
        self.assertEqual(result["outcome"], "home")
        self.assertTrue(result["settlement_verified"])

    def test_no_result_from_kickoff_aet_pen_or_incomplete_score(self):
        for row, known in ((fixture("FT"), NOW + timedelta(hours=1)),
                           (fixture("AET"), NOW + timedelta(hours=4)),
                           (fixture("PEN"), NOW + timedelta(hours=4)),
                           (fixture("LIVE"), NOW + timedelta(hours=4))):
            with self.subTest(status=row["fixture"]["status"]):
                self.assertEqual(final_results([row], known.isoformat()), [])
        row = fixture("FT")
        row["score"]["fulltime"]["home"] = True
        self.assertEqual(final_results([row], (NOW + timedelta(hours=4)).isoformat()), [])

    def test_ambiguous_delivery_and_restart_never_resend(self):
        self.store.enqueue("one", "Synthetic card")
        calls = []
        def unknown(*args):
            calls.append(args)
            raise DeliveryError("timeout", uncertain=True)
        self.assertEqual(flush(self.store, TOKEN, CHANNEL, NOW, sender=unknown, clock=lambda: NOW), 0)
        self.store.close()
        self.store = Store(self.path)
        self.store.recover_sending()
        self.assertEqual(flush(self.store, TOKEN, CHANNEL, NOW, sender=unknown, clock=lambda: NOW), 0)
        self.assertEqual(len(calls), 1)

    def test_later_card_expired_while_first_sent_is_not_delivered(self):
        actual = [NOW]
        self.store.enqueue("first", "First")
        self.store.enqueue("expiring", "Second", (NOW + timedelta(seconds=1)).isoformat())
        calls = []
        def delayed(token, channel, source):
            calls.append(source)
            actual[0] += timedelta(seconds=2)
            return 17
        self.assertEqual(flush(self.store, TOKEN, CHANNEL, NOW, sender=delayed, clock=lambda: actual[0]), 1)
        self.assertEqual(calls, ["First"])

    def test_crash_after_claim_is_held_not_retried(self):
        self.store.enqueue("one", "Synthetic")
        self.store.claim("one")
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.recover_sending(), 1)
        with patch("publisher.send") as sender:
            self.assertEqual(flush(self.store, TOKEN, CHANNEL, NOW, sender=sender, clock=lambda: NOW), 0)
            sender.assert_not_called()


class PublisherSchemaTests(unittest.TestCase):
    def send_response(self, data):
        with patch("publisher.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(data).encode())
            return send(TOKEN, CHANNEL, "Synthetic offline card")

    def test_positive_integer_message_id_only(self):
        self.assertEqual(self.send_response({"ok": True, "result": {"message_id": 17}}), 17)
        for identity in (True, False, 0, -1, "17", None):
            with self.subTest(identity=identity), self.assertRaises(DeliveryError) as error:
                self.send_response({"ok": True, "result": {"message_id": identity}})
            self.assertTrue(error.exception.uncertain)

    def test_malformed_telegram_success_is_uncertain(self):
        for data in ([], None, {"ok": True, "result": None}, {"ok": True, "result": []}, {"ok": False}):
            with self.subTest(data=data), self.assertRaises(DeliveryError) as error:
                self.send_response(data)
            self.assertTrue(error.exception.uncertain)


if __name__ == "__main__":
    unittest.main()
