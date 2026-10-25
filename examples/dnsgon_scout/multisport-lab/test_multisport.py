from datetime import date
from math import comb
import csv
import gzip
import io
import unittest

from evaluate_lol import FIELDS, predictions, probability, read_maps
from series_probability import series_distribution, series_win_probability


class SeriesMath(unittest.TestCase):
    def test_closed_forms_and_terminal_mass(self):
        for p in (0, .1, .4, .5, .6, .9, 1):
            self.assertAlmostEqual(series_win_probability(3, p), 3*p**2 - 2*p**3)
            self.assertAlmostEqual(series_win_probability(5, p), 10*p**3 - 15*p**4 + 6*p**5)
            for best_of in (1, 3, 5, 7, 9):
                target = best_of // 2 + 1
                distribution = series_distribution(best_of, p)
                self.assertAlmostEqual(sum(distribution.values()), 1)
                self.assertTrue(all((a == target) ^ (b == target) for a, b in distribution))
                expected = sum(comb(best_of, wins) * p**wins * (1-p)**(best_of-wins)
                               for wins in range(target, best_of+1))
                self.assertAlmostEqual(series_win_probability(best_of, p), expected)

    def test_score_conditional_and_already_finished(self):
        self.assertAlmostEqual(series_win_probability(3, .6, (1, 0)), 1 - .4**2)
        self.assertEqual(series_distribution(3, {}, (2, 1)), {(2, 1): 1.0})
        probabilities = {(0, 0): .6, (1, 0): .8, (0, 1): .3, (1, 1): .4}
        expected = .6 * (.8 + .2*.4) + .4*.3*.4
        self.assertAlmostEqual(series_win_probability(3, probabilities), expected)

    def test_swap_symmetry_and_validation(self):
        for p in (.2, .4, .8):
            self.assertAlmostEqual(series_win_probability(5, p), 1 - series_win_probability(5, 1-p))
        for best_of in (0, 2, 10, True, 3.0):
            with self.assertRaises(ValueError):
                series_distribution(best_of, .5)
        for p in (True, -.1, 1.1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                series_distribution(3, p)
        with self.assertRaises(ValueError):
            series_distribution(3, .5, (2, 2))


def sample(game='one', day='2026-05-31 10:00:00'):
    common = {'game_id': game, 'date': day, 'patch': '16.10', 'league': 'Example'}
    return [common | dict(team_id='a', team_name='Alpha', opponent_team_name='Beta', side='Blue', result='TRUE'),
            common | dict(team_id='b', team_name='Beta', opponent_team_name='Alpha', side='Red', result='FALSE')]


def pack(rows):
    text = io.StringIO(); writer = csv.DictWriter(text, fieldnames=FIELDS)
    writer.writeheader(); writer.writerows(rows)
    return gzip.compress(text.getvalue().encode())


class LoLProtocol(unittest.TestCase):
    def test_reciprocal_pairs_duplicates_conflicts_and_future(self):
        rows = sample() + sample() + sample('future', '2026-10-25 12:00:00')
        rows += sample('broken')[:1]
        bad = sample('conflict'); bad.append(bad[0] | {'result': 'FALSE'}); rows += bad
        parsed, quality = read_maps(pack(rows), date(2026, 10, 25))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(quality['filtered']['same_day_or_future_excluded'], 2)
        self.assertEqual(quality['filtered']['unpaired_or_conflicting_map'], 2)
        self.assertEqual((parsed[0]['a'], parsed[0]['b'], parsed[0]['y']), ('a', 'b', 1))

    def test_day_boundary_and_prefix_invariance(self):
        def row(day, identity, y):
            return dict(day=day, id=identity, a='a', b='b', y=y, league='Example')
        warmup = [row('2026-05-31', str(i), 1) for i in range(20)]
        same_day = [row('2026-06-01', 'x', 0), row('2026-06-01', 'y', 1)]
        before, _ = predictions(warmup + same_day)
        after, _ = predictions(warmup + same_day + [row('2026-06-02', 'z', 0)])
        self.assertEqual(before, after[:len(before)])
        self.assertEqual(before[0]['elo_difference'], before[1]['elo_difference'])
        self.assertTrue(all(r['history_through_a'] < r['day'] and r['history_through_b'] < r['day'] for r in after))
        changed, _ = predictions(warmup + [r | {'y': 1-r['y']} for r in same_day])
        self.assertEqual([r['elo_difference'] for r in before], [r['elo_difference'] for r in changed])

    def test_stale_or_insufficient_history_is_excluded(self):
        warmup = [dict(day='2026-01-01', id=str(i), a='a', b='b', y=1) for i in range(20)]
        results, skipped = predictions(warmup + [dict(day='2026-06-01', id='stale', a='a', b='b', y=1)])
        self.assertEqual(results, [])
        self.assertEqual(skipped['insufficient_or_inactive_team_history'], 1)
        for difference in (-1e6, 0, 1e6):
            self.assertTrue(0 < probability(difference) < 1)


if __name__ == '__main__':
    unittest.main()
