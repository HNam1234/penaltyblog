"""Pure-stdlib inference for exported penaltyblog Dixon-Coles parameters.

Equations follow penaltyblog/models/probabilities.pyx (MIT, Martin Eastwood).
The trainer tests parity against the original compiled prediction method.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import exp, isfinite, log

OUTCOMES = ('home', 'draw', 'away')
MIN_PROBABILITY = 0.75


def utc(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('aware_timestamp_required')
    return parsed.astimezone(timezone.utc)


def score_grid(artifact, home, away):
    """Raw exact-score distribution. 1X2 temperature does not calibrate AH/OU."""
    if home == away or home not in artifact['team_counts'] or away not in artifact['team_counts']:
        raise ValueError('team_unseen_in_training')
    if min(artifact['team_counts'][home], artifact['team_counts'][away]) < 15:
        raise ValueError('insufficient_team_history')
    params = artifact['params']
    lh = exp(params['home_advantage'] + params['attack_' + home] + params['defence_' + away])
    la = exp(params['attack_' + away] + params['defence_' + home])
    rho = params['rho']
    if not all(isfinite(x) for x in (lh, la, rho)) or not 0 < lh < 10 or not 0 < la < 10:
        raise ValueError('invalid_goal_rates')
    ph, pa = [exp(-lh)], [exp(-la)]
    for k in range(1, 20):
        ph.append(ph[-1] * lh / k)
        pa.append(pa[-1] * la / k)
    cells = []
    for h in range(20):
        for a in range(20):
            factor = (1 - rho * lh * la if (h, a) == (0, 0) else
                      1 + rho * lh if (h, a) == (0, 1) else
                      1 + rho * la if (h, a) == (1, 0) else
                      1 - rho if (h, a) == (1, 1) else 1)
            if factor < 0:
                raise ValueError('invalid_dc_correction')
            cells.append((h, a, ph[h] * pa[a] * factor))
    total = sum(p for _, _, p in cells)
    if not 0.999 < total <= 1.00001:
        raise ValueError('score_grid_tail_too_large')
    return [(h, a, p / total) for h, a, p in cells]


def predict(artifact, home, away):
    probs = dict.fromkeys(OUTCOMES, 0.0)
    for h, a, p in score_grid(artifact, home, away):
        outcome = 'home' if h > a else 'away' if a > h else 'draw'
        probs[outcome] += p
    raw = probs
    temperature = artifact.get('temperature', 1.0)
    if not 0.5 <= temperature <= 3:
        raise ValueError('invalid_temperature')
    scaled = {k: max(p, 1e-15) ** (1 / temperature) for k, p in raw.items()}
    mass = sum(scaled.values())
    return {k: p / mass for k, p in scaled.items()}


def wilson(hits, count):
    if not count:
        return None
    z, p = 1.959963984540054, hits / count
    divisor = 1 + z * z / count
    centre = (p + z * z / (2 * count)) / divisor
    half = z * ((p * (1 - p) + z * z / (4 * count)) / count) ** 0.5 / divisor
    return [max(0, centre - half), min(1, centre + half)]


def metrics(rows, threshold=MIN_PROBABILITY):
    selected = [r for r in rows if max(r['probabilities'].values()) >= threshold]
    hits = sum(max(r['probabilities'], key=r['probabilities'].get) == r['outcome'] for r in selected)
    n = len(rows)
    return {'count': n,
            'brier': sum(sum((p - (k == r['outcome'])) ** 2 for k, p in r['probabilities'].items()) for r in rows) / n if n else None,
            'log_loss': -sum(log(max(r['probabilities'][r['outcome']], 1e-15)) for r in rows) / n if n else None,
            'high_probability': {'threshold': threshold, 'count': len(selected), 'hits': hits,
                                 'hit_rate': hits / len(selected) if selected else None,
                                 'wilson95': wilson(hits, len(selected)),
                                 'mean_probability': sum(max(r['probabilities'].values()) for r in selected) / len(selected) if selected else None}}


def qualify(artifact, home, away, now):
    """No claim of calibrated true probability or profitability from a raw score."""
    now = utc(now)
    if artifact.get('schema') != 1 or utc(artifact['trained_at']) > now:
        raise ValueError('invalid_model_artifact')
    if (now.date() - datetime.fromisoformat(artifact['latest_result_date']).date()).days > 14:
        raise ValueError('training_results_stale')
    for team in (home, away):
        latest = artifact.get('team_last_result', {}).get(team)
        if not latest or (now.date() - date_from_iso(latest)).days > 21:
            raise ValueError('team_results_stale_or_missing')
    coverage = artifact.get('data_coverage', {})
    if not coverage.get('checked_at') or (now - utc(coverage['checked_at'])).total_seconds() > 36*3600:
        raise ValueError('result_coverage_check_stale_or_missing')
    if utc(coverage['checked_at']) > now:
        raise ValueError('future_coverage_check')
    if any(home in (r['home'], r['away']) or away in (r['home'], r['away'])
           for r in coverage.get('missing_past_fixtures', [])):
        raise ValueError('missing_recent_team_result')
    probabilities = predict(artifact, home, away)
    selection = max(probabilities, key=probabilities.get)
    if probabilities[selection] < MIN_PROBABILITY:
        return {'qualified': False, 'reason': 'below_75_percent', 'probabilities': probabilities}
    performance = artifact.get('validation', {}).get('high_probability', {})
    # A descriptive eligibility rule, not statistical certification.
    if (performance.get('count', 0) < 50 or (performance.get('hit_rate') or 0) < 0.75):
        return {'qualified': False, 'reason': 'high_probability_group_unverified', 'probabilities': probabilities}
    return {'qualified': True, 'selection': selection, 'probabilities': probabilities,
            'historical_group': performance, 'label': 'Dự báo thử nghiệm, chưa bảo đảm kết quả'}


def artifact_id(artifact):
    return sha256(json.dumps(artifact, sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]


def date_from_iso(value):
    return datetime.fromisoformat(value).date()
