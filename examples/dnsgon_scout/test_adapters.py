"""Offline contracts: fixed hosts, bounded reads, quota and honest partial data."""

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from adapters import APISportsFootball, ProviderError, TheOddsAPI, TheSportsDB, _NoRedirect


class Response(io.BytesIO):
    def __init__(self, data, headers=None, raw=None):
        super().__init__(raw if raw is not None else json.dumps(data).encode())
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.max_read = None

    def read(self, size=-1):
        self.max_read = size
        return super().read(size)


class AdapterTests(unittest.TestCase):
    def request(self, client, method, *args, data=None, headers=None, raw=None, **kwargs):
        response = Response(data, headers, raw)
        with patch("adapters.build_opener") as factory:
            factory.return_value.open.return_value = response
            result = getattr(client, method)(*args, **kwargs)
            request = factory.return_value.open.call_args.args[0]
            timeout = factory.return_value.open.call_args.kwargs["timeout"]
            self.assertIsInstance(factory.call_args.args[0], _NoRedirect)
        return result, request, response, timeout

    def test_theodds_sports_and_quota(self):
        result, req, response, timeout = self.request(TheOddsAPI("test-private"), "sports", data=[], headers={"x-requests-remaining": "500", "X-Requests-Used": "0"})
        self.assertEqual(result.quota, {"remaining": 500, "used": 0})
        self.assertEqual(urlsplit(req.full_url).hostname, "api.the-odds-api.com")
        self.assertEqual(urlsplit(req.full_url).scheme, "https")
        self.assertEqual(timeout, 10)
        self.assertEqual(response.max_read, 2_000_001)
        self.assertFalse(result.partial)

    def test_theodds_decimal_odds_one_region_and_iso_date(self):
        _, req, _, _ = self.request(TheOddsAPI("test-private"), "odds", "soccer_epl", data=[])
        query = parse_qs(urlsplit(req.full_url).query)
        self.assertEqual(query["regions"], ["eu"])
        self.assertEqual(query["markets"], ["h2h"])
        self.assertEqual(query["oddsFormat"], ["decimal"])
        self.assertEqual(query["dateFormat"], ["iso"])

    def test_scores_range(self):
        _, req, _, _ = self.request(TheOddsAPI("test-private"), "scores", "basketball_nba", days_from=2, data=[])
        self.assertEqual(parse_qs(urlsplit(req.full_url).query)["daysFrom"], ["2"])
        for bad in (0, 4, True, "2"):
            with self.assertRaises(ValueError):
                TheOddsAPI("test-private").scores("basketball_nba", bad)

    def test_untrusted_sport_never_reaches_network(self):
        for bad in ("../basketball_nba", "//evil.invalid", "soccer_epl?apiKey=leak", "soccer_epl\n", None):
            with patch("adapters.build_opener") as factory:
                with self.assertRaises(ValueError):
                    TheOddsAPI("test-private").odds(bad)
                factory.assert_not_called()

    def test_invalid_configuration_rejected_without_key_disclosure(self):
        for kwargs in ({"timeout": 0}, {"timeout": True}, {"timeout": float("inf")}, {"max_body_bytes": 0}, {"max_body_bytes": True}):
            with self.assertRaises(ValueError) as ctx:
                TheOddsAPI("test-private", **kwargs)
            self.assertNotIn("test-private", str(ctx.exception))
        for key in ("", None, "bad\r\nheader", "white space"):
            with self.assertRaises(ValueError):
                TheOddsAPI(key)

    def test_redirects_are_not_followed(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.invalid"))
        with patch("adapters.build_opener") as factory:
            factory.return_value.open.side_effect = HTTPError("https://example.invalid/?key=test-private", 302, "redirect", {"Location": "https://evil.invalid"}, io.BytesIO())
            with self.assertRaises(ProviderError) as ctx:
                TheOddsAPI("test-private").sports()
        self.assertEqual(ctx.exception.code, "redirect_refused")
        self.assertNotIn("test-private", str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)

    def test_http_errors_keep_safe_code_and_integer_quota_only(self):
        for status, code in ((401, "authentication_failed"), (403, "access_denied"), (429, "quota_exhausted"), (500, "http_error")):
            with patch("adapters.build_opener") as factory:
                factory.return_value.open.side_effect = HTTPError("https://example.invalid/?key=test-private", status, "secret", {"Retry-After": "60", "x-requests-used": "not-a-number"}, io.BytesIO(b"secret"))
                with self.assertRaises(ProviderError) as ctx:
                    TheOddsAPI("test-private").sports()
            self.assertEqual(ctx.exception.code, code)
            self.assertEqual(ctx.exception.status, status)
            self.assertEqual(ctx.exception.quota, {"retry_after_seconds": 60})
            self.assertNotIn("secret", str(ctx.exception))

    def test_network_error_never_exposes_request_url(self):
        with patch("adapters.build_opener") as factory:
            factory.return_value.open.side_effect = URLError("https://example.invalid/?apiKey=test-private")
            with self.assertRaises(ProviderError) as ctx:
                TheOddsAPI("test-private").sports()
        self.assertEqual(str(ctx.exception), "network_unavailable")
        self.assertNotIn("test-private", str(ctx.exception))

    def test_large_body_and_html_are_rejected(self):
        cases = ((b"x" * 11, {}, "response_too_large"), (b"[]", {"Content-Length": "999"}, "response_too_large"), (b"[]", {"Content-Type": "text/html"}, "invalid_content_type"), (b"notjson", {}, "invalid_json"), (b"null", {}, "invalid_schema"))
        for raw, headers, code in cases:
            with self.assertRaises(ProviderError) as ctx:
                self.request(TheOddsAPI("test-private", max_body_bytes=10), "sports", raw=raw, headers=headers)
            self.assertEqual(ctx.exception.code, code)

    def test_theodds_provider_error_payload_not_treated_as_empty_feed(self):
        with self.assertRaises(ProviderError) as ctx:
            self.request(TheOddsAPI("test-private"), "sports", data={"message": "bad api key"})
        self.assertEqual(ctx.exception.code, "invalid_schema")

    def test_api_sports_header_auth_and_preserved_paging(self):
        data = {"errors": [], "response": [], "paging": {"current": 1, "total": 2}}
        result, req, _, _ = self.request(APISportsFootball("test-private"), "fixtures", "2026-10-25", league=39, season=2026, data=data)
        self.assertNotIn("test-private", req.full_url)
        self.assertEqual(req.get_header("X-apisports-key"), "test-private")
        self.assertEqual(urlsplit(req.full_url).hostname, "v3.football.api-sports.io")
        self.assertTrue(result.partial)
        self.assertEqual(result.data, data)
        self.assertEqual(parse_qs(urlsplit(req.full_url).query)["timezone"], ["UTC"])

    def test_api_sports_http200_quota_error_not_no_matches(self):
        with self.assertRaises(ProviderError) as ctx:
            self.request(APISportsFootball("test-private"), "fixtures", "2026-10-25", data={"errors": {"requests": "quota exhausted"}, "response": []})
        self.assertEqual(ctx.exception.code, "provider_body_error")

    def test_api_sports_bad_date_and_ids_fail_closed(self):
        for bad in ("2026-02-30", "20261025", None, "2026-10-25&league=1"):
            with self.assertRaises(ValueError):
                APISportsFootball("test-private").fixtures(bad)
        for bad in (True, "../x", 0, -1):
            with self.assertRaises(ValueError):
                APISportsFootball("test-private").leagues(bad)

    def test_sportsdb_no_private_key_and_partial_metadata_labels(self):
        data = {"events": [{"idEvent": "2494070", "strSport": "Soccer"}]}
        result, req, _, _ = self.request(TheSportsDB(), "events_next_league", 4328, data=data)
        self.assertEqual(req.full_url, "https://www.thesportsdb.com/api/v1/json/123/eventsnextleague.php?id=4328")
        self.assertTrue(result.partial)
        self.assertIn("no_odds_or_win_probability", result.notes)
        self.assertEqual(result.data, data)

    def test_sportsdb_empty_result_does_not_assert_no_matches(self):
        result, _, _, _ = self.request(TheSportsDB(), "events_next_league", "4328", data={"events": None})
        self.assertTrue(result.partial)
        self.assertIsNone(result.data["events"])

    def test_sportsdb_malformed_schema_refused(self):
        for data in ([], {"events": "not a list"}, {"error": "quota"}):
            with self.assertRaises(ProviderError):
                self.request(TheSportsDB(), "lookup_event", 123, data=data)


if __name__ == "__main__":
    unittest.main()
