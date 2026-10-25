"""Normalize documented API-Football rows without guessing settlement rules."""
from datetime import datetime, timezone
import json


def timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timezone_required')
    return parsed.astimezone(timezone.utc)


def text(value, limit=120):
    return ' '.join(str(value or '').split())[:limit]


def fixtures_from(rows):
    """Return only identified fixtures. No invented fallback kickoff or team."""
    events = {}
    for row in rows:
        try:
            fixture, teams, league = row['fixture'], row['teams'], row['league']
            event_id = int(fixture['id'])
            home, away = text(teams['home']['name']), text(teams['away']['name'])
            start = timestamp(fixture['date']).isoformat()
            if event_id <= 0 or not home or not away or home == away:
                continue
            events[event_id] = {
                'event_id': 'api_football:' + str(event_id),
                'sport': 'soccer', 'home': home, 'away': away, 'start_time': start,
                'league': text(league['name']), 'league_id': int(league['id']),
                'status': fixture['status']['short'], 'markets': [],
                'source_url': 'https://www.api-football.com/documentation-v3#tag/Fixtures',
            }
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return events


def normalize(fixtures, odds, book_rules):
    """Only full Match Winner rows for explicitly reviewed bookmaker rules.

    book_rules maps API bookmaker numeric IDs to {period,evidence_url,reviewed_at}.
    A provider market label alone does not prove a bookmaker's settlement rule.
    """
    events = fixtures_from(fixtures)
    rejected = {'unreviewed_book': 0, 'invalid_row': 0, 'unknown_fixture': 0}
    for row in odds:
        try:
            fixture_id = int(row['fixture']['id'])
            event = events.get(fixture_id)
            if event is None:
                rejected['unknown_fixture'] += 1
                continue
            if event['status'] != 'NS':
                continue
            updated = timestamp(row['update']).isoformat()
            for book in row['bookmakers']:
                book_id = str(int(book['id']))
                rule = book_rules.get(book_id, {})
                if (rule.get('period') != 'regulation'
                        or not rule.get('evidence_url', '').startswith('https://')
                        or not rule.get('reviewed_at')):
                    rejected['unreviewed_book'] += 1
                    continue
                timestamp(rule['reviewed_at'])
                for market in book['bets']:
                    if market.get('id') != 1 or market.get('name') != 'Match Winner':
                        continue
                    outcomes = {}
                    mapping = {'Home': 'home', 'Draw': 'draw', 'Away': 'away'}
                    for item in market['values']:
                        name = mapping.get(item['value'])
                        if name is None or name in outcomes:
                            raise ValueError('invalid_outcomes')
                        outcomes[name] = item['odd']
                    if set(outcomes) != {'home', 'draw', 'away'}:
                        raise ValueError('incomplete_market')
                    event['markets'].append({
                        'provider': 'api_sports_football',
                        'bookmaker': 'api_football_book:' + book_id,
                        'bookmaker_name': text(book['name']),
                        'market': '1x2', 'period': 'regulation', 'as_of': updated,
                        'outcomes': outcomes, 'status': 'open', 'in_play': False,
                    })
        except (KeyError, TypeError, ValueError, OverflowError):
            rejected['invalid_row'] += 1
    # Conflicting copies at the same source timestamp are ambiguous, not a vote.
    for event in events.values():
        copies, conflicts = {}, set()
        for row in event['markets']:
            key = (row['bookmaker'], row['market'], row['period'], row['as_of'])
            signature = json.dumps(row['outcomes'], sort_keys=True)
            if key in copies and copies[key] != signature:
                conflicts.add(key)
            copies[key] = signature
        if conflicts:
            event['markets'] = [r for r in event['markets']
                                if (r['bookmaker'], r['market'], r['period'], r['as_of']) not in conflicts]
            rejected['ambiguous_book_market'] = rejected.get('ambiguous_book_market', 0) + len(conflicts)
    return list(events.values()), rejected


def final_results(fixtures, known_at):
    """Only FT at 90 minutes is settled here; AET/PEN/void need other rules.

    settled_at uses actual acquisition as a conservative upper bound: API's
    fixture.date is kickoff, NOT full-time. No fabricated end-of-match time.
    """
    timestamp(known_at)
    events = fixtures_from(fixtures)
    results = []
    for row in fixtures:
        try:
            fixture = row['fixture']
            event = events[int(fixture['id'])]
            if fixture['status']['short'] != 'FT':
                continue
            score = row['score']['fulltime']
            home, away = score['home'], score['away']
            if (type(home) is not int or type(away) is not int or home < 0 or away < 0
                    or timestamp(event['start_time']) >= timestamp(known_at)):
                continue
            outcome = 'home' if home > away else 'away' if away > home else 'draw'
            results.append({
                'event_id': event['event_id'], 'sport': 'soccer', 'market': '1x2',
                'period': 'regulation', 'line': None, 'outcome': outcome,
                'status': 'final', 'settlement_verified': True,
                'settled_at': known_at, 'known_at': known_at,
                'settled_at_basis': 'first_observed_final_upper_bound',
            })
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return results
