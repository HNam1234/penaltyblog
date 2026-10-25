"""Dedicated high-probability channel, independent outbox, no extra feed requests.

Consumes only fresh, independently modeled EPL 1X2 analyses. Research/watch cards
cannot enter this outbox. At-most-once publisher holds uncertain delivery.
"""
from collections import Counter
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path

from dc_runtime import artifact_id,qualify,utc
from fun88 import VN
from model_alerts import literal

MIN_P=.75
MIN_EV=.03


def eligible(report,artifact,fixtures,now):
    now=utc(now)
    if not timedelta(0)<=now-utc(artifact['trained_at'])<=timedelta(hours=36):
        raise ValueError('model_stale')
    if report.get('schema')!=1 or report['model_version']!=artifact_id(artifact):
        raise ValueError('model_version_mismatch')
    if any(not timedelta(0)<=now-utc(report[k])<=timedelta(minutes=5) for k in ('created_at','quote_acquired_at')):
        raise ValueError('analysis_or_quote_stale')
    if utc(report['start_time'])<=now or utc(report['start_time']).astimezone(VN).date()!=now.astimezone(VN).date():
        raise ValueError('not_upcoming_today')
    matches=[f for f in fixtures if f['id']==report['official_fixture_id'] and f['home']==report['home']
             and f['away']==report['away'] and not f['started'] and not f['finished']
             and abs((utc(f['start_time'])-utc(report['start_time'])).total_seconds())<=60]
    if len(matches)!=1:
        raise ValueError('official_fixture_not_confirmed')
    result=qualify(artifact,report['home'],report['away'],now)
    if not result['qualified']:
        raise ValueError(result['reason'])
    probabilities=result['probabilities']
    if any(abs(probabilities[k]-report['model_1x2'][k])>1e-8 for k in probabilities):
        raise ValueError('independent_probability_mismatch')
    quotes={}
    for market in report['markets']:
        if market['market']!='1x2':continue
        side=market['side'];decimal=market['decimal']
        if (side not in probabilities or side in quotes or market['line'] is not None
                or market['probability_basis']!='calibrated_epl_1x2'
                or isinstance(decimal,bool) or not isinstance(decimal,(int,float))
                or not isfinite(decimal) or not 1.001<=decimal<=1000):
            raise ValueError('invalid_1x2_quotes')
        quotes[side]=float(decimal)
    if set(quotes)!=set(probabilities) or not .98<=sum(1/x for x in quotes.values())<=1.30:
        raise ValueError('incomplete_or_implausible_book')
    selection=max(probabilities,key=probabilities.get)
    probability=probabilities[selection]
    if probability<MIN_P:
        raise ValueError('below_75_percent')
    ev=probability*quotes[selection]-1
    if ev<MIN_EV:
        raise ValueError('model_ev_below_3_percent')
    identity=sha256((report['event_id']+'|highprob-epl-1x2-v1').encode()).hexdigest()[:24]
    return {'id':identity,'event_id':report['event_id'],'sport':'soccer_epl','market':'1x2',
            'period':'regulation','line':None,'home':report['home'],'away':report['away'],
            'selection':selection,'probabilities':probabilities,'probability':probability,
            'decimal_odds':quotes[selection],'expected_net_before_costs':ev,
            'created_at':now.isoformat(),'start_time':report['start_time'],
            'quote_acquired_at':report['quote_acquired_at'],'data_as_of':artifact['trained_at'],
            'model_version':report['model_version'],'method':'independent_dc_epl_highprob_v1',
            'historical_group':result['historical_group'],'label':'experimental_not_guaranteed',
            'team_context':report.get('team_context',{})}


def card(row):
    chosen={'home':row['home'],'away':row['away'],'draw':'Hòa'}[row['selection']]
    return ('**DNS GỌN · XÁC SUẤT CAO · THỬ NGHIỆM**\n\n'
            f"**{literal(row['home'])} — {literal(row['away'])}**\n"
            f"EPL · {utc(row['start_time']).astimezone(VN):%H:%M %d/%m} VN\n\n"
            f"Lựa chọn: **{literal(chosen)}** · 1X2 · 90 phút + bù giờ.\n"
            f"Xác suất mô hình độc lập: **{row['probability']:.1%}**.\n"
            f"Odds Fun88 lúc lấy: **{row['decimal_odds']:.3f}**.\n"
            f"EV theo model trước chi phí: {row['expected_net_before_costs']:+.1%} trên đơn vị vốn.\n"
            f"Giá lấy lúc {utc(row['quote_acquired_at']).astimezone(VN):%H:%M:%S} VN; giá có thể đổi.\n\n"
            'Đã qua kiểm tra lịch chính thức, tuổi dữ liệu, lịch sử đội và nhóm dự báo lịch sử. '
            'Đội hình/chấn thương chưa được đưa vào công thức. Đây chưa phải lợi thế sinh lời đã chứng minh.\n'
            f"Mã khóa trước trận: `{row['id']}`; giữ cả kết quả đúng và sai.\n"
            '[Source và giới hạn](https://github.com/HNam1234/penaltyblog/tree/dnsgon-scout/examples/dnsgon_scout)')


def welcome():
    return ('**DNS GỌN · ĐÃ BẬT KÊNH XÁC SUẤT CAO**\n\n'
            'Kênh riêng dùng cùng bot DNS Gọn. VPS kiểm tra mỗi 5 phút.\n\n'
            'Điều kiện hiện tại:\n'
            '• EPL, 1X2 trước trận trong ngày Việt Nam.\n'
            '• Xác suất từ mô hình độc lập ≥75%, không lấy % từ odds để thay model.\n'
            '• EV theo mô hình ≥+3% trước chi phí; dữ liệu/giá còn mới, đủ lịch sử đội và lịch chính thức khớp.\n\n'
            'Không có trận qua bộ lọc thì kênh im lặng. Chấp/tài xỉu, giải nhỏ và LoL chưa đủ kiểm định nên chưa phát đề xuất ở đây.\n\n'
            'Báo cáo vẫn là thử nghiệm: không bảo đảm thắng hoặc sinh lời, không tự đặt cược. '
            'Dự báo đã gửi được khóa trước trận, theo dõi cả đúng và sai.\n'
            '[Mã nguồn và kiểm định](https://github.com/HNam1234/penaltyblog/tree/dnsgon-scout/examples/dnsgon_scout)')


def settle(config,store,now):
    """Corroborated full-time scores; never label this a bookmaker account P/L."""
    path=Path(config['model_results_file'])
    if not path.exists():return 0
    rows=json.loads(path.read_text(encoding='utf-8'))['matches']
    count=0
    for forecast in store.predictions():
        matches=[r for r in rows if r['team1']==forecast['home'] and r['team2']==forecast['away']
                 and abs((utc(r['start_time'])-utc(forecast['start_time'])).total_seconds())<=60]
        if len(matches)!=1:continue
        row=matches[0];known=utc(row['known_at']);goals=row['score']['ft']
        if (known>now or known<=utc(forecast['start_time']) or known<=utc(forecast['created_at'])
                or len(goals)!=2 or any(type(g) is not int or not 0<=g<=99 for g in goals)):
            continue
        outcome='home' if goals[0]>goals[1] else 'away' if goals[1]>goals[0] else 'draw'
        count+=store.result({'event_id':forecast['event_id'],'sport':'soccer_epl','market':'1x2','period':'regulation',
                             'line':None,'outcome':outcome,'status':'final','settlement_verified':True,
                             'settled_at':known.isoformat(),'known_at':known.isoformat(),
                             'source':'corroborated_full_time_result_not_bookmaker_financial_settlement'})
    return count


def scan(config,source,store,now=None):
    now=utc(now or datetime.now(timezone.utc));counts=Counter();reports=[]
    store.enqueue('highprob:setup:v1',welcome())
    status='ready_no_qualifying_match'
    try:
        artifact=json.loads(Path(config['model_file']).read_text(encoding='utf-8'))
        snapshot=source.latest_snapshot('analysis:current')
        fixtures=source.latest_snapshot('fpl:today:'+now.astimezone(VN).date().isoformat())
        if not snapshot or not timedelta(0)<=now-utc(snapshot['fetched_at'])<=timedelta(minutes=5):
            raise ValueError('current_analysis_unavailable_or_stale')
        if not fixtures or not timedelta(0)<=now-utc(fixtures['fetched_at'])<=timedelta(minutes=15):
            raise ValueError('official_schedule_unavailable_or_stale')
        reports=snapshot['data']['reports']
        for report in reports:
            try:
                row=eligible(report,artifact,fixtures['data'],now)
                if store.forecast(row):
                    counts['first_forecasts_locked']+=1
                stored=next(p for p in store.predictions() if p['id']==row['id'])
                if stored['model_version']!=row['model_version'] or stored['probabilities']!=row['probabilities']:
                    counts['previously_locked_forecast_changed']+=1;continue
                expiry=min(utc(row['start_time']),utc(row['quote_acquired_at'])+timedelta(minutes=5))
                counts['alerts_queued']+=store.enqueue('highprob:forecast:'+row['id'],card(stored),expiry.isoformat())
            except (ValueError,KeyError,TypeError,OverflowError) as error:
                counts[str(error) if type(error) is ValueError else 'invalid_candidate']+=1
        if counts['alerts_queued']:status='qualified_alert_queued'
        counts['results_saved']+=settle(config,store,now)
    except (OSError,ValueError,KeyError,TypeError,OverflowError) as error:
        status=str(error) if type(error) is ValueError else 'highprob_data_unavailable'
    detail={'analysis_count':len(reports),'counts':dict(counts),'probability_threshold':MIN_P,
            'minimum_model_ev_before_costs':MIN_EV,'scope':'EPL_1X2_only','qualified_this_run':counts['alerts_queued']}
    store.record_run(status,detail,now.isoformat())
    return {'status':status,**detail}


def run(config,source,clock=None):
    from publisher import flush
    from store import Store
    clock=clock or (lambda:datetime.now(timezone.utc))
    channel=config.get('high_probability_channel_id')
    if not channel:return {'enabled':False}
    if channel==config['channel_id']:raise ValueError('dedicated_highprob_channel_required')
    store=Store(config['high_probability_database'])
    try:
        recovered=store.recover_sending()
        result=scan(config,source,store,clock())
        result.update(enabled=True,messages_sent=flush(store,config['telegram_token'],channel,utc(clock()).isoformat(),clock=clock),
                      uncertain_messages_held=recovered)
        return result
    finally:store.close()
