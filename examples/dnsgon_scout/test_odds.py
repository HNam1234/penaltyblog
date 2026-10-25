"""Synthetic provider contracts; fixtures here are test data, never live picks."""
from copy import deepcopy
import unittest

from core import analyze_event
from odds import normalize, score_results


NOW = "2026-10-25T06:00:00Z"
REVIEWED = "2026-10-25T05:00:00Z"
POLICY = {"period": "regulation", "evidence_url": "https://example.org/official-rules", "reviewed_at": REVIEWED}


def fixture():
    return {"id": "0123456789abcdef", "sport_key": "soccer_epl", "sport_title": "EPL",
            "home_team": "Club H", "away_team": "Club A", "commence_time": "2026-10-25T14:00:00Z",
            "bookmakers": [{"key": "pinnacle", "title": "Pinnacle", "last_update": NOW,
                            "markets": [{"key": "h2h", "last_update": NOW,
                                         "outcomes": [{"name": "Club H", "price": 1.3}, {"name": "Draw", "price": 5.5}, {"name": "Club A", "price": 9.0}]}]}]}


def completed():
    return {**fixture(), "completed": True, "commence_time": "2026-10-24T14:00:00Z",
            "last_update": "2026-10-25T05:55:00Z",
            "scores": [{"name": "Club H", "score": "2"}, {"name": "Club A", "score": "1"}]}


def competition():
    return {"soccer_epl": {**POLICY, "verified": True, "competition_type": "domestic_league", "no_extra_time": True}}


class OddsTests(unittest.TestCase):
    def call(self, rows=None, rules=None):
        return normalize([fixture()] if rows is None else rows, {"pinnacle": POLICY} if rules is None else rules, now=NOW)

    def test_complete_three_way_maps_to_core_schema(self):
        events, rejected = self.call()
        self.assertEqual(rejected, {})
        event = events[0]
        self.assertEqual(event["event_id"], "the_odds_api:0123456789abcdef")
        self.assertEqual(event["sport"], "soccer_epl")
        market = event["markets"][0]
        self.assertEqual(market["outcomes"], {"home": 1.3, "draw": 5.5, "away": 9.0})
        self.assertEqual(market["period"], "regulation")
        self.assertEqual(market["bookmaker"], "pinnacle")
        self.assertEqual(market["as_of"], NOW)

    def test_missing_or_invalid_policy_never_infers_period(self):
        for rule in ({}, {**POLICY, "period": "full_match"}, {**POLICY, "evidence_url": "http://example.org"}, {**POLICY, "evidence_url": []}, {**POLICY, "reviewed_at": "2027-01-01T00:00:00Z"}):
            events, rejected = self.call(rules={"pinnacle": rule})
            self.assertEqual(events[0]["markets"], [])
            self.assertEqual(rejected["unreviewed_book"], 1)

    def test_no_book_level_timestamp_fallback(self):
        row = fixture()
        del row["bookmakers"][0]["markets"][0]["last_update"]
        events, rejected = self.call([row])
        self.assertEqual(events[0]["markets"], [])
        self.assertEqual(rejected["invalid_timestamp"], 1)

    def test_stale_market_is_not_freshened_by_acquisition(self):
        row = fixture()
        row["bookmakers"][0]["markets"][0]["last_update"] = "2026-10-25T05:40:00Z"
        events, _ = self.call([row])
        self.assertEqual(events[0]["markets"][0]["as_of"], "2026-10-25T05:40:00Z")
        self.assertEqual(analyze_event(events[0], now=NOW)["skipped"][0]["reason"], "stale_snapshot")

    def test_non_soccer_wrong_market_and_two_way_rejected(self):
        row = fixture()
        row["sport_key"] = "basketball_nba"
        events, rejected = self.call([row])
        self.assertEqual(events, [])
        self.assertIn("unsupported_sport", rejected)
        for key in ("h2h_lay", "h2h_h1", "h2h_3_way", "totals", "draw_no_bet"):
            row = fixture()
            row["bookmakers"][0]["markets"][0]["key"] = key
            events, rejected = self.call([row])
            self.assertEqual(events[0]["markets"], [])
            self.assertEqual(rejected["unsupported_market"], 1)
        row = fixture()
        row["bookmakers"][0]["markets"][0]["outcomes"].pop()
        self.assertEqual(self.call([row])[0][0]["markets"], [])

    def test_bad_prices_outcomes_and_duplicates_fail_closed(self):
        for price in (True, "1.3", 1, 0, float("nan"), float("inf"), 1001):
            row = fixture()
            row["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = price
            self.assertEqual(self.call([row])[0][0]["markets"], [])
        row = fixture()
        row["bookmakers"][0]["markets"][0]["outcomes"][1]["name"] = "Club H"
        self.assertIn("unexpected_or_duplicate_outcome", self.call([row])[1])

    def test_started_completed_suspended_and_future_market_rejected(self):
        for change in ({"commence_time": NOW}, {"in_play": True}, {"completed": True}, {"status": "suspended"}):
            self.assertEqual(self.call([{**fixture(), **change}])[0], [])
        row = fixture()
        row["bookmakers"][0]["markets"][0]["last_update"] = "2026-10-25T06:01:00Z"
        self.assertEqual(self.call([row])[0][0]["markets"], [])
        row = fixture()
        row["bookmakers"][0]["markets"][0]["suspended"] = True
        self.assertEqual(self.call([row])[0][0]["markets"], [])

    def test_same_book_identical_copy_is_not_extra_vote(self):
        row = fixture()
        row["bookmakers"].append(deepcopy(row["bookmakers"][0]))
        events, rejected = self.call([row])
        self.assertEqual(len(events[0]["markets"]), 1)
        self.assertEqual(rejected, {})

    def test_explicit_closed_book_or_extra_time_market_never_overridden(self):
        row = fixture()
        row["bookmakers"][0]["status"] = "closed"
        self.assertEqual(self.call([row])[0][0]["markets"], [])
        row = fixture()
        row["bookmakers"][0]["markets"][0]["period"] = "extra_time"
        self.assertEqual(self.call([row])[0][0]["markets"], [])

    def test_conflicting_same_timestamp_copies_all_removed(self):
        row = fixture()
        other = deepcopy(row["bookmakers"][0])
        other["markets"][0]["outcomes"][0]["price"] = 1.5
        row["bookmakers"].extend([other, deepcopy(row["bookmakers"][0])])
        events, rejected = self.call([row])
        self.assertEqual(events[0]["markets"], [])
        self.assertEqual(rejected["ambiguous_book_market"], 1)

    def test_conflicting_event_identity_is_not_merged(self):
        row, other = fixture(), fixture()
        other["away_team"] = "Different Club"
        events, rejected = self.call([row, other, fixture()])
        self.assertEqual(events, [])
        self.assertEqual(rejected["conflicting_event_identity"], 1)

    def test_exchange_not_sportsbook_and_book_key_not_aliased(self):
        for key in ("betfair_ex_eu", "matchbook", "smarkets"):
            row = fixture()
            row["bookmakers"][0]["key"] = key
            events, rejected = self.call([row], {key: POLICY})
            self.assertEqual(events[0]["markets"], [])
            self.assertEqual(rejected["exchange_requires_separate_contract"], 1)
        row = fixture()
        row["bookmakers"][0]["key"] = "unibet_fr"
        self.assertEqual(self.call([row], {"unibet": POLICY})[0][0]["markets"], [])

    def test_three_references_requires_four_different_books(self):
        row = fixture()
        row["bookmakers"] = [{**deepcopy(row["bookmakers"][0]), "key": k} for k in ("pinnacle", "williamhill", "marathonbet")]
        rules = {book["key"]: POLICY for book in row["bookmakers"]}
        event = self.call([row], rules)[0][0]
        result = analyze_event(event, now=NOW)
        self.assertEqual(result["markets"][0]["filtered"]["insufficient_independent_reference_books"], 3)
        row["bookmakers"].append({**deepcopy(row["bookmakers"][0]), "key": "unibet_fr"})
        rules["unibet_fr"] = POLICY
        event = self.call([row], rules)[0][0]
        self.assertEqual(analyze_event(event, now=NOW)["markets"][0]["bookmakers"], 4)

    def test_score_completed_alone_never_settles(self):
        self.assertEqual(score_results([completed()], NOW, {}), [])
        for field in ("verified", "competition_type", "no_extra_time", "evidence_url", "reviewed_at"):
            rule = competition()
            del rule["soccer_epl"][field]
            self.assertEqual(score_results([completed()], NOW, rule), [])

    def test_score_only_reviewed_domestic_regulation(self):
        result = score_results([completed()], NOW, competition())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["outcome"], "home")
        self.assertTrue(result[0]["settlement_verified"])
        self.assertEqual(result[0]["known_at"], NOW)
        self.assertEqual(result[0]["settled_at"], NOW)

    def test_cups_aet_penalties_and_incomplete_scores_not_settled(self):
        for change in ({"sport_key": "soccer_fa_cup"}, {"status": "AET"}, {"status": "PEN"}, {"extra_time": True}, {"penalties": True}, {"completed": False}, {"scores": None}, {"last_update": "2026-10-25T07:00:00Z"}):
            self.assertEqual(score_results([{**completed(), **change}], NOW, competition()), [])

    def test_conflicting_final_results_not_counted_twice(self):
        first, second = completed(), completed()
        second["scores"][0]["score"] = "0"
        self.assertEqual(score_results([first, second, first], NOW, competition()), [])


if __name__ == "__main__":
    unittest.main()
