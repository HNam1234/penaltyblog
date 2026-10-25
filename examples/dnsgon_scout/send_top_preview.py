"""Explicit owner-requested one-off preview; never enters scored alert forecasts."""
from datetime import datetime,timedelta,timezone
import json
from math import isfinite
from pathlib import Path

from dc_runtime import artifact_id,qualify,utc
from fun88 import VN
from high_probability import MIN_P,MIN_EV
from model_alerts import literal
from publisher import DeliveryError,send
from store import Store


def preview(config,source,target,now):
    analysis=source.latest_snapshot('analysis:current')
    fixtures=source.latest_snapshot('fpl:today:'+now.astimezone(VN).date().isoformat())
    if not analysis or not timedelta(0)<=now-utc(analysis['fetched_at'])<=timedelta(minutes=5):
        raise ValueError('preview_analysis_stale')
    if not fixtures or not timedelta(0)<=now-utc(fixtures['fetched_at'])<=timedelta(minutes=15):
        raise ValueError('preview_fixtures_stale')
    model=json.loads(Path(config['model_file']).read_text(encoding='utf-8'))
    if not timedelta(0)<=now-utc(model['trained_at'])<=timedelta(hours=36):
        raise ValueError('preview_model_stale')
    rows=[]
    for report in analysis['data']['reports']:
        if (utc(report['start_time'])<=now
                or utc(report['start_time']).astimezone(VN).date()!=now.astimezone(VN).date()):
            continue
        if (report['model_version']!=artifact_id(model)
                or any(not timedelta(0)<=now-utc(report[k])<=timedelta(minutes=5) for k in ('created_at','quote_acquired_at'))):
            continue
        official=[f for f in fixtures['data'] if f['id']==report['official_fixture_id']
                  and f['home']==report['home'] and f['away']==report['away']
                  and not f['started'] and not f['finished']
                  and abs((utc(f['start_time'])-utc(report['start_time'])).total_seconds())<=60]
        if len(official)!=1:continue
        p=qualify(model,report['home'],report['away'],now)['probabilities']
        if any(abs(p[k]-report['model_1x2'][k])>1e-8 for k in p):continue
        quotes={m['side']:m['decimal'] for m in report['markets']
                if m['market']=='1x2' and m['line'] is None and m['probability_basis']=='calibrated_epl_1x2'}
        if (set(quotes)!=set(p) or any(isinstance(o,bool) or not isinstance(o,(int,float))
                or not isfinite(o) or not 1.001<=o<=1000 for o in quotes.values())
                or not .98<=sum(1/o for o in quotes.values())<=1.30):
            continue
        selection=max(p,key=p.get)
        rows.append(report|{'probabilities':p,'selection':selection,'decimal':quotes[selection],
                            'ev':p[selection]*quotes[selection]-1})
    if not rows:raise ValueError('no_fresh_upcoming_modeled_match')
    row=max(rows,key=lambda r:r['probabilities'][r['selection']])
    p=row['probabilities'];chosen={'home':row['home'],'away':row['away'],'draw':'Hòa'}[row['selection']]
    probability=p[row['selection']];g=row['model_goal_expectation']
    threshold=probability>=MIN_P and row['ev']>=MIN_EV
    label='DƯỚI NGƯỠNG' if not threshold else 'CHƯA THAY THẾ TÍN HIỆU AGENT'
    source=('**DNS GỌN · BẢN XEM THỬ · '+label+'**\n\n'
            f"**{literal(row['home'])} — {literal(row['away'])}**\n"
            f"EPL · {utc(row['start_time']).astimezone(VN):%H:%M %d/%m/%Y} VN\n"
            f"Cửa có xác suất cao nhất trong **{len(rows)} trận EPL** bot đã chấm hôm nay; phạm vi không bao gồm mọi môn/giải.\n\n"
            f"**Cửa đang xem:** {literal(chosen)} · 1X2 · 90 phút + bù giờ.\n"
            f"**Xác suất model:** {probability:.1%}.\n"
            f"**Odds Fun88 lúc lấy:** {row['decimal']:.3f}.\n"
            f"**EV theo model trước chi phí:** {row['ev']:+.1%} trên đơn vị vốn; chưa chứng minh có lời.\n\n"
            '**Vì sao đứng đầu lượt này**\n'
            f"• Xác suất ba cửa: chủ {p['home']:.1%} · hòa {p['draw']:.1%} · khách {p['away']:.1%}; cửa trên có số cao nhất trong tập được chấm.\n"
            f"• Dixon–Coles dự kiến bàn chủ/khách {g['home']:.2f}/{g['away']:.2f}, tính từ kết quả quá khứ; đây không phải xG quan sát.\n"
            '• Số trận trong tập train: '+', '.join(literal(t)+' '+str(n) for t,n in row['team_sample_counts'].items())+'.\n\n'
            '**Kết luận**\n'
            +(f'Chưa đạt ngưỡng xác suất {MIN_P:.0%}; đây là bản xem thử theo yêu cầu, không phải tín hiệu đủ chuẩn.\n' if probability<MIN_P
              else 'Đây là bản xem thử theo yêu cầu; agent vẫn phải rà đủ các điều kiện trước khi phát tín hiệu tự động.\n')+
            f"Khả năng cửa này không thắng theo model: **{1-probability:.1%}**. "
            'Chưa đưa đội hình/chấn thương vào xác suất; odds có thể đổi.\n'
            f"Giá lấy lúc {utc(row['quote_acquired_at']).astimezone(VN):%H:%M:%S} VN. "
            'Không đổi ngưỡng tự động, không ghi bản xem thử vào bảng dự báo đủ chuẩn.')
    key='manual:top-preview:'+now.astimezone(VN).date().isoformat()+':'+row['event_id']+':v1'
    expires=min(utc(row['start_time']),utc(row['quote_acquired_at'])+timedelta(minutes=5))
    return row,key,expires,source,len(rows)


def main():
    import fcntl
    config=json.loads(Path('/etc/dnsgon-sports.json').read_text())
    with Path(config['high_probability_database']).with_suffix('.agent.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        source=Store(config['database']);target=Store(config['high_probability_database'])
        try:
            now=datetime.now(timezone.utc)
            row,key,expires,text,count=preview(config,source,target,now)
            before=len(target.predictions())
            fresh=target.enqueue(key,text,expires.isoformat())
            previous=target.db.execute('select state,message_id from scout_outbox where key=?',(key,)).fetchone()
            if not fresh:
                print(json.dumps({'already_recorded':True,'state':previous['state'],'message_id':previous['message_id']}));return
            if not target.claim(key):raise ValueError('preview_already_claimed')
            if datetime.now(timezone.utc)>=expires:
                target.mark_failed(key,'preview_expired');raise ValueError('preview_expired')
            try:message_id=send(config['telegram_token'],config['high_probability_channel_id'],text)
            except DeliveryError as error:
                if error.uncertain:target.mark_uncertain(key)
                else:target.mark_failed(key,error.code)
                raise RuntimeError('preview_delivery_failed_no_blind_retry') from None
            target.mark_sent(key,message_id)
            assert len(target.predictions())==before
            result={'sent':True,'channel_id':config['high_probability_channel_id'],'message_id':message_id,
                    'key':key,'event_id':row['event_id'],'home':row['home'],'away':row['away'],
                    'selection':row['selection'],'probability':row['probabilities'][row['selection']],
                    'decimal_odds':row['decimal'],'expected_net_before_costs':row['ev'],
                    'compared_modeled_matches':count,'quote_at':row['quote_acquired_at'],
                    'probability_threshold':MIN_P,'below_threshold':row['probabilities'][row['selection']]<MIN_P,
                    'qualified_forecasts_unchanged':True,'preview_text':text}
            target.record_run('owner_requested_top_preview',{k:v for k,v in result.items() if k!='preview_text'},now)
            print(json.dumps(result,ensure_ascii=True))
        finally:source.close();target.close()


if __name__=='__main__':main()
