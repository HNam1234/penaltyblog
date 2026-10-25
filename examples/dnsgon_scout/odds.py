"""Strict soccer h2h normalization for The Odds API V4.

No default bookmaker or competition settlement policy is inferred here. Feed
prices are observations; a complete 3-way quote does not establish true odds.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from math import isfinite
import re
from urllib.parse import urlsplit


PROVIDER = "the_odds_api"
SOURCE_URL = "https://the-odds-api.com/liveapi/guides/v4/"


def _time(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError()
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid_timestamp") from None


def _iso(value):
    return _time(value).isoformat().replace("+00:00", "Z")


def _text(value, limit=160):
    if not isinstance(value, str):
        raise ValueError("invalid_text")
    value = " ".join(value.split())
    if not value or len(value) > limit:
        raise ValueError("invalid_text")
    return value


def _key(value, *, soccer=False):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_\-]{1,100}", value):
        raise ValueError("invalid_id")
    if soccer and not value.startswith("soccer_"):
        raise ValueError("unsupported_sport")
    return value


def _review(rule, now):
    if not isinstance(rule, dict) or rule.get("period") != "regulation":
        raise ValueError("unreviewed_book")
    try:
        if not isinstance(rule.get("evidence_url"), str):
            raise ValueError()
        url = urlsplit(rule["evidence_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or _time(rule["reviewed_at"]) > now:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise ValueError("unreviewed_book") from None


def _event(row):
    if not isinstance(row, dict):
        raise ValueError("invalid_event")
    identifier, sport = _key(row.get("id")), _key(row.get("sport_key"), soccer=True)
    home, away = _text(row.get("home_team")), _text(row.get("away_team"))
    if home.casefold() == away.casefold() or home.casefold() in {"draw", "tie"} or away.casefold() in {"draw", "tie"}:
        raise ValueError("ambiguous_participants")
    return {"event_id": PROVIDER + ":" + identifier, "sport": sport,
            "home": home, "away": away, "start_time": _iso(row.get("commence_time")),
            "league": _text(row.get("sport_title", sport)), "league_id": sport,
            "markets": [], "source_url": SOURCE_URL}


def _outcomes(values, event):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError("incomplete_or_non_three_way_market")
    names = {event["home"].casefold(): "home", event["away"].casefold(): "away", "draw": "draw"}
    result = {}
    for item in values:
        if not isinstance(item, dict) or item.get("point") is not None:
            raise ValueError("invalid_outcome")
        name = names.get(_text(item.get("name")).casefold())
        price = item.get("price")
        if name is None or name in result:
            raise ValueError("unexpected_or_duplicate_outcome")
        if type(price) not in (int, float) or not isfinite(price) or not 1 < price <= 1000:
            raise ValueError("invalid_decimal_odds")
        result[name] = float(price)
    if set(result) != {"home", "draw", "away"}:
        raise ValueError("incomplete_or_non_three_way_market")
    return result


def normalize(rows, reviewed_book_rules, *, now=None):
    """Return (events, rejected_reason_counts), only pre-start 3-way soccer.

    Rules map the exact provider bookmaker key to period='regulation', HTTPS
    evidence_url and aware reviewed_at. No key aliases or bookmaker-level
    last_update fallback: every accepted h2h market must carry last_update.
    Core still checks freshness, overround, reference counts and disagreement.
    """
    if not isinstance(rows, list) or not isinstance(reviewed_book_rules, dict):
        raise ValueError("invalid_feed_or_rules")
    now = _time(now or datetime.now(timezone.utc))
    events, identities, bad_events, rejected = {}, {}, set(), Counter()
    for row in rows:
        try:
            event = _event(row)
            event_id = event["event_id"]
            identity = tuple(event[k] for k in ("sport", "home", "away", "start_time"))
            if event_id in identities and identities[event_id] != identity:
                bad_events.add(event_id)
                raise ValueError("conflicting_event_identity")
            identities[event_id] = identity
            if _time(event["start_time"]) <= now or row.get("in_play", False) is not False or row.get("completed", False) is not False:
                raise ValueError("event_started_or_completed")
            if row.get("status", "open") != "open":
                raise ValueError("not_open_prematch")
            if not isinstance(row.get("bookmakers"), list):
                raise ValueError("invalid_bookmakers")
            target = events.setdefault(event_id, event)
            for book in row["bookmakers"]:
                try:
                    if not isinstance(book, dict):
                        raise ValueError("invalid_bookmaker")
                    book_key = _key(book.get("key"))
                    if book.get("status", "open") != "open" or book.get("suspended", False) is not False:
                        raise ValueError("not_open_prematch")
                    # Exchange rows need market-specific commission/liquidity;
                    # they are intentionally outside this sportsbook adapter.
                    if book_key.startswith("betfair_ex_") or book_key in {"matchbook", "smarkets"}:
                        raise ValueError("exchange_requires_separate_contract")
                    rule = reviewed_book_rules.get(book_key)
                    _review(rule, now)
                    if rule.get("sports") is not None:
                        if not isinstance(rule["sports"], list) or event["sport"] not in rule["sports"]:
                            raise ValueError("book_rule_sport_not_reviewed")
                    if not isinstance(book.get("markets"), list):
                        raise ValueError("invalid_markets")
                    for market in book["markets"]:
                        try:
                            if not isinstance(market, dict):
                                raise ValueError("invalid_market")
                            if market.get("key") != "h2h":
                                rejected["unsupported_market"] += 1
                                continue
                            if market.get("status", "open") != "open" or market.get("suspended", False) is not False:
                                raise ValueError("not_open_prematch")
                            if market.get("period", "regulation") != "regulation" or market.get("line") is not None:
                                raise ValueError("incompatible_market_contract")
                            updated = _time(market.get("last_update"))
                            if updated >= _time(event["start_time"]) or updated > now + timedelta(seconds=5):
                                raise ValueError("future_or_post_start_market")
                            target["markets"].append({"provider": PROVIDER, "bookmaker": book_key,
                                "bookmaker_name": _text(book.get("title", book_key)),
                                "market": "1x2", "period": "regulation", "line": None,
                                "as_of": _iso(updated), "outcomes": _outcomes(market.get("outcomes"), event),
                                "status": "open", "in_play": False,
                                "rules_evidence_url": rule["evidence_url"], "rules_reviewed_at": _iso(rule["reviewed_at"])})
                        except (ValueError, TypeError, KeyError, OverflowError) as error:
                            rejected[str(error) if isinstance(error, ValueError) else "invalid_market"] += 1
                except (ValueError, TypeError, KeyError, OverflowError) as error:
                    rejected[str(error) if isinstance(error, ValueError) else "invalid_bookmaker"] += 1
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            rejected[str(error) if isinstance(error, ValueError) else "invalid_event"] += 1
    for event_id in bad_events:
        events.pop(event_id, None)
    for event in events.values():
        unique, conflicts = {}, set()
        for market in event["markets"]:
            key = (market["bookmaker"], market["market"], market["period"], market["as_of"])
            signature = json.dumps(market["outcomes"], sort_keys=True, allow_nan=False)
            if key in unique and unique[key][0] != signature:
                conflicts.add(key)
            unique[key] = (signature, market)
        event["markets"] = [row for key, (_, row) in unique.items() if key not in conflicts]
        rejected["ambiguous_book_market"] += len(conflicts)
    return list(events.values()), {key: value for key, value in rejected.items() if value}


def score_results(rows, known_at, reviewed_competition_rules):
    """Only explicitly reviewed ordinary domestic league finals may settle.

    The V4 completed flag alone does not identify a regulation-time score.
    Rules must require competition_type='domestic_league', period='regulation',
    no_extra_time=True, HTTPS evidence_url, reviewed_at and verified=True.
    Cups, playoffs and unreviewed competitions return no final results.
    """
    if not isinstance(rows, list) or not isinstance(reviewed_competition_rules, dict):
        raise ValueError("invalid_feed_or_rules")
    known = _time(known_at)
    results, identities, conflicts = {}, {}, set()
    for row in rows:
        try:
            event = _event(row)
            rule = reviewed_competition_rules.get(event["sport"], {})
            _review(rule, known)
            if rule.get("verified") is not True or rule.get("competition_type") != "domestic_league" or rule.get("no_extra_time") is not True:
                continue
            if row.get("completed") is not True or _time(event["start_time"]) >= known:
                continue
            # Explicit contrary status/period flags always override league policy.
            if row.get("status", "FT") != "FT" or row.get("period", "regulation") != "regulation" or row.get("extra_time", False) is not False or row.get("penalties", False) is not False:
                continue
            updated = _time(row.get("last_update"))
            if not _time(event["start_time"]) < updated <= known:
                continue
            scores = row.get("scores")
            if not isinstance(scores, list) or len(scores) != 2:
                continue
            mapping = {event["home"].casefold(): "home", event["away"].casefold(): "away"}
            values = {}
            for item in scores:
                name = mapping.get(_text(item.get("name")).casefold())
                value = item.get("score")
                if name is None or name in values or isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,2}", str(value)):
                    raise ValueError("invalid_score")
                values[name] = int(value)
            if set(values) != {"home", "away"}:
                continue
            identity = (event["sport"], event["home"], event["away"], event["start_time"], values["home"], values["away"])
            event_id = event["event_id"]
            if event_id in identities and identities[event_id] != identity:
                conflicts.add(event_id)
            identities[event_id] = identity
            result = "home" if values["home"] > values["away"] else "away" if values["away"] > values["home"] else "draw"
            results[event_id] = {"event_id": event_id, "sport": event["sport"], "market": "1x2", "period": "regulation", "line": None,
                "outcome": result, "status": "final", "settlement_verified": True,
                "settled_at": _iso(known), "known_at": _iso(known), "settled_at_basis": "first_observed_final_upper_bound",
                "rules_evidence_url": rule["evidence_url"], "rules_reviewed_at": _iso(rule["reviewed_at"])}
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
            continue
    return [result for event_id, result in results.items() if event_id not in conflicts]
