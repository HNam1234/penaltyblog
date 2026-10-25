from copy import deepcopy
from datetime import datetime
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from adapters import ProviderError
from direct import CLAUSE, Polymarket, moneyline_markets, watch_event


NOW = "2026-10-25T06:24:00+00:00"
START = "2026-10-25T14:00:00Z"


def event():
    return {"id": "501", "title": "Club H vs Club A", "active": True, "closed": False, "startTime": START,
            "markets": [{"id": str(101 + i), "question": q, "sportsMarketType": "moneyline", "version": "v1",
                "active": True, "closed": False, "acceptingOrders": True, "gameStartTime": START,
                "description": CLAUSE, "outcomes": '["Yes","No"]', "clobTokenIds": json.dumps([str(1001 + 2*i), str(1002 + 2*i)]),
                "conditionId": "condition" + str(i), "feesEnabled": True, "makerBaseFee": 1000, "takerBaseFee": 1000}
                for i, q in enumerate(("Club H to win?", "Draw?", "Club A to win?"))]}


def books():
    return {str(1001 + 2*i): {"asset_id": str(1001 + 2*i), "market": "condition" + str(i),
                "timestamp": str(int(datetime.fromisoformat(NOW).timestamp() * 1000)),
                "bids": [{"price": "0.20", "size": "500"}, {"price": "0.32", "size": "20"}],
                "asks": [{"price": "0.8", "size": "200"}, {"price": "0.33", "size": "5.25"}],
                "min_order_size": "5", "hash": "public-book-state"} for i in range(3)}


class DirectTests(unittest.TestCase):
    def test_actual_gamma_hour_only_timezone_on_python310(self):
        row = event()
        for market in row['markets']:
            market['gameStartTime'] = '2026-10-25 14:00:00+00'
        self.assertEqual(len(moneyline_markets(row, NOW)), 3)

    def test_client_events_fixed_host_read_only_and_bounded(self):
        with patch("direct._read_json", return_value=([], {})) as read:
            result = Polymarket().events(10188)
        url, headers, timeout, size = read.call_args.args
        self.assertEqual((urlsplit(url).scheme, urlsplit(url).hostname, urlsplit(url).path), ("https", "gamma-api.polymarket.com", "/events"))
        self.assertEqual(parse_qs(urlsplit(url).query)["series_id"], ["10188"])
        self.assertEqual(headers, {})
        self.assertEqual((timeout, size), (15, 2_000_000))
        self.assertTrue(result.partial)

    def test_client_book_fixed_token_and_schema(self):
        with patch("direct._read_json", return_value=(books()["1001"], {})) as read:
            result = Polymarket().book("1001")
        self.assertEqual(read.call_args.args[0], "https://clob.polymarket.com/book?token_id=1001")
        self.assertEqual(read.call_args.args[1], {})
        self.assertEqual(result.data["asset_id"], "1001")
        with patch("direct._read_json", return_value=(books()["1001"], {})):
            with self.assertRaises(ProviderError):
                Polymarket().book("1003")

    def test_exact_fixture_lookup_is_bounded_and_excludes_unrequested_events(self):
        slugs = ['epl-liv-bri-2026-10-25','epl-mun-bou-2026-10-25']
        with patch('direct._read_json',return_value=([{'slug':slugs[0]}],{})) as read:
            result = Polymarket().events_by_slugs(slugs)
        self.assertEqual(parse_qs(urlsplit(read.call_args.args[0]).query)['slug'],slugs)
        self.assertTrue(result.partial)
        with patch('direct._read_json',return_value=([{'slug':'unrequested'}],{})):
            with self.assertRaises(ProviderError): Polymarket().events_by_slugs(slugs)
        for bad in ([],['https://evil.invalid'],slugs*11):
            with self.assertRaises(ValueError): Polymarket().events_by_slugs(bad)

    def test_input_cannot_control_host_path_or_add_auth(self):
        for value in (True, "../../wallet", "123&auth=secret", "https://evil.invalid", 0):
            with patch("direct._read_json") as read:
                with self.assertRaises(ValueError):
                    Polymarket().book(value)
                read.assert_not_called()
        for bad in (0, 21, True):
            with self.assertRaises(ValueError):
                Polymarket().events(10188, bad)

    def test_quotes_are_three_binary_questions_one_venue_not_picks(self):
        result = watch_event(event(), books(), NOW)
        self.assertEqual(result["status"], "quotes_available")
        self.assertEqual(len(result["quotes"]), 3)
        self.assertEqual(result["independent_venues"], 1)
        self.assertFalse(result["recommendation"])
        self.assertNotIn("home", result)
        self.assertNotIn("probability", result["quotes"][0])
        self.assertIsNone(result["quotes"][0]["fee_rate"])
        self.assertTrue(result["quotes"][0]["fees_enabled"])

    def test_bid_max_ask_min_not_array_order_and_keep_depth_size(self):
        quote = watch_event(event(), books(), NOW)["quotes"][0]
        self.assertEqual(quote["best_bid"], {"price": "0.32", "shares": "20"})
        self.assertEqual(quote["best_ask"], {"price": "0.33", "shares": "5.25"})

    def test_no_metadata_midpoint_fallback_for_missing_book(self):
        row = event()
        row["markets"][0]["outcomePrices"] = '["0.95","0.05"]'
        result = watch_event(row, {}, NOW)
        self.assertEqual(result["quotes"], [])
        self.assertEqual(result["status"], "quotes_unavailable")
        self.assertEqual(result["skipped"][0]["reason"], "missing_book")

    def test_only_exact_reviewed_90_minute_clause(self):
        row = event()
        row["markets"][0]["description"] = "First 45 minutes of play"
        self.assertEqual(watch_event(row, books(), NOW)["quotes"], [])
        row = event()
        row["markets"][0]["sportsMarketType"] = "soccer_halftime_result"
        self.assertEqual(watch_event(row, books(), NOW)["quotes"], [])

    def test_freshness_comes_from_book_milliseconds_not_gamma_updated_at(self):
        data = books()
        data["1001"]["timestamp"] = str(int(datetime.fromisoformat(NOW).timestamp() * 1000) - 301_000)
        row = event()
        row["markets"][0]["updatedAt"] = NOW
        result = watch_event(row, data, NOW)
        self.assertEqual(len(result["quotes"]), 2)
        self.assertEqual(result["skipped"][0]["reason"], "stale_future_or_post_start_book")

    def test_wrong_tokens_conditions_and_future_timestamp_skipped(self):
        for field, value in (("asset_id", "999"), ("market", "other"), ("timestamp", "1792913040000")):
            data = books()
            data["1001"][field] = value
            self.assertEqual(len(watch_event(event(), data, NOW)["quotes"]), 2)

    def test_no_ask_is_explicit_no_liquidity(self):
        data = books()
        data["1001"]["asks"] = []
        quote = watch_event(event(), data, NOW)["quotes"][0]
        self.assertIsNone(quote["best_ask"])
        self.assertEqual(quote["liquidity_status"], "no_ask_liquidity")

    def test_invalid_decimal_crossed_books_or_terminal_prices_skip(self):
        for price in ("NaN", "Infinity", "0", "1", "1.1", 0.33):
            data = books()
            data["1001"]["asks"][1]["price"] = price
            self.assertEqual(len(watch_event(event(), data, NOW)["quotes"]), 2)
        data = books()
        data["1001"]["asks"][1]["price"] = "0.3"
        self.assertEqual(watch_event(event(), data, NOW)["skipped"][0]["reason"], "crossed_orderbook")

    def test_v2_uses_position_id_and_outcome_order(self):
        row = event()
        for i, market in enumerate(row["markets"]):
            market.update(version="v2", outcomes='["No","Yes"]', positionIds=[str(1002+2*i), str(1001+2*i)])
            market["clobTokenIds"] = '["9991","9992"]'
        self.assertEqual(len(watch_event(row, books(), NOW)["quotes"]), 3)

    def test_duplicate_or_incomplete_questions_not_full_group(self):
        row = event()
        row["markets"][1]["question"] = row["markets"][0]["question"]
        self.assertEqual(watch_event(row, books(), NOW)["quotes"], [])
        row = event()
        row["markets"].pop()
        self.assertEqual(watch_event(row, books(), NOW)["quotes"], [])

    def test_actual_kickoff_not_market_creation_or_end_date(self):
        row = event()
        row["startDate"] = "2020-01-01T00:00:00Z"
        row["endDate"] = "2020-01-01T00:00:00Z"
        self.assertEqual(len(watch_event(row, books(), NOW)["quotes"]), 3)
        row["startTime"] = "2026-10-25T15:00:00Z"
        self.assertEqual(watch_event(row, books(), NOW)["quotes"], [])


if __name__ == "__main__":
    unittest.main()
