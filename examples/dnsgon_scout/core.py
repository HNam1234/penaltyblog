"""Small, offline market baseline and forward-only scoring. Never places bets.

Odds-derived probabilities describe market prices, not calibrated true chances.
Every shortlist item remains unvalidated even after descriptive scores exist.
All datetimes must include an offset; all odds must be decimal odds.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite, log, sqrt


LABEL = "chưa kiểm định"
METHOD = "market_consensus_leave_one_bookmaker_out"
DEFAULTS = {
    "max_age_seconds": 300,
    "max_clock_skew_seconds": 5,
    "min_reference_books": 3,
    "min_reference_providers": 1,
    "min_probability": 0.75,
    "min_edge": 0.03,
    "max_disagreement": 0.08,
    "min_overround": -0.03,
    "max_overround": 0.30,
}


def _time(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid_aware_timestamp") from None


def _iso(value):
    return _time(value).isoformat().replace("+00:00", "Z")


def _number(value):
    if isinstance(value, bool):
        raise ValueError("invalid_number")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid_number") from None
    if not isfinite(number):
        raise ValueError("invalid_number")
    return number


def _config(values):
    result = {**DEFAULTS, **(values or {})}
    if set(result) != set(DEFAULTS):
        raise ValueError("unknown_config_key")
    for key, value in result.items():
        result[key] = _number(value)
    for key in ("min_reference_books", "min_reference_providers"):
        if result[key] < 1 or not result[key].is_integer():
            raise ValueError("invalid_config")
        result[key] = int(result[key])
    if result["max_age_seconds"] <= 0 or result["max_clock_skew_seconds"] < 0:
        raise ValueError("invalid_config")
    if not 0 < result["min_probability"] < 1 or not 0 <= result["max_disagreement"] <= 1:
        raise ValueError("invalid_config")
    if not 0 <= result["min_edge"] <= 1 or not -1 < result["min_overround"] < result["max_overround"]:
        raise ValueError("invalid_config")
    return result


def _family(sport):
    sport = str(sport).lower()
    if sport in ("football", "soccer") or sport.startswith("soccer_"):
        return "football"
    for family in ("basketball", "tennis"):
        if sport == family or sport.startswith(family + "_"):
            return family
    raise ValueError("unsupported_sport")


def _contract(event, row):
    """An explicit period prevents comparing 90-minute prices with qualification."""
    family = _family(event["sport"])
    market = row.get("market")
    if market == "h2h":
        market = "1x2" if family == "football" else "moneyline"
    expected_period = {"football": "regulation", "basketball": "full_game_including_overtime", "tennis": "full_match"}[family]
    if row.get("period") != expected_period:
        raise ValueError("missing_or_incompatible_period")
    line = row.get("line")
    if market == "1x2" and family == "football" and line is None:
        outcomes = ("home", "draw", "away")
    elif market == "moneyline" and family in ("basketball", "tennis") and line is None:
        outcomes = ("home", "away")
    elif market == "totals" and family in ("football", "basketball"):
        line = _number(line)
        # Only half lines: integer/quarter lines need push/split settlement rules.
        if line <= 0 or abs((line % 1) - 0.5) > 1e-9:
            raise ValueError("unsupported_push_or_split_line")
        outcomes = ("over", "under")
    else:
        raise ValueError("unsupported_market")
    return (market, expected_period, line), outcomes


def remove_margin(odds):
    """Proportional de-vig for a *complete* 2/3-way mutually exclusive market.

    Caller validates the settlement/outcome set; this is a market baseline,
    not a claim that proportional de-vig recovers true probabilities.
    """
    if len(odds) not in (2, 3):
        raise ValueError("incomplete_outcomes")
    inverse = {}
    for name, value in odds.items():
        price = _number(value)
        if price <= 1 or price > 1000:
            raise ValueError("invalid_decimal_odds")
        inverse[name] = 1 / price
    total = sum(inverse.values())
    return {name: value / total for name, value in inverse.items()}, total - 1


def normalize_snapshot(event, row, *, now, config=None):
    """Validate one complete bookmaker market. Raises ValueError(reason_code)."""
    settings = _config(config)
    now = _time(now)
    key, expected = _contract(event, row)
    if row.get("sport", event["sport"]) != event["sport"]:
        raise ValueError("incompatible_sport")
    if row.get("event_id", event["event_id"]) != event["event_id"]:
        raise ValueError("incompatible_event")
    if row.get("status", "open") != "open" or row.get("in_play", False):
        raise ValueError("not_open_prematch")
    as_of = _time(row.get("as_of"))
    age = (now - as_of).total_seconds()
    if age < -settings["max_clock_skew_seconds"]:
        raise ValueError("future_snapshot")
    if age > settings["max_age_seconds"]:
        raise ValueError("stale_snapshot")
    if as_of >= _time(event["start_time"]):
        raise ValueError("post_start_snapshot")
    provider = str(row.get("provider") or "").strip()
    book = str(row.get("bookmaker") or "").strip().casefold()
    if not provider or not book:
        raise ValueError("missing_source_identity")
    names = {str(event["home"]).strip().casefold(): "home", str(event["away"]).strip().casefold(): "away", "draw": "draw", "tie": "draw"}
    prices = {}
    outcomes = row.get("outcomes", [])
    if isinstance(outcomes, dict):
        outcomes = [{"name": name, "odds": price} for name, price in outcomes.items()]
    if not isinstance(outcomes, list):
        raise ValueError("invalid_outcomes")
    commission = _number(row.get("commission", 0))
    if not 0 <= commission <= 0.2:
        raise ValueError("invalid_commission")
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            raise ValueError("invalid_outcomes")
        name = str(outcome.get("name", "")).strip().casefold()
        name = names.get(name, name)
        if name not in expected or name in prices:
            raise ValueError("unexpected_or_duplicate_outcome")
        raw = _number(outcome.get("odds"))
        if raw <= 1 or raw > 1000:
            raise ValueError("invalid_decimal_odds")
        prices[name] = 1 + (raw - 1) * (1 - commission)
    if set(prices) != set(expected):
        raise ValueError("incomplete_outcomes")
    probabilities, overround = remove_margin(prices)
    if not settings["min_overround"] <= overround <= settings["max_overround"]:
        raise ValueError("implausible_overround")
    return {"key": key, "provider": provider, "bookmaker": book, "as_of": _iso(as_of), "age_seconds": max(0, age), "odds": prices, "probabilities": probabilities, "overround": overround}


def analyze_event(event, *, now=None, config=None):
    """Return JSON-safe summaries, shortlist and explicit skip reasons.

    Shortlist EV is relative to other bookmakers' prices, NOT proven profitability.
    Same bookmaker mirrored by several providers counts only once (freshest row).
    No post-match values are read by this function.
    """
    settings = _config(config)
    now = _time(now or datetime.now(timezone.utc))
    report = {"event_id": event.get("event_id"), "label": LABEL, "method": METHOD, "generated_at": _iso(now), "candidates": [], "markets": [], "skipped": []}
    try:
        if not all(str(event.get(field) or "").strip() for field in ("event_id", "sport", "home", "away", "start_time")):
            raise ValueError("missing_event_metadata")
        if str(event["home"]).strip().casefold() == str(event["away"]).strip().casefold():
            raise ValueError("identical_participants")
        _family(event["sport"])
        if _time(event["start_time"]) <= now:
            raise ValueError("event_started")
    except ValueError as exc:
        report["skipped"].append({"reason": str(exc)})
        return report
    groups = defaultdict(dict)
    rows = event.get("markets", [])
    if not isinstance(rows, list):
        report["skipped"].append({"reason": "invalid_markets"})
        return report
    for row in rows:
        try:
            if not isinstance(row, dict):
                raise ValueError("invalid_market_row")
            snapshot = normalize_snapshot(event, row, now=now, config=settings)
            books = groups[snapshot["key"]]
            previous = books.get(snapshot["bookmaker"])
            if previous is None or _time(snapshot["as_of"]) > _time(previous["as_of"]):
                books[snapshot["bookmaker"]] = snapshot
        except ValueError as exc:
            report["skipped"].append({"bookmaker": row.get("bookmaker") if isinstance(row, dict) else None, "reason": str(exc)})
    for (market, period, line), books in sorted(groups.items(), key=lambda item: str(item[0])):
        reasons = Counter()
        summary = {"market": market, "period": period, "line": line, "bookmakers": len(books)}
        for book, offer in books.items():
            refs = [snapshot for name, snapshot in books.items() if name != book]
            if len(refs) < settings["min_reference_books"] or len({r["provider"] for r in refs}) < settings["min_reference_providers"]:
                reasons["insufficient_independent_reference_books"] += 1
                continue
            outcomes = tuple(offer["probabilities"])
            probabilities = {name: sum(r["probabilities"][name] for r in refs) / len(refs) for name in outcomes}
            disagreement = max(max(r["probabilities"][name] for r in refs) - min(r["probabilities"][name] for r in refs) for name in outcomes)
            if disagreement > settings["max_disagreement"]:
                reasons["reference_disagreement"] += 1
                continue
            for selection, probability in probabilities.items():
                edge = probability * offer["odds"][selection] - 1
                if probability < settings["min_probability"]:
                    reasons["below_probability_threshold"] += 1
                    continue
                if edge < settings["min_edge"]:
                    reasons["below_market_edge_threshold"] += 1
                    continue
                identity = "|".join(map(str, (event["event_id"], event["sport"], market, period, line, selection, book)))
                data_as_of = max([offer["as_of"]] + [r["as_of"] for r in refs], key=_time)
                report["candidates"].append({
                    "id": sha256(identity.encode()).hexdigest()[:24], "event_id": event["event_id"], "sport": event["sport"],
                    "home": event["home"], "away": event["away"], "start_time": _iso(event["start_time"]),
                    "market": market, "period": period, "line": line, "selection": selection,
                    "bookmaker": book, "provider": offer["provider"], "offered_odds": offer["odds"][selection],
                    "probability": probability, "probabilities": probabilities,
                    "estimated_edge": edge, "reference_disagreement": disagreement,
                    "reference_books": sorted(r["bookmaker"] for r in refs),
                    "reference_providers": sorted({r["provider"] for r in refs}),
                    "created_at": _iso(now), "data_as_of": data_as_of,
                    "oldest_snapshot_at": min([offer["as_of"]] + [r["as_of"] for r in refs], key=_time),
                    "label": LABEL, "method": METHOD,
                    "caveat": "Baseline từ odds thị trường; chưa phải xác suất thắng đã kiểm định hay bảo đảm có lãi.",
                })
        summary["filtered"] = dict(reasons)
        report["markets"].append(summary)
    report["candidates"].sort(key=lambda candidate: (-candidate["estimated_edge"], candidate["id"]))
    if not rows:
        report["skipped"].append({"reason": "missing_odds"})
    return report


def _forecast_key(row):
    if not isinstance(row, dict):
        raise ValueError("invalid_contract_row")
    key = tuple(row.get(name) for name in ("event_id", "sport", "market", "period", "line"))
    try:
        hash(key)
    except TypeError:
        raise ValueError("invalid_contract_fields") from None
    return key


def _created_order(row):
    """ISO strings with different UTC offsets do not sort chronologically."""
    try:
        return _time(row.get("created_at"))
    except (ValueError, AttributeError):
        return datetime.max.replace(tzinfo=timezone.utc)


def _metrics(rows, bins=10):
    buckets = [[] for _ in range(bins)]
    brier = logloss = hits = 0
    for probabilities, outcome, selection in rows:
        brier += sum((p - (name == outcome)) ** 2 for name, p in probabilities.items())
        logloss -= log(max(probabilities[outcome], 1e-15))
        hits += selection == outcome
        top = max(probabilities, key=probabilities.get)
        bucket = min(bins - 1, int(probabilities[top] * bins))
        buckets[bucket].append((probabilities[top], top == outcome))
    count = len(rows)
    calibration = []
    for index, values in enumerate(buckets):
        if values:
            calibration.append({"lower": index / bins, "upper": (index + 1) / bins, "count": len(values), "mean_top_probability": sum(v[0] for v in values) / len(values), "observed_top_frequency": sum(v[1] for v in values) / len(values)})
    if not count:
        return {"count": 0, "brier": None, "log_loss": None, "selection_hit_rate": None, "calibration": []}
    p = hits / count
    z = 1.959963984540054
    centre = (p + z * z / (2 * count)) / (1 + z * z / count)
    half = z * sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / (1 + z * z / count)
    return {"count": count, "brier": brier / count, "log_loss": logloss / count, "selection_hit_rate": p, "hit_rate_wilson95": [max(0, centre - half), min(1, centre + half)], "calibration": calibration}


def score_predictions(predictions, results, *, now=None, min_samples=100):
    """Describe resolved forward forecasts; sample count never certifies a strategy.

    Results need matching contract fields, outcome, settled_at, known_at,
    status='final' and settlement_verified=True. known_at records when this
    system actually obtained the final result, not its publication date.
    The caller must verify actual market rules.
    Only the earliest valid forecast per event/contract is counted. Void/push,
    provisional and future results are excluded. No profit figures are invented.
    """
    now = _time(now or datetime.now(timezone.utc))
    if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples < 1:
        raise ValueError("invalid_min_samples")
    result_map, excluded = defaultdict(list), Counter()
    for result in results:
        try:
            result_map[_forecast_key(result)].append(result)
        except ValueError:
            excluded["invalid_result_row"] += 1
    accepted, groups, seen = [], defaultdict(list), set()
    ordered = sorted(predictions, key=_created_order)
    for forecast in ordered:
        try:
            key = _forecast_key(forecast)
            if any(key[index] is None for index in range(4)):
                raise ValueError("missing_forecast_contract")
            _contract(forecast, forecast)
            created = _time(forecast.get("created_at"))
            started = _time(forecast.get("start_time"))
            data_as_of = _time(forecast.get("data_as_of"))
            if created >= started or data_as_of > created or created > now:
                raise ValueError("forecast_leakage_or_future")
            probabilities = {name: _number(p) for name, p in forecast.get("probabilities", {}).items()}
            _, expected = _contract(forecast, forecast)
            if set(probabilities) != set(expected) or any(not 0 <= p <= 1 for p in probabilities.values()) or abs(sum(probabilities.values()) - 1) > 1e-8:
                raise ValueError("invalid_probability_distribution")
            matches = result_map.get(key, [])
            if not matches:
                raise ValueError("missing_result")
            if len(matches) != 1:
                raise ValueError("ambiguous_result")
            result = matches[0]
            if result.get("status") != "final" or result.get("settlement_verified") is not True:
                raise ValueError("unverified_settlement")
            settled = _time(result.get("settled_at"))
            if settled > now or settled < started:
                raise ValueError("future_or_impossible_result")
            known = _time(result.get("known_at"))
            if known > now or known < settled:
                raise ValueError("future_or_impossible_result_knowledge")
            outcome = result.get("outcome")
            if outcome not in probabilities:
                raise ValueError("void_push_or_unknown_outcome")
            selection = forecast.get("selection", max(probabilities, key=probabilities.get))
            if selection not in probabilities:
                raise ValueError("invalid_selection")
            if key in seen:
                raise ValueError("duplicate_forecast")
            seen.add(key)
            row = probabilities, outcome, selection
            accepted.append(row)
            groups[(forecast["sport"], forecast["market"], forecast["period"], forecast.get("line"))].append(row)
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            excluded[str(exc) if isinstance(exc, ValueError) else "invalid_forecast_row"] += 1
    metrics = _metrics(accepted)
    return {"label": LABEL, "generated_at": _iso(now), "metrics": metrics,
            "sample_status": "descriptive_sample_available" if len(accepted) >= min_samples else "insufficient_sample",
            "minimum_descriptive_samples": min_samples, "excluded": dict(excluded),
            "by_contract": [{"sport": key[0], "market": key[1], "period": key[2], "line": key[3], **_metrics(rows)} for key, rows in sorted(groups.items(), key=lambda item: str(item[0]))],
            "caveat": "Điểm mô tả dự đoán đã ghi trước trận; số mẫu/hit rate không bảo đảm xác suất tương lai hoặc lợi nhuận. Không so trực tiếp Brier 2 cửa với 3 cửa."}
