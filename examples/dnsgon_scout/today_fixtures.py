"""Today's EPL schedule from first-party public FPL JSON, Vietnam calendar day."""
from datetime import datetime, timedelta, timezone
import re

from adapters import _read_json, ProviderError
from football import timestamp
from model_data import CLUB_CODES

VN = timezone(timedelta(hours=7))
NAMES = {'Arsenal':'Arsenal FC','Aston Villa':'Aston Villa FC','Bournemouth':'AFC Bournemouth',
         'Brentford':'Brentford FC','Brighton':'Brighton & Hove Albion FC','Chelsea':'Chelsea FC',
         'Coventry City':'Coventry City FC','Crystal Palace':'Crystal Palace FC','Everton':'Everton FC',
         'Fulham':'Fulham FC','Hull City':'Hull City AFC','Ipswich Town':'Ipswich Town FC',
         'Leeds':'Leeds United FC','Liverpool':'Liverpool FC','Man City':'Manchester City FC',
         'Man Utd':'Manchester United FC','Newcastle':'Newcastle United FC',
         "Nott'm Forest":'Nottingham Forest FC','Spurs':'Tottenham Hotspur FC','Sunderland':'Sunderland AFC',
         'Burnley':'Burnley FC','Leicester':'Leicester City FC','Southampton':'Southampton FC',
         'West Ham':'West Ham United FC','Wolves':'Wolverhampton Wanderers FC'}
SOURCE = 'https://fantasy.premierleague.com/fixtures'


def normalize(fixtures, teams, day):
    if not isinstance(fixtures, list) or not isinstance(teams, list) or len(teams) != 20:
        raise ValueError('invalid_fpl_schema')
    mapping = {}
    for team in teams:
        if (type(team.get('id')) is not int or team['id'] in mapping or team.get('name') not in NAMES):
            raise ValueError('unmapped_fpl_team')
        mapping[team['id']] = NAMES[team['name']]
    rows, seen = [], set()
    for fixture in fixtures:
        if not fixture.get('kickoff_time'):
            continue  # Unscheduled/TBD is never assigned a fictitious time.
        kickoff = timestamp(fixture['kickoff_time'])
        if kickoff.astimezone(VN).date().isoformat() != day:
            continue
        if (type(fixture.get('id')) is not int or fixture['id'] in seen
                or type(fixture.get('started')) is not bool or type(fixture.get('finished')) is not bool):
            raise ValueError('invalid_fpl_fixture')
        seen.add(fixture['id'])
        home, away = mapping[fixture['team_h']], mapping[fixture['team_a']]
        if home == away: raise ValueError('invalid_fpl_pair')
        rows.append({'id': fixture['id'], 'home': home, 'away': away,
                     'start_time': kickoff.isoformat(), 'started': fixture['started'],
                     'finished': fixture['finished'], 'source': SOURCE,
                     'slug': f"epl-{CLUB_CODES[home]}-{CLUB_CODES[away]}-{kickoff.date().isoformat()}"})
    return sorted(rows, key=lambda r: (r['start_time'], r['home']))


def load(store, now, fetch=_read_json, clock=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = timestamp(now); day = now.astimezone(VN).date().isoformat()
    provider = 'fpl:today:' + day
    cached = store.latest_snapshot(provider)
    def fresh(row, seconds):
        return row and 0 <= (now-timestamp(row['fetched_at'])).total_seconds() < seconds
    calls = 0
    if not fresh(cached, 15*60):
        team_cache = store.latest_snapshot('fpl:teams')
        if not fresh(team_cache, 24*3600):
            if not store.reserve_request('fpl_public', now.date().isoformat(), limit=140):
                raise ProviderError('fpl_local_budget')
            body,_ = fetch('https://fantasy.premierleague.com/api/bootstrap-static/', {}, 8, 4_000_000)
            teams = body['teams']; del body
            at = timestamp(clock()).isoformat()
            store.snapshot('fpl:teams', teams, at); calls += 1
        else:
            teams = team_cache['data']
        if not store.reserve_request('fpl_public', now.date().isoformat(), limit=140):
            raise ProviderError('fpl_local_budget')
        body,_ = fetch('https://fantasy.premierleague.com/api/fixtures/', {}, 8, 2_000_000)
        if not isinstance(body, list) or len(body) != 380:
            raise ValueError('incomplete_fpl_calendar')
        rows = normalize(body, teams, day); del body
        at = timestamp(clock()).isoformat()
        store.snapshot(provider, rows, at); calls += 1
    else:
        rows, at = cached['data'], cached['fetched_at']
    # Refilter on every scan so cached fixtures cannot turn into in-play alerts.
    upcoming = [r for r in rows if not r['started'] and not r['finished'] and timestamp(r['start_time']) > timestamp(clock())]
    return {'day': day, 'status': 'upcoming_today' if upcoming else 'no_upcoming_today',
            'fixtures': upcoming, 'scheduled_count': len(rows), 'source': SOURCE,
            'checked_at': at, 'requests': calls, 'timezone': 'Asia/Ho_Chi_Minh'}


def publish(store, report, now):
    """One calendar message per day, separate from probability-qualified alerts."""
    day = timestamp(now).astimezone(VN).date()
    lines = [f"**EPL HÔM NAY · {day.strftime('%d/%m/%Y')}**", '', 'Giờ Việt Nam · Các trận chưa bắt đầu', '']
    for row in report['fixtures']:
        home = re.sub(r'([\\`*_\[\]{}()])', r'\\\1', row['home'])
        away = re.sub(r'([\\`*_\[\]{}()])', r'\\\1', row['away'])
        hour = timestamp(row['start_time']).astimezone(VN).strftime('%H:%M')
        lines.append(f'• {hour} — {home} vs {away}')
    if not report['fixtures']:
        lines.append('Nguồn lịch đã đọc thành công; không còn trận EPL chưa bắt đầu trong ngày.')
    lines += ['', 'Đây là lịch thi đấu, chưa phải đề xuất đặt cược. Nếu có trận qua bộ lọc xác suất/giá/dữ liệu, bot gửi thẻ riêng.',
              '[Nguồn lịch chính thức: Fantasy Premier League](' + SOURCE + ')']
    expires = datetime.combine(day+timedelta(days=1), datetime.min.time(), tzinfo=VN)
    return store.enqueue('daily-fixtures:' + day.isoformat() + ':v1', '\n'.join(lines), expires.isoformat())
