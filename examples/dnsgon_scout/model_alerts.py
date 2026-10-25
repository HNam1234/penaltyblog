"""Independent model forecasts; quotes inform the report, not the fit."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from dc_runtime import artifact_id, predict, qualify, utc


def literal(value):
    return re.sub(r'([\\`*_\[\]{}()])', r'\\\1', ' '.join(str(value).split())[:160])


def teams(title):
    parts = title.split(' vs. ')
    if len(parts) != 2 or any(not part or ' - ' in part for part in parts):
        raise ValueError('unmapped_fixture_title')
    return parts


class ModelAlerts:
    def __init__(self, artifact_path, store, now=None):
        self.store = store
        self.now = utc(now or datetime.now(timezone.utc))
        self.artifact = json.loads(Path(artifact_path).read_text(encoding='utf-8'))
        self.version = artifact_id(self.artifact)
        store.snapshot('independent_model:' + self.version, self.artifact, self.artifact['trained_at'])
        self.counts = Counter()
        self.existing = {r['id']: r for r in store.predictions()}

    def rank(self, event):
        try:
            home, away = teams(event['title'])
            result = qualify(self.artifact, home, away, self.now)
            return max(result['probabilities'].values())
        except (ValueError, KeyError, TypeError, OverflowError):
            return -1

    def observe(self, watch):
        try:
            now = utc(watch['fetched_at'])
            if utc(watch['start_time']) <= now:
                raise ValueError('fixture_already_started')
            home, away = teams(watch['title'])
            result = qualify(self.artifact, home, away, now)
            identity = sha256((watch['event_id'] + '|dc-independent-v1').encode()).hexdigest()[:24]
            prediction = self.existing.get(identity)
            if prediction is None:
                probabilities = result['probabilities']
                prediction = {'id': identity, 'event_id': watch['event_id'], 'sport': 'soccer_epl',
                              'home': home, 'away': away, 'market': '1x2', 'period': 'regulation', 'line': None,
                              'start_time': watch['start_time'], 'created_at': now.isoformat(),
                              'data_as_of': self.artifact['trained_at'], 'model_version': self.version,
                              'probabilities': probabilities, 'selection': max(probabilities, key=probabilities.get),
                              'method': self.artifact['method'], 'label': 'thử nghiệm',
                              'qualified': result['qualified'], 'eligibility_reason': result.get('reason'),
                              'historical_group': self.artifact['validation']['high_probability']}
                self.store.forecast(prediction)
                self.existing[identity] = prediction
                self.counts['new_forecasts_locked'] += 1
            # The stored first forecast defines the alert. No rewriting after odds change.
            if not prediction['qualified']:
                self.counts[prediction.get('eligibility_reason') or 'not_qualified'] += 1
                return
            selection = prediction['selection']
            raw_team = home if selection == 'home' else away
            quotes = [q for q in watch['quotes'] if (
                q['question'].startswith('Will ' + raw_team + ' win on ')
                if selection != 'draw' else q['question'].endswith('end in a draw?'))]
            if len(quotes) != 1 or quotes[0]['best_ask'] is None:
                self.counts['selected_outcome_has_no_ask'] += 1
                return
            quote = quotes[0]
            if float(quote['best_ask']['shares']) < float(quote['min_order_size_shares']):
                self.counts['insufficient_top_ask_size'] += 1
                return
            expiry = min(utc(prediction['start_time']), utc(quote['as_of']) + timedelta(minutes=5))
            if expiry <= now:
                self.counts['expired_quote'] += 1
                return
            probability = prediction['probabilities'][selection]
            price = float(quote['best_ask']['price'])
            if probability - price < 0.03:
                self.counts['model_price_gap_below_3_points'] += 1
                return
            perf = prediction['historical_group']
            chosen = 'Hòa' if selection == 'draw' else raw_team
            choice = 'Hòa sau 90 phút + bù giờ' if selection == 'draw' else literal(chosen) + ' thắng 90 phút + bù giờ'
            kickoff = utc(watch['start_time']).astimezone(timezone(timedelta(hours=7))).strftime('%d/%m %H:%M VN')
            interval = perf['wilson95']
            source_time = utc(quote['as_of']).isoformat()
            text = ('**DỰ BÁO XÁC SUẤT CAO · THỬ NGHIỆM**\n\n'
                    f"{literal(home)} — {literal(away)}\n"
                    f"Bắt đầu: {kickoff}\n"
                    f"Lựa chọn: {choice}.\n"
                    f"Xác suất mô hình: {probability:.1%}\n\n"
                    f"Nhóm dự báo ≥75% trong kiểm định lịch sử: {perf['hits']}/{perf['count']} đúng ({perf['hit_rate']:.1%}). "
                    f"Khoảng Wilson 95%: {interval[0]:.1%}–{interval[1]:.1%}.\n"
                    'Kiểm định EPL 2020–2026 theo ngày; chưa phải thành tích live.\n\n'
                    f"Giá mua Yes hiện tại: {quote['best_ask']['price']}; chưa trừ phí.\n"
                    f"Chênh xác suất mô hình − giá: {probability-price:+.1%}; chưa tính phí và sai số mô hình.\n"
                    f"Mốc sổ lệnh: {source_time}\n"
                    'Tỷ lệ đúng cao không tự chứng minh giá có lợi hoặc có lãi. Không có đề nghị mức tiền đặt.\n\n'
                    f"Đã khóa dự báo trước trận: `{identity}`. Bot sẽ lưu cả đúng và sai.\n"
                    '[Mã nguồn mô hình](https://github.com/HNam1234/penaltyblog/tree/dnsgon-scout/examples/dnsgon_scout)')
            if self.store.enqueue('model-alert:' + identity, text, expiry.isoformat()):
                self.counts['new_qualified_alerts'] += 1
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            self.counts[str(error) if isinstance(error, ValueError) else 'invalid_model_data'] += 1

    def settle(self, results_path):
        path = Path(results_path)
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding='utf-8'))
        for row in data['matches']:
            goals = row['score']['ft']
            known = utc(row['known_at'])
            if (known > self.now or known <= utc(row['start_time']) or not str(row['source_event_id']).isdigit()
                    or not isinstance(goals, list) or len(goals) != 2
                    or any(type(g) is not int or not 0 <= g <= 99 for g in goals)):
                continue
            outcome = 'home' if goals[0] > goals[1] else 'away' if goals[1] > goals[0] else 'draw'
            self.store.result({'event_id': 'polymarket:' + row['source_event_id'], 'sport': 'soccer_epl',
                               'market': '1x2', 'period': 'regulation', 'line': None,
                               'outcome': outcome, 'status': 'final', 'settlement_verified': True,
                               'settled_at': known.isoformat(), 'known_at': known.isoformat(),
                               'source': 'corroborated_resolved_polymarket_contracts'})


def scan(config, store, *, now=None, api=None, clock=None):
    from direct_watch import scan as collect
    from core import score_predictions
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = utc(now or clock())
    day = now.astimezone(timezone(timedelta(hours=7))).date().isoformat()
    try:
        model = ModelAlerts(config['model_file'], store, now)
        model.settle(config['model_results_file'])
        if config.get('model_data_dir'):
            from model_data import CLUB_CODES
            start_year = now.year if now.month >= 7 else now.year-1
            calendar = Path(config['model_data_dir'])/f'{start_year}-{str(start_year+1)[-2:]}.json'
            fixtures = json.loads(calendar.read_text(encoding='utf-8'))['matches']
            slugs = []
            for fixture in fixtures:
                if not now.date() <= datetime.fromisoformat(fixture['date']).date() <= (now+timedelta(days=2)).date():
                    continue
                h,a = CLUB_CODES.get(fixture['team1']), CLUB_CODES.get(fixture['team2'])
                if h and a:
                    slugs.append(f"epl-{h}-{a}-{fixture['date']}")
            config = {**config, 'fixture_slugs': sorted(set(slugs))[:20]}
    except (OSError, ValueError, KeyError, TypeError):
        store.record_run('model_unavailable', {}, now.isoformat())
        store.enqueue('model-unavailable:' + day,
                      '**NHỊP THỂ THAO · MÔ HÌNH TẠM DỪNG**\n\n'
                      'Chưa đọc được mô hình/kết quả đã kiểm tra. Bot đang dừng đề xuất, không thay bằng dự đoán của AI.')
        return {'status': 'model_unavailable', 'new_forecasts': 0, 'network_requests': 0}
    report = collect({**config, 'send_price_watch_cards': False}, store, now=now, api=api,
                     clock=clock, rank_event=model.rank, observe=model.observe)
    live = score_predictions([r for r in store.predictions() if r.get('qualified')], store.results(), now=utc(clock()))
    report.update(model_counts=dict(model.counts), new_forecasts=model.counts['new_forecasts_locked'],
                  model_id=model.version, live_scored=live['metrics']['count'],
                  fixture_lookup='exact_calendar_slugs' if 'fixture_slugs' in config else 'bounded_series_pages')
    store.record_run(report['status'], report, utc(clock()).isoformat())
    historical = model.artifact['validation']['high_probability']
    missing = model.artifact.get('data_coverage', {}).get('missing_past_fixtures', [])
    stale = (utc(clock()) - utc(model.artifact['trained_at'])).total_seconds() > 36*3600
    store.enqueue('model-daily:' + day,
                  '**NHỊP THỂ THAO · ĐÃ BẬT BỘ LỌC DỰ BÁO**\n\n'
                  'Phạm vi: bóng đá EPL, 1X2 trong 90 phút + bù giờ; dữ liệu thị trường công khai Polymarket.\n'
                  'Mỗi 5 phút: quét có giới hạn, ưu tiên xác suất cao. Chỉ báo khi mô hình ≥75%, '
                  'chênh mô hình − giá mua ≥3 điểm % trước phí và dữ liệu đủ mới. Không có trận đạt thì giữ im lặng.\n\n'
                  f"Kiểm định lịch sử: {historical['hits']}/{historical['count']} đúng ({historical['hit_rate']:.1%}) "
                  'trong nhóm mô hình ≥75%. Đây là hồi cứu theo thời gian, không phải thành tích live hay bằng chứng có lãi.\n'
                  f"Dự báo đạt ngưỡng đã có kết quả live: {live['metrics']['count']}.\n"
                  f"Kết quả huấn luyện mới nhất: {model.artifact['latest_result_date']}. "
                  f"Trận quá khứ còn thiếu kết quả: {len(missing)}; đội liên quan sẽ không được đề xuất.\n"
                  + ('Mô hình đang cũ, tạm dừng đề xuất cho đến lần cập nhật thành công.\n' if stale else '')
                  + '\nBot lưu cả đúng và sai, chống gửi lặp, không tự đặt cược.\n'
                  '[Mã nguồn và cách kiểm định](https://github.com/HNam1234/penaltyblog/tree/dnsgon-scout/examples/dnsgon_scout)')
    return report
