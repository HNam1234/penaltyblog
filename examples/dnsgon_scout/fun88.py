"""Bounded, anonymous reads of the publicly displayed Fun88 sports widget.

This is an unofficial adapter to the observed guest protocol, not an account API.
Only signature, fixture/odds listing and completed-results operations are allowed.
Endpoint/schema changes fail closed. No login, wallet, or wagering methods.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from math import isfinite
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from uuid import uuid4

from adapters import ProviderError, _NoRedirect
from dc_runtime import utc

ORIGIN = 'https://2s5sic5xgq.com'
SOURCE = ORIGIN + '/vit/home/index/today?thememode=2&siteid=wf01'
VN = timezone(timedelta(hours=7))
VIRTUAL = re.compile(r'cyberbattle|\bvirtual\b|\besports?\b|bóng ảo|^(?:VS|ES)[\s:.-]', re.I)
OPS = {'cdb60a1', '601ef09', '684d193'}


class GuestSports:
    def __init__(self, transport=None, clock=None):
        self.transport = transport or self._http
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sign = None
        self.calls = 0

    def _http(self, body):
        request = Request(ORIGIN + '/msport/api', data=json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                                   'Origin': ORIGIN, 'Referer': SOURCE, 'User-Agent': 'Mozilla/5.0'}, method='POST')
        try:
            with build_opener(_NoRedirect()).open(request, timeout=10) as response:
                content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                if content_type != 'application/json':
                    raise ProviderError('fun88_invalid_content_type')
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ProviderError('fun88_reply_too_large')
            return json.loads(raw.decode('utf-8-sig'))
        except ProviderError:
            raise
        except (HTTPError, URLError, socket.timeout, TimeoutError, UnicodeError, ValueError, RecursionError):
            raise ProviderError('fun88_public_feed_unavailable') from None

    def _post(self, operation, data=None):
        if operation not in OPS:
            raise ValueError('read_only_operation_required')
        body = {'url': operation}
        if data is not None:
            if self.sign is None:
                signature = self._post('cdb60a1')
                self.sign = signature.get('response')
                if not isinstance(self.sign, str) or not 1 <= len(self.sign) <= 4096:
                    raise ProviderError('fun88_guest_signature_unavailable')
            body.update(sign=self.sign, data=data)
        self.calls += 1
        result = self.transport(body)
        if not isinstance(result, dict):
            raise ProviderError('fun88_invalid_reply')
        if data is not None and result.get('StatusCode') != 100:
            raise ProviderError('fun88_guest_request_rejected')
        if data is not None:
            try:
                delta = (utc(self.clock()) - utc(result['ServerTime'])).total_seconds()
                if not -30 <= delta <= 120:
                    raise ValueError()
            except (KeyError, TypeError, ValueError, OverflowError):
                raise ProviderError('fun88_server_clock_or_schema_invalid') from None
        return result

    def _base(self):
        return {'SBCompany': 1, 'SportId': 1, 'LanguageCode': 'eng', 'Acpid': 'wf01',
                'Sourceid': 'M_VUE', 'requestid': 'vue_wf01_anonymous_' + str(uuid4()),
                'SelectedTimeZone': 28, 'MemberCode': 'anonymous'}

    def page(self, page):
        if type(page) is not int or not 1 <= page <= 100:
            raise ValueError('invalid_page')
        return self._post('601ef09', {**self._base(), 'Markets': [2], 'Page': page,
                          'OddsType': 3, 'IsCombo': False, 'EventDate': '', 'IncludeNextDay': False,
                          'BetTypeIds': [1, 2, 3], 'PeriodIds': [1], 'MarketLineLevels': [1],
                          'EventGroupTypeIds': [1], 'CompetitionIds': [], 'IsPopular': False,
                          'PlayerSpreadGroup': 'A', 'EventOrderBy': 10, 'AllLiveMarketData': False,
                          'IsWithoutVS': True})

    def results(self, competition, now):
        if not re.fullmatch(r'[1-9][0-9]{0,11}', str(competition)):
            raise ValueError('invalid_competition')
        day = utc(now).astimezone(VN).date()
        # The widget only exposes a bounded recent subset, not a full season.
        return self._post('684d193', {**self._base(), 'RSportId': 1, 'EventGroupTypeIds': [1],
                          'EventTypeId': 1, 'StartDate': (day - timedelta(days=90)).isoformat(),
                          'EndDate': day.isoformat(), 'CompetitionId': None,
                          'CompetitionIds': [int(competition)], 'IncludeNextDay': False, 'IsCombo': False,
                          'Page': 1, 'IsIncludeLive': False, 'ResultsSorting': False, 'IsWithoutVS': True})


def _id(value):
    if not re.fullmatch(r'[1-9][0-9]{0,14}', str(value)):
        raise ValueError('invalid_identifier')
    return str(value)


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 180 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid_name')
    return value.strip()


def quarter(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('invalid_quarter_line')
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) > 40 or result * 4 != (result * 4).to_integral_value():
            raise ValueError('invalid_quarter_line')
        return float(result)
    except InvalidOperation:
        raise ValueError('invalid_quarter_line') from None


def normalize_page(payload, fetched_at):
    """Reject in-play, simulated, non-today, suspended and ambiguous contracts."""
    now, rejected, events = utc(fetched_at), Counter(), []
    if payload.get('StatusCode') != 100:
        raise ValueError('invalid_listing')
    for sport in payload.get('Sports', []):
        if sport.get('SportId') != 1:
            continue
        for row in sport.get('Events', []):
            try:
                name = _name(row['Competition']['CompetitionName'])
                home, away = _name(row['HomeTeam']), _name(row['AwayTeam'])
                if row.get('RSportId') != 1 or row.get('EventGroupTypeId') != 1 or any(VIRTUAL.search(x) for x in (name, home, away)):
                    raise ValueError('virtual_or_other_event_group')
                start = utc(row['EventDate'])
                if start <= now or start.astimezone(VN).date() != now.astimezone(VN).date():
                    raise ValueError('not_upcoming_today')
                if (row.get('Market') != 2 or row.get('EventStatusId') != 1 or row.get('RBTimeStatus') != 0
                        or row.get('RBTime') not in (None, '') or row.get('HomeScore') not in (None, '')
                        or row.get('AwayScore') not in (None, '') or row.get('IsBetTradeOpen') is not True):
                    raise ValueError('inplay_or_event_closed')
                markets, ambiguous = {}, set()
                for line in row.get('MarketLines', []):
                    kind = {'1': 'asian_handicap', '2': 'total_goals', '3': '1x2'}.get(str(line.get('BetTypeId')))
                    if (kind is None or line.get('PeriodId') != 1 or line.get('MarketLineLevel') != 1
                            or line.get('MarketlineStatusId') != 1 or line.get('IsLocked') is not False):
                        continue
                    expected = {'1x2': {'5': 'home', '6': 'away', '7': 'draw'},
                                'asian_handicap': {'1': 'home', '2': 'away'},
                                'total_goals': {'3': 'over', '4': 'under'}}[kind]
                    quotes, invalid = {}, False
                    for selection in line.get('WagerSelections', []):
                        side = expected.get(str(selection.get('SelectionId')))
                        price = selection.get('Odds')
                        if (side is None or side in quotes or selection.get('OddsType') != 3
                                or isinstance(price, bool) or not isinstance(price, (int, float))
                                or not isfinite(price) or not 1.001 <= price <= 1000
                                or selection.get('WagerStatusId') not in (None, 1)):
                            invalid = True
                            break
                        raw_line = None if kind == '1x2' else quarter(selection.get('Handicap'))
                        if kind == 'total_goals' and raw_line < 0:
                            invalid = True
                            break
                        # The shipped display formatter negates the HOME handicap.
                        side_line = -raw_line if kind == 'asian_handicap' and side == 'home' else raw_line
                        quotes[side] = {'decimal': float(price), 'line': side_line}
                    if invalid or set(quotes) != set(expected.values()):
                        rejected['invalid_or_incomplete_market'] += 1
                        continue
                    if kind != '1x2':
                        sides = list(quotes.values())
                        if (sides[0]['line'] + sides[1]['line'] != 0 if kind == 'asian_handicap'
                                else sides[0]['line'] != sides[1]['line']):
                            rejected['inconsistent_lines'] += 1
                            continue
                    if kind in markets:
                        ambiguous.add(kind)
                    markets[kind] = {'id': _id(line['MarketlineId']), 'period': 'regulation', 'selections': quotes}
                for kind in ambiguous:
                    markets.pop(kind, None)
                    rejected['ambiguous_market'] += 1
                if not markets:
                    raise ValueError('no_supported_main_market')
                event = {'id': 'fun88:' + _id(row['EventId']), 'competition_id': _id(row['Competition']['CompetitionId']),
                         'competition': name.lstrip('*'), 'home': home, 'away': away,
                         'home_id': _id(row['HomeTeamId']), 'away_id': _id(row['AwayTeamId']),
                         'start_time': start.isoformat(), 'fetched_at': now.isoformat(),
                         'server_time': payload['ServerTime'], 'source': SOURCE, 'markets': markets}
                if '1x2' in markets:
                    q = {k: 1 / v['decimal'] for k, v in markets['1x2']['selections'].items()}
                    margin = sum(q.values())
                    if not 0.98 <= margin <= 1.30:
                        raise ValueError('implausible_1x2_book')
                    event.update(market_probabilities={k: v / margin for k, v in q.items()}, overround=margin - 1)
                events.append(event)
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                reason = str(error) if type(error) is ValueError and re.fullmatch(r'[a-z_]+', str(error)) else 'invalid_event_schema'
                rejected[reason] += 1
    return events, rejected


def recent_results(payload, competition, now):
    """Keep exact provider team IDs; never fuzzy-match similarly named clubs."""
    before, found, conflicts = utc(now), {}, set()
    for league in payload.get('Competitions', []):
        if str(league.get('CompetitionId')) != str(competition) or league.get('SportId') != 1:
            continue
        for row in league.get('Events', []):
            try:
                if row.get('EventGroupTypeId') != 1 or row.get('RSportId') != 1 or row.get('EventTypeId') != 1:
                    continue
                when = utc(row['EventDate'])
                if not before - timedelta(days=90) <= when < before:
                    continue
                scores = [r for r in row.get('ResultList', []) if r.get('PeriodId') == 1 and r.get('IsCancelled') is False]
                if len(scores) != 1 or not all(re.fullmatch(r'[0-9]{1,2}', str(scores[0].get(k))) for k in ('HomeScore', 'AwayScore')):
                    continue
                result = {'id': _id(row['EventId']), 'at': when.isoformat(), 'home_id': _id(row['HomeTeamId']),
                          'away_id': _id(row['AwayTeamId']), 'home_goals': int(scores[0]['HomeScore']), 'away_goals': int(scores[0]['AwayScore'])}
                if result['id'] in found and found[result['id']] != result:
                    conflicts.add(result['id'])
                found[result['id']] = result
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
    return sorted((r for k, r in found.items() if k not in conflicts), key=lambda r: r['at'], reverse=True)


def form(results, team_id, limit=5):
    rows = [r for r in results if str(team_id) in (r['home_id'], r['away_id'])][:limit]
    wins = draws = losses = gf = ga = 0
    outcomes = []
    for row in rows:
        scored, conceded = ((row['home_goals'], row['away_goals']) if str(team_id) == row['home_id']
                            else (row['away_goals'], row['home_goals']))
        gf += scored; ga += conceded
        wins += scored > conceded; draws += scored == conceded; losses += scored < conceded
        outcomes.append('W' if scored > conceded else 'D' if scored == conceded else 'L')
    return {'count': len(rows), 'wins': wins, 'draws': draws, 'losses': losses, 'goals_for': gf, 'goals_against': ga,
            'recent_first': outcomes, 'last_match': rows[0]['at'] if rows else None,
            'coverage': 'recent_available_subset_not_complete_season'}


def settlement(market, side, line, home_goals, away_goals):
    """Unit stake net outcome, excluding price: quarter lines split evenly."""
    if any(type(x) is not int or not 0 <= x <= 50 for x in (home_goals, away_goals)):
        raise ValueError('invalid_score')
    line = quarter(line)
    ticks = int(round(line * 4))
    lines = (line - .25, line + .25) if ticks % 2 else (line, line)
    values = []
    for half_line in lines:
        if market == 'asian_handicap' and side in ('home', 'away'):
            delta = (home_goals - away_goals if side == 'home' else away_goals - home_goals) + half_line
        elif market == 'total_goals' and side in ('over', 'under'):
            delta = (home_goals + away_goals - half_line) * (1 if side == 'over' else -1)
        else:
            raise ValueError('unsupported_settlement_contract')
        values.append(1 if delta > 0 else -1 if delta < 0 else 0)
    return {-2: 'loss', -1: 'half_loss', 0: 'push', 1: 'half_win', 2: 'win'}[sum(values)]
