"""Refresh a research-only LoL evaluation. Never calls Telegram or enables alerts."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
from urllib.request import Request,build_opener

from adapters import _NoRedirect
from esports_stats import DATA_URL


def update(output):
    sys.path.insert(0,str(Path(__file__).parent/'multisport-lab'))
    from evaluate_lol import evaluate
    with build_opener(_NoRedirect()).open(Request(DATA_URL,headers={'User-Agent':'DNSGon-SportsResearch/0.2'}),timeout=25) as response:
        raw=response.read(3_000_001)
    now=datetime.now(timezone.utc)
    report,_=evaluate(raw,now.date())
    if (now.date()-datetime.fromisoformat(report['protocol']['test_through']).date()).days>7:
        raise ValueError('research_data_stale')
    report['fetched_at']=now.isoformat()
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    for name,body in [('dataset.csv.gz',raw),('evaluation.json',(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode())]:
        temp=output/(name+'.tmp');temp.write_bytes(body);temp.replace(output/name)
    return {'status':'research_only','paired_maps':report['data_quality']['paired_maps'],
            'test':report['test'],'high_probability':report['high_75'],'production_alerts_enabled':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    print(json.dumps(update(parser.parse_args().output)))
