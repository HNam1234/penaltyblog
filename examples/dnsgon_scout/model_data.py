"""Public results only: CC0 archive plus corroborated resolved sports markets."""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from adapters import _read_json, _NoRedirect

CLAUSE = 'This market refers only to the outcome within the first 90 minutes of regular play plus stoppage time.'
CLUB_CODES = {
    'Arsenal FC': 'ars', 'Aston Villa FC': 'ast', 'AFC Bournemouth': 'bou',
    'Brentford FC': 'bre', 'Brighton & Hove Albion FC': 'bri', 'Burnley FC': 'bur',
    'Chelsea FC': 'che', 'Coventry City FC': 'cov', 'Crystal Palace FC': 'cry',
    'Everton FC': 'eve', 'Fulham FC': 'ful', 'Hull City AFC': 'hul', 'Ipswich Town FC': 'ips',
    'Leeds United FC': 'lee', 'Leicester City FC': 'lei', 'Liverpool FC': 'liv',
    'Manchester City FC': 'mac', 'Manchester United FC': 'mun', 'Newcastle United FC': 'new',
    'Nottingham Forest FC': 'not', 'Southampton FC': 'sou', 'Sunderland AFC': 'sun',
    'Tottenham Hotspur FC': 'tot', 'West Ham United FC': 'whu', 'Wolverhampton Wanderers FC': 'wol',
}


def result_from_event(event, fixture, known_at):
    """Score agrees with all three final 90-minute contract resolutions.

    VFT alone is not treated as proof. Require ended + closed, full-time
    status, exact fixture identity/date and resolved contracts. Conflicting,
    cancelled, postponed, unsettled and 50/50 resolutions are excluded.
    """
    if (event.get('closed') is not True or event.get('ended') is not True
            or event.get('period') not in {'FT', 'VFT'}
            or event.get('title') != fixture['team1'] + ' vs. ' + fixture['team2']):
        return None
    if not str(event.get('startTime', '')).startswith(fixture['date'] + 'T'):
        return None
    match = re.fullmatch(r'([0-9]{1,2})-([0-9]{1,2})', str(event.get('score')))
    if not match:
        return None
    hg, ag = map(int, match.groups())
    winner = 'home' if hg > ag else 'away' if ag > hg else 'draw'
    resolutions = {}
    for market in event.get('markets', []):
        if market.get('sportsMarketType') != 'moneyline':
            continue
        if (market.get('closed') is not True or market.get('umaResolutionStatus') != 'resolved'
                or CLAUSE not in market.get('description', '')):
            return None
        question = market.get('question', '')
        role = ('home' if question == f"Will {fixture['team1']} win on {fixture['date']}?" else
                'away' if question == f"Will {fixture['team2']} win on {fixture['date']}?" else
                'draw' if question == f"Will {fixture['team1']} vs. {fixture['team2']} end in a draw?" else None)
        if role is None or role in resolutions:
            return None
        outcomes = json.loads(market['outcomes']) if isinstance(market['outcomes'], str) else market['outcomes']
        prices = json.loads(market['outcomePrices']) if isinstance(market['outcomePrices'], str) else market['outcomePrices']
        if set(outcomes) != {'Yes', 'No'} or len(prices) != 2:
            return None
        yes = str(prices[outcomes.index('Yes')])
        no = str(prices[outcomes.index('No')])
        if (yes, no) not in {('0', '1'), ('1', '0')}:
            return None
        resolutions[role] = yes == '1'
    if set(resolutions) != {'home', 'draw', 'away'} or resolutions != {r: r == winner for r in resolutions}:
        return None
    return {**fixture, 'score': {'ft': [hg, ag]},
            'source': 'polymarket_resolved_score', 'known_at': known_at,
            'source_event_id': str(event['id']), 'start_time': event['startTime']}


def public_json(url, headers, timeout, size):
    if urlsplit(url).hostname != 'raw.githubusercontent.com':
        return _read_json(url, headers, timeout, size)
    # GitHub's documented raw JSON files are served as text/plain.
    with build_opener(_NoRedirect()).open(Request(url, headers={'User-Agent': 'DNSGon-Model/1'}), timeout=timeout) as response:
        raw = response.read(size + 1)
    if len(raw) > size:
        raise ValueError('public_result_file_too_large')
    return json.loads(raw.decode('utf-8')), {}


def refresh(folder, now=None, fetch=public_json):
    now = now or datetime.now(timezone.utc)
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    start_year = now.year if now.month >= 7 else now.year - 1
    season = f'{start_year}-{str(start_year+1)[-2:]}'
    body, _ = fetch('https://raw.githubusercontent.com/openfootball/football.json/master/' + season + '/en.1.json', {}, 10, 2_000_000)
    (folder/(season+'.json')).write_text(json.dumps(body, ensure_ascii=False), encoding='utf-8')
    old_path = folder/'resolved-live-results.json'
    old = json.loads(old_path.read_text(encoding='utf-8')) if old_path.exists() else {'matches': []}
    rows = {(r['date'], r['team1'], r['team2']): r for r in old['matches']}
    requested = added = 0
    errors = []
    for fixture in body['matches']:
        score = fixture.get('score')
        if isinstance(score, dict) and isinstance(score.get('ft'), list) or isinstance(score, list) and len(score) == 2:
            continue
        if date.fromisoformat(fixture['date']) >= now.date():
            continue
        key = (fixture['date'], fixture['team1'], fixture['team2'])
        if key in rows:
            continue
        if requested >= 60:
            errors.append('bounded_recent_results_page')
            break
        h, a = CLUB_CODES.get(fixture['team1']), CLUB_CODES.get(fixture['team2'])
        if not h or not a:
            errors.append('unmapped_club_code')
            continue
        slug = f"epl-{h}-{a}-{fixture['date']}"
        requested += 1
        try:
            events, _ = fetch('https://gamma-api.polymarket.com/events?' + urlencode({'slug': slug}), {}, 8, 2_000_000)
            if not isinstance(events, list):
                raise ValueError('invalid_events')
            matches = [result_from_event(e, fixture, now.isoformat()) for e in events]
            matches = [r for r in matches if r]
            if len(matches) == 1:
                rows[key] = matches[0]
                added += 1
            else:
                errors.append('unresolved_or_unmatched_result')
        except (ValueError, KeyError, TypeError, OSError, RuntimeError):
            errors.append('result_feed_unavailable')
    old_path.write_text(json.dumps({'matches': list(rows.values())}, ensure_ascii=False), encoding='utf-8')
    missing = []
    for fixture in body['matches']:
        score = fixture.get('score')
        score = score.get('ft') if isinstance(score, dict) else score
        has_score = isinstance(score, list) and len(score) == 2 and all(type(g) is int and g >= 0 for g in score)
        key = (fixture['date'], fixture['team1'], fixture['team2'])
        if date.fromisoformat(fixture['date']) < now.date() and not has_score and key not in rows:
            missing.append({'date': fixture['date'], 'home': fixture['team1'], 'away': fixture['team2']})
    report = {'checked_at': now.isoformat(), 'recent_requests': requested, 'added_results': added,
              'stored_recent_results': len(rows), 'missing_past_fixtures': missing,
              'errors': dict((key, errors.count(key)) for key in set(errors))}
    (folder/'coverage.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    return report
