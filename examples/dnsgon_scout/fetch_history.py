"""Download the public CC0 EPL archive; no keys or private endpoints."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from model_data import public_json, refresh


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--first', type=int, default=2016)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    last = now.year if now.month >= 7 else now.year-1
    if not 2010 <= args.first <= last: raise ValueError('invalid_first_season')
    folder = Path(args.data); folder.mkdir(parents=True, exist_ok=True)
    for year in range(args.first, last+1):
        season = f'{year}-{str(year+1)[-2:]}'
        data,_ = public_json('https://raw.githubusercontent.com/openfootball/football.json/master/'
                             +season+'/en.1.json', {}, 15, 2_000_000)
        (folder/(season+'.json')).write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(refresh(folder)))


if __name__ == '__main__': main()
