"""Retrospective chronological evaluation using CC0 OpenFootball results.

2016–2019 warm-up; 2019/20 temperature selection; 2020–2026 held-out scoring.
Refit each match day using earlier matches only. Live
training uses the same trailing three years; no bookmaker odds enter fitting.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import argparse
import json
from math import exp, log
from pathlib import Path
import sys

from dc_runtime import predict, metrics

# OpenFootball used shorter names before 2020/21; retain each club's history.
ALIASES = {'Aston Villa':'Aston Villa FC', 'Brighton & Hove Albion':'Brighton & Hove Albion FC',
           'Crystal Palace':'Crystal Palace FC', 'Hull City':'Hull City AFC',
           'Leicester City':'Leicester City FC', 'Manchester City':'Manchester City FC',
           'Manchester United':'Manchester United FC', 'Newcastle United':'Newcastle United FC',
           'Norwich City':'Norwich City FC', 'Sheffield United':'Sheffield United FC',
           'Tottenham Hotspur':'Tottenham Hotspur FC', 'West Bromwich Albion':'West Bromwich Albion FC',
           'West Ham United':'West Ham United FC', 'Wolverhampton Wanderers':'Wolverhampton Wanderers FC'}


def load_results(folder):
    rows = {}
    for path in sorted(Path(folder).glob('*.json')):
        body = json.loads(path.read_text(encoding='utf-8'))
        matches = body.get('matches', []) if isinstance(body, dict) else body
        for m in matches:
            score = m.get('score')
            score = score.get('ft') if isinstance(score, dict) else score
            if not isinstance(score, list) or len(score) != 2 or any(type(g) is not int or g < 0 for g in score):
                continue
            when = date.fromisoformat(m['date'])
            if when >= datetime.now(timezone.utc).date():
                continue
            home, away = (ALIASES.get(m[k], m[k]) for k in ('team1','team2'))
            key = (m['date'], home, away)
            record = {'date': m['date'], 'home': home, 'away': away, 'hg': score[0], 'ag': score[1]}
            if key in rows and rows[key] != record:
                raise ValueError('conflicting_result')
            rows[key] = record
    return sorted(rows.values(), key=lambda r: (r['date'], r['home'], r['away']))


def fit(rows, cutoff):
    from penaltyblog.models import DixonColesGoalModel
    cutoff = date.fromisoformat(str(cutoff))
    rows = [r for r in rows if cutoff - timedelta(days=3*365) <= date.fromisoformat(r['date']) < cutoff]
    if len(rows) < 200:
        raise ValueError('insufficient_training_matches')
    weights = [exp(-log(2) * (cutoff - date.fromisoformat(r['date'])).days / 365) for r in rows]
    model = DixonColesGoalModel([r['hg'] for r in rows], [r['ag'] for r in rows],
                               [r['home'] for r in rows], [r['away'] for r in rows], weights=weights)
    model.fit(minimizer_options={'maxiter': 400, 'ftol': 1e-8})
    artifact = {'schema': 1, 'method': 'penaltyblog_dixon_coles_v1',
                'params': {k: float(v) for k, v in model.get_params().items()},
                'team_counts': dict(Counter([r['home'] for r in rows] + [r['away'] for r in rows])),
                'team_last_result': {t: max(r['date'] for r in rows if t in (r['home'], r['away']))
                                     for t in set([r['home'] for r in rows] + [r['away'] for r in rows])},
                'temperature': 1.0, 'train_cutoff': cutoff.isoformat(),
                'latest_result_date': max(r['date'] for r in rows),
                'training_count': len(rows), 'trained_at': datetime.now(timezone.utc).isoformat(),
                'runtime_package': 'penaltyblog==1.13.1'}
    # Assert parity on a real pair before exporting for the stdlib VPS worker.
    sample = next(r for r in reversed(rows) if min(artifact['team_counts'][r['home']], artifact['team_counts'][r['away']]) >= 15)
    expected = model.predict(sample['home'], sample['away'], max_goals=20)
    actual = predict(artifact, sample['home'], sample['away'])
    reference = {'home': expected.home_win, 'draw': expected.draw, 'away': expected.away_win}
    if max(abs(actual[k] - reference[k]) for k in reference) > 1e-8:
        raise ValueError('exported_model_parity_failed')
    return artifact


def evaluate(rows):
    outputs, skipped, model, fitted_day = [], Counter(), None, None
    for row in rows:
        if row['date'] < '2019-08-01' or row['date'] >= '2026-08-01':
            continue
        current_day = row['date']
        if fitted_day != current_day:
            fitted_day = current_day
            try:
                model = fit(rows, current_day)
            except (ValueError, RuntimeError) as error:
                model = None
                skipped['fit_failed'] += 1
            print('fit ' + current_day, file=sys.stderr, flush=True)
        if model is None:
            continue
        try:
            probabilities = predict(model, row['home'], row['away'])
            outcome = 'home' if row['hg'] > row['ag'] else 'away' if row['ag'] > row['hg'] else 'draw'
            outputs.append({**row, 'outcome': outcome, 'probabilities': probabilities,
                            'training_cutoff': model['train_cutoff']})
        except ValueError as error:
            skipped[str(error)] += 1
    calibration = [r for r in outputs if r['date'] < '2020-08-01']
    test = [r for r in outputs if '2020-08-01' <= r['date'] < '2026-08-01']
    if not calibration or not test:
        raise ValueError('missing_chronological_split')
    def transformed(rows, temperature):
        result = []
        for row in rows:
            probs = {k: max(p, 1e-15) ** (1/temperature) for k, p in row['probabilities'].items()}
            total = sum(probs.values())
            result.append({**row, 'probabilities': {k: p/total for k, p in probs.items()}})
        return result
    temperatures = [0.75 + i*0.05 for i in range(26)]
    temperature = min(temperatures, key=lambda t: metrics(transformed(calibration, t))['log_loss'])
    calibrated_test = transformed(test, temperature)
    report = {'calibration_period': ['2019-08-01', '2020-07-31'],
              'test_period': ['2020-08-01', max(r['date'] for r in test)],
              'temperature': temperature, 'calibration': metrics(transformed(calibration, temperature)),
              'validation': metrics(calibrated_test), 'skipped': dict(skipped),
              'evaluation_kind': 'retrospective_chronological_no_live_vintage_claim',
              'profitability_evaluated': False}
    return report, calibrated_test


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--live-only', action='store_true')
    args = parser.parse_args()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    rows = load_results(args.data)
    if args.live_only:
        report = json.loads((output/'validation.json').read_text())
    else:
        report, predictions = evaluate(rows)
        (output/'validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        (output/'retrospective-predictions.json').write_text(json.dumps(predictions), encoding='utf-8')
    artifact = fit(rows, datetime.now(timezone.utc).date())
    artifact.update(temperature=report['temperature'], validation=report['validation'],
                    evaluation_kind=report['evaluation_kind'])
    coverage = Path(args.data)/'coverage.json'
    if coverage.exists():
        artifact['data_coverage'] = json.loads(coverage.read_text(encoding='utf-8'))
    pending = output/'model.json.tmp'
    pending.write_text(json.dumps(artifact, indent=2), encoding='utf-8')
    pending.replace(output/'model.json')
    print(json.dumps({'artifact_written': True, 'validation': artifact['validation'],
                      'latest_result_date': artifact['latest_result_date'], 'temperature': artifact['temperature']}))


if __name__ == '__main__':
    main()
