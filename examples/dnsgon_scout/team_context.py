"""First-party FPL team availability, shown as context, never a model modifier.

soccerdata's ESPN reader informed source selection. No soccerdata source is
copied or installed: its full browser/pandas dependency tree is unnecessary here.
"""
from datetime import datetime, timedelta, timezone

from adapters import _read_json
from dc_runtime import utc
from today_fixtures import NAMES

FPL = 'https://fantasy.premierleague.com/api/bootstrap-static/'


def normalize(payload, fetched_at):
    at=utc(fetched_at)
    teams={}
    for row in payload['teams']:
        if row['name'] not in NAMES or type(row['id']) is not int or row['id'] in teams:
            raise ValueError('unmapped_fpl_context_team')
        teams[row['id']]=NAMES[row['name']]
    if len(teams)!=20:
        raise ValueError('invalid_fpl_context_team_count')
    result={name:{'availability_reports':[],'source':FPL,'fetched_at':at.isoformat(),
                  'lineup_confirmed':False,'probability_adjustment_applied':False} for name in teams.values()}
    seen=set()
    for player in payload['elements']:
        if player['id'] in seen or player['team'] not in teams:
            raise ValueError('invalid_fpl_player_identity')
        seen.add(player['id'])
        if player.get('status') not in ('i','d','s','u'):
            continue
        name=player['web_name']
        if not isinstance(name,str) or not 1<=len(name)<=100 or any(ord(c)<32 for c in name):
            raise ValueError('invalid_fpl_player_name')
        news_added=player.get('news_added')
        if news_added and utc(news_added)>at+timedelta(minutes=1):
            continue
        result[teams[player['team']]]['availability_reports'].append({
            'player':name,'status':player['status'],'source_updated_at':news_added,
            'minutes_this_season':player.get('minutes',0),
            'note':'FPL availability flag; not independently confirmed medical or starting lineup information'})
    for context in result.values():
        context['availability_reports'].sort(key=lambda r:(-r['minutes_this_season'],r['player']))
    return result


def load(store, now, fetch=_read_json, clock=None):
    now=utc(now);cached=store.latest_snapshot('context:fpl:availability')
    if cached and timedelta(0)<=now-utc(cached['fetched_at'])<timedelta(minutes=30):
        return cached['data']
    body,_=fetch(FPL,{},8,4_000_000)
    context=normalize(body,(clock or (lambda:datetime.now(timezone.utc)))())
    store.snapshot('context:fpl:availability',context,now.isoformat())
    return context
