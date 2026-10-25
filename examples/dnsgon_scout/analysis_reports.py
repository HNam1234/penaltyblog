"""Attach model-specific market analysis to real bookmaker fixtures."""
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path

from adapters import ProviderError
from dc_runtime import utc
from fun88 import VN
from market_analysis import analyze
from model_alerts import literal


def card(report, context):
    lines=['**PHÂN TÍCH KÈO · MÔ HÌNH THỬ NGHIỆM**',
           f"**{literal(report['home'])} — {literal(report['away'])}**",
           f"EPL · {utc(report['start_time']).astimezone(VN):%H:%M %d/%m} VN",'',
           '**Xác suất thắng trận từ model** (không suy từ odds):']
    p=report['model_1x2'];b=report['book_1x2']
    lines.append(f"• Chủ {p['home']:.1%} · hòa {p['draw']:.1%} · khách {p['away']:.1%}.")
    if b:lines.append(f"• Bảng giá Fun88 sau bỏ biên: {b['home']:.1%} / {b['draw']:.1%} / {b['away']:.1%}.")
    g=report['model_goal_expectation']
    lines.append(f"• Số bàn kỳ vọng từ model: {g['home']:.2f}–{g['away']:.2f}; đây không phải xG quan sát từ cú sút.")
    lines+=['','**Chấp và tài/xỉu — xác suất riêng cho từng cửa**']
    for row in report['markets']:
        if row['market']=='1x2':continue
        label={'home':'Chủ','away':'Khách','over':'Tài','under':'Xỉu'}[row['side']]
        o=row['probabilities']
        lines.append(f"• {label} {row['line']:+g} @ {row['decimal']:.3f}: lời {row['p_profit']:.1%}, lỗ {row['p_loss']:.1%}.")
        lines.append(f"  Đủ/nửa thắng: {o['win']:.1%}/{o['half_win']:.1%}; hòa tiền {o['push']:.1%}; nửa/đủ thua {o['half_loss']:.1%}/{o['loss']:.1%}.")
        fair=f"{row['fair_decimal']:.3f}" if row['fair_decimal'] is not None else 'không xác định'
        lines.append(f"  Giá hòa vốn theo model {fair}; EV trước chi phí {row['expected_net_per_unit_before_costs']:+.1%} trên đơn vị vốn.")
    lines+=['','**Tình trạng lực lượng theo FPL**']
    for name in (report['home'],report['away']):
        c=context.get(name)
        if c is None:
            lines.append(f'• {literal(name)}: chưa có dữ liệu nguồn.');continue
        people=c['availability_reports'][:3]
        labels={'i':'chấn thương','d':'chưa chắc ra sân','s':'treo giò','u':'không khả dụng'}
        display='; '.join(literal(x['player'])+' ('+labels[x['status']]+')' for x in people)
        lines.append(f"• {literal(name)}: {display or 'chưa thấy cờ vắng mặt'}.")
    lines+=['Chưa xác nhận đội hình xuất phát; cờ FPL có thể cập nhật chậm và chưa được đưa vào công thức xác suất.', '',
            '**Giới hạn:** chấp/tài xỉu đang dùng phân bố tỷ số thô, chưa kiểm định riêng. '
            'EV dương theo model chưa chứng minh có lợi thế thực tế; tỷ lệ đúng EPL 1X2 cũ không áp sang các kèo này.',
            f"Giá lấy {utc(report['quote_acquired_at']).astimezone(VN):%H:%M:%S} VN · model `{report['model_version']}`.",
            '[Mô hình và công thức](https://penaltyblog.readthedocs.io/en/latest/models/football_prob_grid.html) · '
            '[Nguồn lực lượng FPL](https://fantasy.premierleague.com/)']
    return '\n'.join(lines)


def enrich(config, store, events, now, clock):
    counts=Counter();reports=[]
    if not config.get('enable_market_analysis',False):return {'enabled':False}
    try:
        from today_fixtures import load as load_fixtures
        from team_context import load as load_context
        fixture_report=load_fixtures(store,now,clock=clock)
        artifact=json.loads(Path(config['model_file']).read_text(encoding='utf-8'))
        try:context=load_context(store,now,clock=clock)
        except (ProviderError,ValueError,KeyError,TypeError):context={};counts['team_context_unavailable']+=1
        for event in events:
            if event['competition_id']!='191':continue
            try:
                at=utc(clock());report=analyze(event,artifact,fixture_report['fixtures'],at)
                report['team_context']={n:context.get(n) for n in (report['home'],report['away'])}
                reports.append(report)
                provider='analysis:prematch:'+event['id']
                if store.latest_snapshot(provider) is None:
                    store.snapshot(provider,report,at.isoformat())
                    counts['first_analyses_locked']+=1
                # One research card per event, independent of the odds-watch quota.
                if config.get('send_market_analysis_cards',False):
                    expiry=min(utc(event['start_time']),utc(event['fetched_at'])+timedelta(minutes=5))
                    if expiry>at:
                        counts['cards_queued']+=store.enqueue('market-analysis:'+event['id']+':v1',card(report,context),expiry.isoformat())
            except (ValueError,KeyError,TypeError,OverflowError) as error:
                counts[str(error) if type(error) is ValueError else 'invalid_analysis_data']+=1
        store.snapshot('analysis:current',{'reports':reports,'coverage':'EPL_only_independent_model'},utc(clock()).isoformat())
    except (ProviderError,OSError,ValueError,KeyError,TypeError) as error:
        counts[error.code if isinstance(error,ProviderError) else 'analysis_source_unavailable']+=1
    return {'enabled':True,'events_analyzed':len(reports),'counts':dict(counts)}
