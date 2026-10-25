"""Offline, team-only daily Elo baseline. No live picks or Telegram writes.

Ratings update after ALL predictions for the source date, avoiding within-day
result leakage when timestamps have no timezone and series IDs are unreliable.
Calibration: June-July 2026. Held-out: August 1 through the exclusive cutoff.
Side, patch, roster, draft, and current-game performance are NOT model features.
"""
from collections import Counter, defaultdict
from datetime import date, datetime
import argparse
import csv
import gzip
from hashlib import sha256
import io
import json
from math import exp, log
from pathlib import Path

CAL_START, TEST_START = '2026-06-01', '2026-08-01'
K, MIN_HISTORY, MAX_INACTIVE_DAYS = 20.0, 20, 60
TEMPERATURES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)
FIELDS = ('game_id', 'date', 'team_id', 'team_name', 'opponent_team_name', 'result', 'side', 'patch', 'league')


def read_maps(raw, cutoff):
    if len(raw) > 3_000_000:
        raise ValueError('compressed_dataset_limit')
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        inflated = stream.read(40_000_001)
    if len(inflated) > 40_000_000:
        raise ValueError('inflated_dataset_limit')
    reader = csv.DictReader(io.StringIO(inflated.decode('utf-8-sig')))
    if not set(FIELDS) <= set(reader.fieldnames or []):
        raise ValueError('unsupported_columns')
    games, filtered = defaultdict(dict), Counter()
    total = 0
    for row in reader:
        total += 1
        if total > 50000 or sum(len(str(v)) for v in row.values()) > 12000:
            raise ValueError('csv_dataset_limit')
        try:
            when = datetime.fromisoformat(row['date'])
            if when.date() >= cutoff:
                filtered['same_day_or_future_excluded'] += 1
                continue
            if row['result'] not in ('TRUE', 'FALSE') or row['side'] not in ('Blue', 'Red'):
                raise ValueError('invalid_outcome')
            if any(not row[k] or len(row[k]) > 180 or any(ord(c) < 32 for c in row[k])
                   for k in ('game_id', 'team_id', 'team_name', 'opponent_team_name', 'patch', 'league')):
                raise ValueError('invalid_identity')
            selected = {k: row[k] for k in FIELDS}
            games[row['game_id']][json.dumps(selected, sort_keys=True)] = selected
        except (ValueError, KeyError, TypeError):
            filtered['invalid_row'] += 1
    maps = []
    for game_id, unique in games.items():
        sides = sorted(unique.values(), key=lambda r: r['team_id'])
        if (len(sides) != 2 or {r['side'] for r in sides} != {'Blue', 'Red'}
                or {r['result'] for r in sides} != {'TRUE', 'FALSE'}
                or sides[0]['team_id'] == sides[1]['team_id']
                or sides[0]['team_name'] != sides[1]['opponent_team_name']
                or sides[1]['team_name'] != sides[0]['opponent_team_name']
                or any(sides[0][k] != sides[1][k] for k in ('date', 'patch', 'league'))):
            filtered['unpaired_or_conflicting_map'] += 1
            continue
        a, b = sides
        maps.append({'id': game_id, 'day': datetime.fromisoformat(a['date']).date().isoformat(),
                     'a': a['team_id'], 'b': b['team_id'], 'name_a': a['team_name'], 'name_b': b['team_name'],
                     'league': a['league'], 'patch': a['patch'], 'y': int(a['result'] == 'TRUE')})
    return sorted(maps, key=lambda r: (r['day'], r['id'])), {'rows': total, 'paired_maps': len(maps), 'filtered': dict(filtered)}


def probability(elo_difference, temperature=1.0):
    z = log(10) * elo_difference / 400 / temperature
    return 1 / (1 + exp(-max(-30, min(30, z))))


def predictions(maps):
    ratings, counts, last = defaultdict(lambda: 1500.0), Counter(), {}
    by_day, output, skipped = defaultdict(list), [], Counter()
    for row in maps:
        by_day[row['day']].append(row)
    for day, rows in sorted(by_day.items()):
        changes = defaultdict(float)
        for row in rows:
            a, b = row['a'], row['b']
            difference = ratings[a] - ratings[b]
            p = probability(difference)
            valid = min(counts[a], counts[b]) >= MIN_HISTORY
            if valid:
                valid = all((date.fromisoformat(day) - date.fromisoformat(last[t])).days <= MAX_INACTIVE_DAYS for t in (a, b))
            if day >= CAL_START:
                if valid:
                    output.append(row | {'elo_difference': difference, 'prior_maps_a': counts[a], 'prior_maps_b': counts[b],
                                         'history_through_a': last[a], 'history_through_b': last[b]})
                else:
                    skipped['insufficient_or_inactive_team_history'] += 1
            delta = K * (row['y'] - p)
            changes[a] += delta
            changes[b] -= delta
        # Nothing in this source day can affect another prediction for that day.
        for team, delta in changes.items():
            ratings[team] += delta
        for row in rows:
            for team in (row['a'], row['b']):
                counts[team] += 1
                last[team] = day
    return output, dict(skipped)


def metrics(rows, temperature=1.0, constant=False):
    if not rows:
        return {'n': 0}
    ps = [0.5 if constant else probability(r['elo_difference'], temperature) for r in rows]
    return {'n': len(rows), 'brier': sum((p - r['y']) ** 2 for p, r in zip(ps, rows)) / len(rows),
            'log_loss': -sum(r['y'] * log(p) + (1-r['y']) * log(1-p) for p, r in zip(ps, rows)) / len(rows),
            'accuracy': sum(0.5 if p == 0.5 else int((p > 0.5) == bool(r['y'])) for p, r in zip(ps, rows)) / len(rows)}


def confidence_group(rows, low, high=1.00000001):
    selected = [r for r in rows if low <= max(r['p_a'], 1-r['p_a']) < high]
    n = len(selected)
    return {'n': n, 'correct': sum((r['p_a'] > .5) == bool(r['y']) for r in selected),
            'hit_rate': sum((r['p_a'] > .5) == bool(r['y']) for r in selected) / n if n else None,
            'mean_predicted_probability': sum(max(r['p_a'], 1-r['p_a']) for r in selected) / n if n else None}


def evaluate(raw, cutoff):
    maps, quality = read_maps(raw, cutoff)
    rows, skipped = predictions(maps)
    calibration = [r for r in rows if CAL_START <= r['day'] < TEST_START]
    test = [r for r in rows if r['day'] >= TEST_START]
    if len(calibration) < 200 or len(test) < 200:
        raise ValueError('insufficient_calibration_or_test_data')
    choices = [{'temperature': t, **metrics(calibration, t)} for t in TEMPERATURES]
    temperature = min(choices, key=lambda r: (r['log_loss'], r['temperature']))['temperature']
    test = [r | {'p_a': probability(r['elo_difference'], temperature)} for r in test]
    leagues = {}
    for league in sorted({r['league'] for r in test}):
        subset = [r for r in test if r['league'] == league]
        leagues[league] = metrics(subset, temperature) | {'high_75': confidence_group(subset, .75)}
    report = {'schema': 1, 'status': 'offline_research_only', 'model': 'team_elo_daily_batch_k20_no_side_feature',
              'source': {'url': 'https://chaincc.lol/data/chaincc-teams-2026.csv.gz', 'license': 'CC BY 4.0',
                         'attribution': 'ChainCC Open LoL Esports Dataset', 'sha256': sha256(raw).hexdigest(),
                         'changes': 'Paired reciprocal results; chronological team-only Elo evaluation.'},
              'cutoff_exclusive': cutoff.isoformat(), 'data_quality': quality, 'skipped': skipped,
              'protocol': {'warmup_before': CAL_START, 'calibration_from': CAL_START, 'test_from': TEST_START,
                           'test_through': max(r['day'] for r in test), 'min_prior_maps_each_team': MIN_HISTORY,
                           'max_inactive_days': MAX_INACTIVE_DAYS, 'rating_updates': 'after_all_maps_of_each_source_day',
                           'temperature': temperature, 'selection_metric': 'calibration_log_loss', 'candidate_temperatures': list(TEMPERATURES)},
              'calibration_candidates': choices, 'test': metrics(test, temperature), 'raw_elo_test': metrics(test),
              'constant_50_test': metrics(test, constant=True), 'high_75': confidence_group(test, .75),
              'reliability': [{'from': low, 'to_exclusive': high, **confidence_group(test, low, high)}
                              for low, high in ((.5, .6), (.6, .7), (.7, .75), (.75, .8), (.8, .9), (.9, 1.00000001))],
              'leagues': leagues, 'production_alerts_enabled': False, 'roi': None,
              'limitations': ['One current dataset edition, not archived live vintages.',
                             'No roster, transfers, patch, region-transfer or champion-draft adjustment.',
                             'Map-level results; no reliable series IDs or independence claim.',
                             'Same-day outcomes excluded from all same-day forecasts; daily updates during held-out evaluation.',
                             'No live odds feed, price or settlement-rule validation; no profit backtest.',
                             'No significance interval: maps may cluster within series; per-league samples can be small.']}
    return report, test


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--cutoff', default='2026-10-25')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report, rows = evaluate(Path(args.dataset).read_bytes(), date.fromisoformat(args.cutoff))
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    (output / 'lol-baseline-evaluation.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    (output / 'lol-heldout-predictions.json').write_text(json.dumps(rows, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({k: report[k] for k in ('data_quality', 'protocol', 'test', 'constant_50_test', 'high_75', 'production_alerts_enabled')}, ensure_ascii=True))
