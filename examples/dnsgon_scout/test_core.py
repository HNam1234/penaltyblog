"""Offline, synthetic-fixture tests. These are not historical profit results."""

from copy import deepcopy
import math
import unittest

from core import LABEL, METHOD, analyze_event, normalize_snapshot, remove_margin, score_predictions


NOW = "2026-10-25T07:00:00Z"


def event(sport="soccer"):
    period = {"soccer": "regulation", "basketball": "full_game_including_overtime", "tennis": "full_match"}[sport]
    prices = [(1.4, 6, 8), (1.2, 8, 14), (1.21, 8.2, 14.2), (1.19, 8.1, 14.1)]
    rows = []
    for index, odds in enumerate(prices):
        if sport != "soccer":
            odds = (1.4, 3) if index == 0 else (1.2, 5)
        names = ("home", "draw", "away") if sport == "soccer" else ("home", "away")
        rows.append({"provider": "fixture-provider", "bookmaker": f"book-{index}",
                     "as_of": "2026-10-25T06:59:00Z", "market": "h2h", "period": period,
                     "outcomes": dict(zip(names, odds))})
    return {"event_id": "synthetic-event", "sport": sport, "home": "Synthetic Home", "away": "Synthetic Away",
            "start_time": "2026-10-25T08:00:00Z", "markets": rows}


def forecast():
    return {"event_id": "synthetic-event", "sport": "soccer", "market": "1x2", "period": "regulation", "line": None,
            "start_time": "2026-10-25T08:00:00Z", "created_at": NOW, "data_as_of": "2026-10-25T06:59:00Z",
            "selection": "home", "probabilities": {"home": 0.8, "draw": 0.1, "away": 0.1}}


def result(**changes):
    row = {"event_id": "synthetic-event", "sport": "soccer", "market": "1x2", "period": "regulation", "line": None,
           "outcome": "home", "settled_at": "2026-10-25T10:00:00Z", "known_at": "2026-10-25T10:01:00Z",
           "status": "final", "settlement_verified": True}
    return {**row, **changes}


SCORE_NOW = "2026-10-25T11:00:00Z"


class MarginTests(unittest.TestCase):
    def test_complete_market_sums_to_one(self):
        probabilities, overround = remove_margin({"home": 2, "draw": 3, "away": 4})
        self.assertAlmostEqual(sum(probabilities.values()), 1)
        self.assertAlmostEqual(overround, 1 / 12)
        self.assertAlmostEqual(probabilities["home"], 6 / 13)

    def test_two_way_margin_removed(self):
        probabilities, overround = remove_margin({"home": 1.9, "away": 1.9})
        self.assertEqual(probabilities, {"home": 0.5, "away": 0.5})
        self.assertGreater(overround, 0)

    def test_bad_prices_rejected(self):
        for price in (1, 0, -2, float("nan"), float("inf"), True, None, 1001, "bad"):
            with self.subTest(price=price), self.assertRaises(ValueError):
                remove_margin({"home": price, "away": 2})

    def test_missing_outcomes_rejected(self):
        with self.assertRaisesRegex(ValueError, "incomplete_outcomes"):
            remove_margin({"home": 2})


class NormalizationTests(unittest.TestCase):
    def test_named_participants_and_draw_alias(self):
        sample = event()
        row = sample["markets"][0]
        row["outcomes"] = {"SYNTHETIC HOME": 1.4, "Tie": 6, "Synthetic Away": 8}
        normalized = normalize_snapshot(sample, row, now=NOW)
        self.assertEqual(normalized["key"], ("1x2", "regulation", None))
        self.assertEqual(set(normalized["odds"]), {"home", "draw", "away"})

    def test_missing_football_draw_rejected(self):
        sample = event()
        del sample["markets"][0]["outcomes"]["draw"]
        with self.assertRaisesRegex(ValueError, "incomplete_outcomes"):
            normalize_snapshot(sample, sample["markets"][0], now=NOW)

    def test_duplicate_outcomes_rejected(self):
        sample = event()
        sample["markets"][0]["outcomes"] = [{"name": name, "odds": odds} for name, odds in
                                             (("home", 1.4), ("draw", 6), ("away", 8), ("Synthetic Home", 1.4))]
        with self.assertRaisesRegex(ValueError, "unexpected_or_duplicate_outcome"):
            normalize_snapshot(sample, sample["markets"][0], now=NOW)

    def test_stale_and_future_snapshots_rejected(self):
        sample = event()
        for timestamp, reason in (("2026-10-25T06:54:59Z", "stale_snapshot"),
                                  ("2026-10-25T07:00:06Z", "future_snapshot")):
            row = {**sample["markets"][0], "as_of": timestamp}
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, reason):
                normalize_snapshot(sample, row, now=NOW)

    def test_small_clock_skew_permitted_and_marked_zero_age(self):
        sample = event()
        row = {**sample["markets"][0], "as_of": "2026-10-25T07:00:05Z"}
        self.assertEqual(normalize_snapshot(sample, row, now=NOW)["age_seconds"], 0)

    def test_naive_timestamp_rejected(self):
        sample = event()
        row = {**sample["markets"][0], "as_of": "2026-10-25T06:59:00"}
        with self.assertRaisesRegex(ValueError, "invalid_aware_timestamp"):
            normalize_snapshot(sample, row, now=NOW)

    def test_in_play_closed_and_post_start_not_used(self):
        sample = event()
        for changes, reason in (({"in_play": True}, "not_open_prematch"),
                                ({"status": "suspended"}, "not_open_prematch"),
                                ({"as_of": sample["start_time"]}, "post_start_snapshot")):
            row = {**sample["markets"][0], **changes}
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, reason):
                normalize_snapshot(sample, row, now=sample["start_time"])

    def test_period_must_be_explicit_and_compatible(self):
        sample = event()
        for period in (None, "full_match", "including_overtime", "qualification"):
            with self.subTest(period=period), self.assertRaisesRegex(ValueError, "missing_or_incompatible_period"):
                normalize_snapshot(sample, {**sample["markets"][0], "period": period}, now=NOW)

    def test_mismatched_event_and_sport_rejected(self):
        sample = event()
        for changes, reason in (({"event_id": "different"}, "incompatible_event"),
                                ({"sport": "tennis"}, "incompatible_sport")):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, reason):
                normalize_snapshot(sample, {**sample["markets"][0], **changes}, now=NOW)

    def test_source_identity_required(self):
        sample = event()
        for key in ("bookmaker", "provider"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "missing_source_identity"):
                normalize_snapshot(sample, {**sample["markets"][0], key: ""}, now=NOW)

    def test_exchange_commission_reduces_executable_odds(self):
        sample = event()
        row = {**sample["markets"][0], "commission": 0.05}
        normalized = normalize_snapshot(sample, row, now=NOW)
        self.assertAlmostEqual(normalized["odds"]["home"], 1 + 0.4 * 0.95)

    def test_only_half_total_lines_supported(self):
        sample = event()
        base = {**sample["markets"][0], "market": "totals", "outcomes": {"over": 1.9, "under": 1.9}}
        self.assertEqual(normalize_snapshot(sample, {**base, "line": 2.5}, now=NOW)["key"], ("totals", "regulation", 2.5))
        for line in (2, 2.25, 2.75, 0, None):
            with self.subTest(line=line), self.assertRaises(ValueError):
                normalize_snapshot(sample, {**base, "line": line}, now=NOW)

    def test_overround_outlier_rejected(self):
        sample = event()
        row = {**sample["markets"][0], "outcomes": {"home": 1.01, "draw": 1.01, "away": 1.01}}
        with self.assertRaisesRegex(ValueError, "implausible_overround"):
            normalize_snapshot(sample, row, now=NOW)


class AnalysisTests(unittest.TestCase):
    def test_shortlist_probability_excludes_own_book(self):
        sample = event()
        report = analyze_event(sample, now=NOW)
        candidate = next(row for row in report["candidates"] if row["bookmaker"] == "book-0")
        expected = sum(remove_margin(row["outcomes"])[0]["home"] for row in sample["markets"][1:]) / 3
        self.assertAlmostEqual(candidate["probability"], expected)
        self.assertNotIn("book-0", candidate["reference_books"])
        self.assertEqual(len(candidate["reference_books"]), 3)
        self.assertEqual(candidate["label"], LABEL)
        self.assertEqual(candidate["method"], METHOD)
        self.assertAlmostEqual(candidate["estimated_edge"], expected * 1.4 - 1)

    def test_mirrored_bookmaker_does_not_add_independent_reference(self):
        sample = event()
        sample["markets"] = sample["markets"][:3]
        mirror = {**sample["markets"][1], "bookmaker": "BOOK-1", "provider": "another-provider"}
        sample["markets"].append(mirror)
        report = analyze_event(sample, now=NOW)
        self.assertEqual(report["markets"][0]["bookmakers"], 3)
        self.assertEqual(report["candidates"], [])

    def test_newest_mirror_selected(self):
        sample = event()
        mirror = deepcopy(sample["markets"][1])
        mirror["as_of"] = "2026-10-25T06:59:30Z"
        sample["markets"].append(mirror)
        report = analyze_event(sample, now=NOW)
        candidate = next(row for row in report["candidates"] if row["bookmaker"] == "book-0")
        self.assertEqual(candidate["data_as_of"], mirror["as_of"])
        self.assertEqual(candidate["oldest_snapshot_at"], "2026-10-25T06:59:00Z")

    def test_tennis_and_basketball_moneyline(self):
        for sport in ("tennis", "basketball"):
            with self.subTest(sport=sport):
                report = analyze_event(event(sport), now=NOW)
                self.assertTrue(report["candidates"])
                self.assertEqual(report["candidates"][0]["market"], "moneyline")

    def test_missing_odds_reported_without_candidate(self):
        sample = event()
        sample["markets"] = []
        self.assertEqual(analyze_event(sample, now=NOW)["skipped"], [{"reason": "missing_odds"}])

    def test_started_and_missing_metadata_reported(self):
        for changes, reason in (({"start_time": NOW}, "event_started"), ({"home": ""}, "missing_event_metadata"),
                                ({"away": "SYNTHETIC HOME"}, "identical_participants"), ({"sport": "golf"}, "unsupported_sport")):
            report = analyze_event({**event(), **changes}, now=NOW)
            with self.subTest(changes=changes):
                self.assertEqual(report["skipped"], [{"reason": reason}])
                self.assertEqual(report["candidates"], [])

    def test_malformed_market_rows_do_not_crash(self):
        sample = {**event(), "markets": [None, "bad"]}
        report = analyze_event(sample, now=NOW)
        self.assertEqual(len(report["skipped"]), 2)

    def test_distinct_lines_never_combined(self):
        sample = event()
        for index, row in enumerate(sample["markets"]):
            row.update(market="totals", line=2.5 if index < 2 else 3.5, outcomes={"over": 1.9, "under": 1.9})
        report = analyze_event(sample, now=NOW)
        self.assertEqual(len(report["markets"]), 2)
        self.assertEqual(report["candidates"], [])

    def test_disagreement_filters_reference_set(self):
        sample = event()
        sample["markets"][3]["outcomes"] = {"home": 2, "draw": 4, "away": 4}
        report = analyze_event(sample, now=NOW)
        self.assertFalse(any(row["bookmaker"] == "book-0" for row in report["candidates"]))
        self.assertGreater(report["markets"][0]["filtered"].get("reference_disagreement", 0), 0)

    def test_optional_provider_requirement(self):
        sample = event()
        report = analyze_event(sample, now=NOW, config={"min_reference_providers": 2})
        self.assertEqual(report["candidates"], [])

    def test_candidate_id_stable_for_contract(self):
        first = analyze_event(event(), now=NOW)["candidates"][0]
        second = analyze_event(event(), now="2026-10-25T07:00:01Z")["candidates"][0]
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["created_at"], second["created_at"])

    def test_invalid_config_not_silently_used(self):
        for config in ({"min_reference_books": 0}, {"min_reference_books": 1.5}, {"min_probability": 1},
                       {"min_edge": -1}, {"max_age_seconds": 0}, {"unknown": 1}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                analyze_event(event(), now=NOW, config=config)


class ForwardScoringTests(unittest.TestCase):
    def score(self, predictions=None, results=None, **kwargs):
        return score_predictions(predictions if predictions is not None else [forecast()],
                                 results if results is not None else [result()], now=SCORE_NOW, **kwargs)

    def test_brier_logloss_and_hit_rate_are_descriptive_only(self):
        report = self.score()
        self.assertAlmostEqual(report["metrics"]["brier"], 0.06)
        self.assertAlmostEqual(report["metrics"]["log_loss"], -math.log(0.8))
        self.assertEqual(report["metrics"]["selection_hit_rate"], 1)
        self.assertEqual(report["sample_status"], "insufficient_sample")
        self.assertEqual(report["label"], LABEL)
        self.assertNotIn("roi", report["metrics"])
        self.assertNotIn("profit", report)
        self.assertEqual(report["metrics"]["calibration"][0]["mean_top_probability"], 0.8)

    def test_empty_sample_does_not_imply_zero_error(self):
        report = self.score(predictions=[])
        self.assertIsNone(report["metrics"]["brier"])
        self.assertIsNone(report["metrics"]["selection_hit_rate"])

    def test_no_lookahead_in_forecasts(self):
        for changes in ({"created_at": "2026-10-25T08:00:00Z"},
                        {"data_as_of": "2026-10-25T07:00:01Z"},
                        {"created_at": "2026-10-25T12:00:00Z", "start_time": "2026-10-25T13:00:00Z"}):
            report = self.score(predictions=[{**forecast(), **changes}])
            with self.subTest(changes=changes):
                self.assertEqual(report["metrics"]["count"], 0)
                self.assertIn("forecast_leakage_or_future", report["excluded"])

    def test_earliest_forecast_uses_actual_time_across_offsets(self):
        earlier = {**forecast(), "created_at": "2026-10-25T07:00:00+07:00", "data_as_of": "2026-10-25T00:00:00Z",
                   "probabilities": {"home": 0.6, "draw": 0.2, "away": 0.2}}
        later = {**forecast(), "created_at": "2026-10-25T01:00:00Z", "data_as_of": "2026-10-25T00:59:00Z"}
        report = self.score(predictions=[later, earlier])
        self.assertEqual(report["metrics"]["count"], 1)
        self.assertAlmostEqual(report["metrics"]["brier"], 0.24)
        self.assertEqual(report["excluded"]["duplicate_forecast"], 1)

    def test_future_or_impossible_settlement_rejected(self):
        for settled_at in ("2026-10-25T12:00:00Z", "2026-10-25T07:59:00Z"):
            report = self.score(results=[result(settled_at=settled_at)])
            with self.subTest(settled_at=settled_at):
                self.assertEqual(report["metrics"]["count"], 0)
                self.assertIn("future_or_impossible_result", report["excluded"])

    def test_result_must_already_be_known_to_system(self):
        for known_at in ("2026-10-25T12:00:00Z", "2026-10-25T09:59:00Z"):
            report = self.score(results=[result(known_at=known_at)])
            with self.subTest(known_at=known_at):
                self.assertEqual(report["metrics"]["count"], 0)
                self.assertIn("future_or_impossible_result_knowledge", report["excluded"])

    def test_missing_known_at_excluded(self):
        settled = result()
        del settled["known_at"]
        self.assertEqual(self.score(results=[settled])["metrics"]["count"], 0)

    def test_provisional_unverified_void_and_ambiguous_excluded(self):
        for rows, reason in (([result(status="live")], "unverified_settlement"),
                             ([result(settlement_verified=False)], "unverified_settlement"),
                             ([result(outcome="void")], "void_push_or_unknown_outcome"),
                             ([result(), result()], "ambiguous_result")):
            report = self.score(results=rows)
            with self.subTest(reason=reason):
                self.assertEqual(report["metrics"]["count"], 0)
                self.assertIn(reason, report["excluded"])

    def test_other_period_result_does_not_settle_forecast(self):
        report = self.score(results=[result(period="including_overtime")])
        self.assertEqual(report["excluded"], {"missing_result": 1})

    def test_invalid_probability_distribution_rejected(self):
        for probabilities in ({"home": 0.8, "away": 0.2}, {"home": 0.8, "draw": 0.2, "away": 0.2},
                              {"home": float("nan"), "draw": 0.1, "away": 0.1}, {"home": 1.2, "draw": -0.1, "away": -0.1}):
            with self.subTest(probabilities=probabilities):
                self.assertEqual(self.score(predictions=[{**forecast(), "probabilities": probabilities}])["metrics"]["count"], 0)

    def test_malformed_rows_are_excluded_not_crashes(self):
        report = self.score(predictions=[None, {**forecast(), "line": {}}, forecast()], results=[None, {"line": {}}, result()])
        self.assertEqual(report["metrics"]["count"], 1)
        self.assertEqual(report["excluded"]["invalid_result_row"], 2)

    def test_enough_descriptive_samples_still_unvalidated(self):
        predictions, results = [], []
        for index in range(100):
            predictions.append({**forecast(), "event_id": f"synthetic-{index}"})
            results.append(result(event_id=f"synthetic-{index}"))
        report = self.score(predictions, results)
        self.assertEqual(report["sample_status"], "descriptive_sample_available")
        self.assertEqual(report["label"], LABEL)
        self.assertEqual(report["metrics"]["count"], 100)

    def test_mixed_contracts_have_separate_metrics(self):
        tennis = {**forecast(), "event_id": "tennis-fixture", "sport": "tennis", "market": "moneyline", "period": "full_match",
                  "probabilities": {"home": 0.8, "away": 0.2}}
        tennis_result = result(event_id="tennis-fixture", sport="tennis", market="moneyline", period="full_match")
        report = self.score([forecast(), tennis], [result(), tennis_result])
        self.assertEqual(len(report["by_contract"]), 2)
        self.assertEqual(report["metrics"]["count"], 2)


if __name__ == "__main__":
    unittest.main()
