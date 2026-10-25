"""Public, read-only Polymarket data. No accounts, wallets or order methods.

Binary exchange prices are not bookmaker consensus or calibrated win chances.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from urllib.parse import urlencode

from adapters import FeedResult, ProviderError, _read_json, _settings


GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
CLAUSE = "This market refers only to the outcome within the first 90 minutes of regular play plus stoppage time."


def _identifier(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,99}", str(value)):
        raise ValueError("invalid_public_id")
    return str(value)


def _time(value):
    try:
        if not isinstance(value, datetime):
            raw = str(value).replace("Z", "+00:00")
            # Gamma emits +00; Python 3.10 requires the equivalent +00:00.
            if re.search(r"[+-][0-9]{2}$", raw):
                raw += ":00"
            value = datetime.fromisoformat(raw)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError()
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid_timestamp") from None


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValueError("invalid_text")
    return " ".join(value.split())


def _decimal(value, *, price=False):
    if not isinstance(value, str) or len(value) > 50:
        raise ValueError("invalid_decimal_string")
    try:
        number = Decimal(value)
        if not number.is_finite() or not 0 < number <= (1 if price else Decimal("1e15")):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_decimal_string") from None
    if price and number == 1:
        raise ValueError("terminal_price")
    return number


class Polymarket:
    def __init__(self, timeout=15, max_body_bytes=2_000_000):
        self.timeout, self.max_body_bytes = _settings(timeout, max_body_bytes)

    def events(self, series_id, limit=20, offset=0):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("invalid_limit")
        if type(offset) is not int or not 0 <= offset <= 80:
            raise ValueError("invalid_offset")
        now = datetime.now(timezone.utc)
        query = urlencode({"series_id": _identifier(series_id), "closed": "false", "limit": limit,
                           "offset": offset,
                           "end_date_min": now.isoformat(), "order": "startDate", "ascending": "true"})
        data, quota = _read_json(GAMMA + "/events?" + query, {}, self.timeout, self.max_body_bytes)
        if not isinstance(data, list):
            raise ProviderError("invalid_schema", quota=quota)
        return FeedResult(data, quota, datetime.now(timezone.utc).isoformat(), "polymarket_gamma", True,
                          ("public_binary_prediction_exchange", "bounded_page_not_full_calendar"))

    def book(self, token_id):
        identifier = _identifier(token_id)
        data, quota = _read_json(CLOB + "/book?" + urlencode({"token_id": identifier}), {}, self.timeout, self.max_body_bytes)
        if not isinstance(data, dict) or data.get("asset_id") != identifier or not isinstance(data.get("bids"), list) or not isinstance(data.get("asks"), list):
            raise ProviderError("invalid_schema", quota=quota)
        return FeedResult(data, quota, datetime.now(timezone.utc).isoformat(), "polymarket_clob", False,
                          ("public_binary_prediction_exchange", "fees_not_inferred"))

    def events_by_slugs(self, slugs):
        if (not isinstance(slugs, list) or not 1 <= len(slugs) <= 20
                or any(not isinstance(s, str) or not re.fullmatch(r'epl-[a-z]{2,4}-[a-z]{2,4}-\d{4}-\d{2}-\d{2}', s) for s in slugs)):
            raise ValueError('invalid_fixture_slugs')
        query = urlencode([('slug', s) for s in slugs] + [('closed', 'false'), ('limit', '20')])
        data, quota = _read_json(GAMMA + '/events?' + query, {}, self.timeout, self.max_body_bytes)
        if not isinstance(data, list) or any(e.get('slug') not in slugs for e in data):
            raise ProviderError('invalid_schema', quota=quota)
        return FeedResult(data, quota, datetime.now(timezone.utc).isoformat(), 'polymarket_gamma', True,
                          ('exact_public_fixture_slugs', 'bounded_calendar_not_full_coverage'))

    @staticmethod
    def football_watch(event, books_by_token, fetched_at):
        return football_watch(event, books_by_token, fetched_at)

    @staticmethod
    def watch_event(event, books_by_token, fetched_at, now=None):
        return watch_event(event, books_by_token, fetched_at, now)


def moneyline_markets(event, now):
    """Validated metadata only, three separate YES/NO contracts, not three books."""
    now = _time(now)
    if not isinstance(event, dict) or event.get("closed") is not False or event.get("active") is not True:
        raise ValueError("event_not_active")
    markets = event.get("markets")
    if not isinstance(markets, list):
        raise ValueError("invalid_markets")
    accepted, seen = [], set()
    for market in markets:
        if not isinstance(market, dict) or market.get("sportsMarketType") != "moneyline":
            continue
        if market.get("active") is not True or market.get("closed") is not False or market.get("acceptingOrders") is not True:
            continue
        start = _time(market.get("gameStartTime"))
        if start <= now:
            continue
        if event.get("startTime") is not None and _time(event["startTime"]) != start:
            raise ValueError("conflicting_kickoff")
        description = market.get("description")
        if not isinstance(description, str) or CLAUSE not in description:
            raise ValueError("unreviewed_period_description")
        outcomes = market.get("outcomes")
        outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
        if not isinstance(outcomes, list) or len(outcomes) != 2 or set(outcomes) != {"Yes", "No"}:
            raise ValueError("not_yes_no_contract")
        version = market.get("version")
        tokens = market.get("clobTokenIds") if version == "v1" else market.get("positionIds") if version == "v2" else None
        tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
        if not isinstance(tokens, list) or len(tokens) != 2:
            raise ValueError("invalid_outcome_ids")
        tokens = [_identifier(token) for token in tokens]
        if len(set(tokens)) != 2:
            raise ValueError("duplicate_outcome_id")
        market_id = _identifier(market.get("id"))
        if market_id in seen:
            raise ValueError("duplicate_moneyline_market")
        seen.add(market_id)
        accepted.append({"market_id": market_id, "question": _text(market.get("question")),
            "token_id": tokens[outcomes.index("Yes")], "version": version,
            "start_time": start.isoformat(), "condition_id": market.get("conditionId"),
            "period": "regulation", "rule_clause": CLAUSE,
            "description_sha256": sha256(description.encode()).hexdigest(),
            "fees_enabled": market.get("feesEnabled") if type(market.get("feesEnabled")) is bool else None,
            "raw_fee_fields": {key: market.get(key) for key in ("feesEnabled", "makerBaseFee", "takerBaseFee")}})
    if accepted and (len(accepted) != 3 or len({row["start_time"] for row in accepted}) != 1
                     or len({row["question"] for row in accepted}) != 3
                     or len({row["token_id"] for row in accepted}) != 3):
        raise ValueError("incomplete_or_conflicting_three_way_group")
    return accepted


def _book_top(book, market, now):
    if book is None:
        raise ValueError("missing_book")
    if not isinstance(book, dict) or book.get("asset_id") != market["token_id"]:
        raise ValueError("book_token_mismatch")
    if market["condition_id"] and book.get("market") != market["condition_id"]:
        raise ValueError("book_condition_mismatch")
    stamp = book.get("timestamp")
    if not isinstance(stamp, str) or not re.fullmatch(r"[0-9]{13,14}", stamp):
        raise ValueError("missing_book_timestamp")
    as_of = datetime.fromtimestamp(int(stamp) / 1000, timezone.utc)
    if as_of > now + timedelta(seconds=5) or now - as_of > timedelta(minutes=5) or as_of >= _time(market["start_time"]):
        raise ValueError("stale_future_or_post_start_book")
    levels = {}
    for side, choose in (("bids", max), ("asks", min)):
        rows = book.get(side)
        if not isinstance(rows, list):
            raise ValueError("invalid_orderbook")
        values = [(_decimal(item["price"], price=True), _decimal(item["size"])) for item in rows]
        top = choose(values, key=lambda value: value[0], default=None)
        levels[side] = {"price": str(top[0]), "shares": str(top[1])} if top else None
    if levels["bids"] and levels["asks"] and Decimal(levels["bids"]["price"]) > Decimal(levels["asks"]["price"]):
        raise ValueError("crossed_orderbook")
    return {"as_of": as_of.isoformat(), "best_bid": levels["bids"], "best_ask": levels["asks"],
            "min_order_size_shares": str(_decimal(book["min_order_size"])),
            "book_hash": book.get("hash"), "fee_rate": None, "fee_status": "unknown",
            "price_units": "quote_currency_per_one_unit_payout_share", "quote_currency": "not_identified_by_book_response",
            "liquidity_status": "top_ask_available" if levels["asks"] else "no_ask_liquidity"}


def football_watch(event, books_by_token, fetched_at, *, now=None):
    """Pure helper: no network. Emits raw-question binary quotes, never picks.

    Caller fetches at most its own per-poll budget; a missing token becomes an
    explicit skip. Source orderbook time, not Gamma updatedAt, defines freshness.
    """
    fetched = _time(fetched_at)
    now = _time(now or fetched)
    result = {"kind": "binary_exchange_market_watch", "provider": "polymarket", "status": "metadata_only",
              "event_id": None, "title": None, "start_time": None, "fetched_at": fetched.isoformat(),
              "quotes": [], "skipped": [], "independent_venues": 1,
              "label": "Theo dõi giá thị trường, chưa phải dự đoán thắng", "recommendation": False}
    try:
        result["event_id"] = "polymarket:" + _identifier(event.get("id"))
        result["title"] = _text(event.get("title"))
        markets = moneyline_markets(event, now)
        if not markets:
            raise ValueError("no_supported_upcoming_moneyline")
        result["start_time"] = markets[0]["start_time"]
        for market in markets:
            try:
                quote = _book_top(books_by_token.get(market["token_id"]), market, now)
                result["quotes"].append({**market, **quote, "outcome": "Yes",
                    "source_url": CLOB + "/book?token_id=" + market["token_id"]})
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                result["skipped"].append({"market_id": market["market_id"], "reason": str(error) if isinstance(error, ValueError) else "invalid_book"})
        result["status"] = "quotes_available" if result["quotes"] else "quotes_unavailable"
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as error:
        result["skipped"].append({"reason": str(error) if isinstance(error, ValueError) else "invalid_event"})
    return result


def watch_event(event, books_by_token, fetched_at, now=None):
    return football_watch(event, books_by_token, fetched_at, now=now)
