"""Small descriptive LoL team cache from ChainCC's CC BY 4.0 open dataset.

One row is a MAP, not a series. No win probability is inferred from recent form.
CSV dates have no timezone label: retain source time and exclude today's rows.
"""
from collections import Counter, defaultdict
import argparse
import csv
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
from urllib.request import Request, build_opener

from adapters import ProviderError, _NoRedirect

DATA_URL = 'https://chaincc.lol/data/chaincc-teams-2026.csv.gz'
ATTRIBUTION = {'name': 'ChainCC Open LoL Esports Dataset', 'url': 'https://chaincc.lol/free/data',
               'license': 'CC BY 4.0', 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
               'changes': 'Selected paired completed maps; aggregated recent team form.'}


def summarize(raw, before_date):
    """Pair reciprocal sides and contradictory duplicates before counting maps."""
    games, invalid, rows = defaultdict(list), Counter(), 0
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        with io.TextIOWrapper(compressed, encoding='utf-8-sig') as text:
            reader = csv.DictReader(text)
            if not {'game_id','date','team_id','team_name','opponent_team_name','result','side','patch','league'} <= set(reader.fieldnames or []):
                raise ValueError('unsupported_lol_columns')
            for row in reader:
                rows += 1
                if rows > 50000 or sum(len(str(v)) for v in row.values()) > 12000:
                    raise ValueError('lol_dataset_limit')
                try:
                    when = datetime.fromisoformat(row['date'])
                    if when.date() >= before_date:
                        invalid['same_day_or_future_excluded'] += 1
                        continue
                    if row['result'] not in ('TRUE','FALSE') or row['side'] not in ('Blue','Red'):
                        raise ValueError()
                    for key in ('game_id','team_id','team_name','opponent_team_name','league','patch'):
                        if not row[key] or len(row[key]) > 180 or any(ord(c)<32 for c in row[key]):
                            raise ValueError()
                    games[row['game_id']].append({k:row[k] for k in ('game_id','date','team_id','team_name','opponent_team_name','result','side','patch','league')})
                except (TypeError, ValueError, KeyError):
                    invalid['invalid_row'] += 1
    teams = defaultdict(list)
    maps = 0
    latest = None
    for game_id, sides in games.items():
        # Duplicate identical lines do not double-count. Contradictions drop the map.
        unique = {json.dumps(r, sort_keys=True):r for r in sides}
        sides = list(unique.values())
        if (len(sides)!=2 or {r['side'] for r in sides}!={'Blue','Red'}
                or {r['result'] for r in sides}!={'TRUE','FALSE'}
                or sides[0]['team_name'] != sides[1]['opponent_team_name']
                or sides[1]['team_name'] != sides[0]['opponent_team_name']
                or sides[0]['team_id'] == sides[1]['team_id']
                or any(sides[0][k] != sides[1][k] for k in ('date','league','patch'))):
            invalid['unpaired_or_conflicting_map'] += 1
            continue
        maps += 1
        latest = max(latest or sides[0]['date'], sides[0]['date'])
        for row in sides:
            teams[row['team_id']].append(row)
    summaries = {}
    for identity, history in teams.items():
        history.sort(key=lambda r:r['date'],reverse=True)
        recent=history[:10]
        summaries[identity]={'team':history[0]['team_name'],'maps_available':len(history),
                             'recent_maps':len(recent),'recent_wins':sum(r['result']=='TRUE' for r in recent),
                             'recent_form':['W' if r['result']=='TRUE' else 'L' for r in recent],
                             'last_source_time':history[0]['date'], 'last_patch':history[0]['patch'],
                             'last_league':history[0]['league'],
                             'recent_opponents':[r['opponent_team_name'] for r in recent]}
    return {'schema':1, 'unit':'map_not_series', 'time_policy':'source_timezone_unspecified_exclude_current_day',
            'cutoff_date_exclusive':before_date.isoformat(), 'source':ATTRIBUTION, 'dataset_sha256':sha256(raw).hexdigest(),
            'rows_read':rows,'paired_maps':maps,'latest_source_time':latest,'filtered':dict(invalid),
            'teams':summaries, 'model_probability':None, 'qualified_betting_edges':0}


def update(output):
    with build_opener(_NoRedirect()).open(Request(DATA_URL,headers={'User-Agent':'DNSGon-SportsResearch/0.1'}),timeout=20) as response:
        raw=response.read(3_000_001)
    if len(raw)>3_000_000:
        raise ProviderError('lol_dataset_large')
    # Bound inflation before CSV parsing; a corrupt download never replaces good data.
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        if len(compressed.read(40_000_001))>40_000_000:
            raise ProviderError('lol_dataset_inflation_limit')
    now=datetime.now(timezone.utc)
    summary=summarize(raw,now.date())
    if summary['paired_maps']<100 or not summary['latest_source_time']:
        raise ProviderError('lol_dataset_incomplete')
    latest=datetime.fromisoformat(summary['latest_source_time']).date()
    if (now.date()-latest).days>7:
        raise ProviderError('lol_dataset_stale')
    summary['fetched_at']=now.isoformat()
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(summary,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    temporary.replace(path)
    return {k:v for k,v in summary.items() if k!='teams'}|{'team_count':len(summary['teams'])}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    print(json.dumps(update(parser.parse_args().output),ensure_ascii=True))
