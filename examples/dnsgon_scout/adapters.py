"""Small read-only clients for documented feeds; no betting/account endpoints.

Each response preserves the vendor JSON. Metadata never implies complete coverage.
No redirects, unbounded reads, automatic retries or raw upstream error messages.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
import math
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True)
class FeedResult:
    data: object
    quota: dict
    fetched_at: str
    provider: str
    partial: bool = False
    notes: tuple = field(default_factory=tuple)


class ProviderError(RuntimeError):
    def __init__(self, code, *, status=None, quota=None):
        self.code, self.status, self.quota = code, status, quota or {}
        super().__init__(code)  # Never expose a URL, key or provider error body.


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_QUOTA = {
    "x-requests-remaining": "remaining", "x-requests-used": "used",
    "x-requests-last": "last_cost", "retry-after": "retry_after_seconds",
    "x-ratelimit-requests-limit": "daily_limit",
    "x-ratelimit-requests-remaining": "daily_remaining",
    "x-ratelimit-limit": "minute_limit", "x-ratelimit-remaining": "minute_remaining",
}


def _quota(headers):
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    return {target: int(lowered[name]) for name, target in _QUOTA.items()
            if lowered.get(name, "").isdigit() and len(lowered[name]) <= 12}


def _settings(timeout, max_body_bytes):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("invalid_timeout")
    if isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int) or not 1 <= max_body_bytes <= 4_000_000:
        raise ValueError("invalid_body_limit")
    return timeout, max_body_bytes


def _key(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512 or any(not 33 <= ord(c) <= 126 for c in value):
        raise ValueError("invalid_api_key")
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", value):
        raise ValueError("invalid_sport")
    return value


def _positive_id(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,11}", str(value)):
        raise ValueError("invalid_id")
    return str(value)


def _date(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError("invalid_date") from None
    return value


def _read_json(url, headers, timeout, max_body_bytes):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "DNSGon-SportsScout/0.1", **headers})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            quota = _quota(response.headers)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise ProviderError("invalid_content_type", quota=quota)
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > max_body_bytes:
                raise ProviderError("response_too_large", quota=quota)
            raw = response.read(max_body_bytes + 1)
            if len(raw) > max_body_bytes:
                raise ProviderError("response_too_large", quota=quota)
            try:
                data = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeError, ValueError, RecursionError):
                raise ProviderError("invalid_json", quota=quota) from None
            if not isinstance(data, (dict, list)):
                raise ProviderError("invalid_schema", quota=quota)
            return data, quota
    except HTTPError as error:
        code = {401: "authentication_failed", 403: "access_denied", 429: "quota_exhausted"}.get(error.code, "http_error")
        if 300 <= error.code < 400:
            code = "redirect_refused"
        quota = _quota(error.headers or {})
        error.close()
        raise ProviderError(code, status=error.code, quota=quota) from None
    except (URLError, socket.timeout, TimeoutError, OSError):
        raise ProviderError("network_unavailable") from None


class TheOddsAPI:
    """V4 https://the-odds-api.com/liveapi/guides/v4/ (requires own key)."""

    def __init__(self, api_key, timeout=10, max_body_bytes=2_000_000):
        self._key = _key(api_key)
        self.timeout, self.max_body_bytes = _settings(timeout, max_body_bytes)

    def _get(self, path, params):
        url = "https://api.the-odds-api.com/v4/" + path + "?" + urlencode({**params, "apiKey": self._key})
        data, quota = _read_json(url, {}, self.timeout, self.max_body_bytes)
        if not isinstance(data, list):
            raise ProviderError("invalid_schema", quota=quota)
        return FeedResult(data, quota, datetime.now(timezone.utc).isoformat(), "the_odds_api")

    def sports(self, all_sports=False):
        if not isinstance(all_sports, bool):
            raise ValueError("invalid_all_sports")
        return self._get("sports/", {"all": str(all_sports).lower()})

    def odds(self, sport, regions="eu", markets="h2h"):
        sport = _identifier(sport)
        # One region deliberately bounds the per-request quota footprint.
        if regions not in {"au", "uk", "eu", "us", "us2"}:
            raise ValueError("one_supported_region_required")
        if not isinstance(markets, str) or not markets or any(m not in {"h2h", "spreads", "totals"} for m in markets.split(",")):
            raise ValueError("invalid_markets")
        if len(set(markets.split(","))) != len(markets.split(",")):
            raise ValueError("duplicate_market")
        return self._get("sports/" + sport + "/odds/", {"regions": regions, "markets": markets, "oddsFormat": "decimal", "dateFormat": "iso"})

    def scores(self, sport, days_from=3):
        if isinstance(days_from, bool) or not isinstance(days_from, int) or not 1 <= days_from <= 3:
            raise ValueError("invalid_days_from")
        return self._get("sports/" + _identifier(sport) + "/scores/", {"daysFrom": days_from, "dateFormat": "iso"})


class APISportsFootball:
    """Official API-Football, key required; check allowed seasons/coverage first."""

    def __init__(self, api_key, timeout=10, max_body_bytes=2_000_000):
        self._key = _key(api_key)
        self.timeout, self.max_body_bytes = _settings(timeout, max_body_bytes)

    def _get(self, endpoint, params):
        data, quota = _read_json("https://v3.football.api-sports.io/" + endpoint + "?" + urlencode(params),
                                 {"x-apisports-key": self._key}, self.timeout, self.max_body_bytes)
        if not isinstance(data, dict) or "errors" not in data or not isinstance(data.get("response"), list):
            raise ProviderError("invalid_schema", quota=quota)
        if data["errors"]:
            # API-Sports may signal authentication/quota/plan failures with HTTP200.
            raise ProviderError("provider_body_error", quota=quota)
        paging = data.get("paging", {})
        if not isinstance(paging, dict):
            raise ProviderError("invalid_schema", quota=quota)
        partial = paging.get("total", 1) != 1
        return FeedResult(data, quota, datetime.now(timezone.utc).isoformat(), "api_sports_football", partial,
                          ("coverage_and_seasons_depend_on_account",))

    def leagues(self, league=None, season=None):
        params = {}
        if league is not None:
            params["id"] = _positive_id(league)
        if season is not None:
            params["season"] = _positive_id(season)
        return self._get("leagues", params)

    def fixtures(self, match_date, league=None, season=None):
        params = {"date": _date(match_date), "timezone": "UTC"}
        if league is not None:
            params["league"] = _positive_id(league)
        if season is not None:
            params["season"] = _positive_id(season)
        return self._get("fixtures", params)

    def odds(self, match_date, league=None, season=None, page=1):
        params = {"date": _date(match_date), "page": _positive_id(page)}
        if league is not None:
            params["league"] = _positive_id(league)
        if season is not None:
            params["season"] = _positive_id(season)
        return self._get("odds", params)


class TheSportsDB:
    """Public development key123; truncated metadata only, never an odds feed.

    Official free limits include one next league event. No website scraping,
    result-limit bypass, fake probabilities or external account required.
    """

    def __init__(self, timeout=10, max_body_bytes=2_000_000):
        self.timeout, self.max_body_bytes = _settings(timeout, max_body_bytes)

    def _get(self, endpoint, entity_id):
        url = "https://www.thesportsdb.com/api/v1/json/123/" + endpoint + "?" + urlencode({"id": _positive_id(entity_id)})
        data, quota = _read_json(url, {}, self.timeout, self.max_body_bytes)
        if not isinstance(data, dict) or "events" not in data or not (data["events"] is None or isinstance(data["events"], list)):
            raise ProviderError("invalid_schema", quota=quota)
        return FeedResult(data, quota, datetime.now(timezone.utc).isoformat(), "thesportsdb_public_dev", True,
                          ("public_development_feed", "partial_free_results", "no_odds_or_win_probability"))

    def events_next_league(self, league_id):
        return self._get("eventsnextleague.php", league_id)

    def lookup_event(self, event_id):
        return self._get("lookupevent.php", event_id)
