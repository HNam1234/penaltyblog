"""Quota-limited, pre-match football discovery through The Odds API."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters import TheOddsAPI, ProviderError
from core import analyze_event, score_predictions
from football import timestamp


def scan(config, store, render_candidate, *, now=None, api=None, clock=None):
    from odds import normalize, score_results
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = now or clock()
    at = timestamp(now).isoformat()
    if api is None:
        path = Path(config['odds_api_key_file'])
        if not path.exists() or not path.read_text().strip():
            store.record_run('awaiting_odds_key', {'network_requests': 0}, at)
            store.enqueue('setup:awaiting-odds-key:v1',
                          '**NHỊP THỂ THAO · ĐÃ NỐI VPS**\n\n'
                          'Đã tạo kênh riêng và chuẩn bị bộ lọc, lưu dự báo trước trận, '
                          'đối chiếu kết quả và gửi báo cáo chống trùng.\n\n'
                          '**Đang chờ key The Odds API gói Starter FREE.** '
                          'API-Football Free đã thử bằng key thật, nhưng chỉ cho mùa 2022–2024; '
                          'chưa đọc được mùa hiện tại.\n\n'
                          'Chưa có đề xuất trận hoặc thành tích sinh lời. Hệ thống không tự đặt cược.\n'
                          '[Đăng ký đúng nhà cung cấp](https://the-odds-api.com/#get-access)')
            return {'status': 'awaiting_odds_key', 'new_forecasts': 0, 'network_requests': 0}
        api = TheOddsAPI(path.read_text().strip())
    new_count, requests, seen, normalized = 0, 0, 0, 0
    reasons, errors, quota, snapshot_ids = Counter(), [], {}, {}
    previous = store.latest_run()
    def fetch(label, units, call):
        nonlocal requests, quota
        day = timestamp(clock()).date().isoformat()
        if not store.reserve_window('the_odds_api_credits', day, limit=450, units=units):
            raise ProviderError('local_31day_credit_limit')
        requests += 1
        response = call()
        quota = response.quota
        snapshot_ids[label] = store.snapshot('the_odds_api:' + label, response.data, response.fetched_at)
        if quota.get('remaining', 500) < 20:
            raise ProviderError('provider_credit_reserve_reached')
        return response
    for sport in config.get('sports', ['soccer_epl', 'soccer_spain_la_liga']):
        if not sport.startswith('soccer_'):
            errors.append('only_reviewed_football_supported')
            continue
        try:
            feed = fetch('odds:' + sport, 1, lambda: api.odds(sport, regions='eu', markets='h2h'))
            seen += len(feed.data)
            decision = max(timestamp(now), timestamp(feed.fetched_at), timestamp(clock()))
            events, rejected = normalize(feed.data, config.get('odds_book_rules', {}), now=decision)
            reasons.update(rejected)
            for event in events:
                if timestamp(event['start_time']) <= decision:
                    continue
                normalized += 1
                report = analyze_event(event, now=decision)
                reasons.update(r['reason'] for r in report['skipped'])
                for group in report['markets']:
                    reasons.update(group.get('filtered', {}))
                for candidate in report['candidates']:
                    candidate.update(rule_version='market-baseline-v1',
                                     source_snapshot_id=snapshot_ids['odds:' + sport],
                                     reviewed_book_rules=config.get('odds_book_rules', {}))
                    if store.forecast(candidate):
                        new_count += 1
                        expiry = min(timestamp(candidate['start_time']),
                                     timestamp(candidate['oldest_snapshot_at']) + timedelta(minutes=5))
                        store.enqueue('forecast:' + candidate['id'], render_candidate(candidate), expiry.isoformat())
            day = timestamp(clock()).date().isoformat()
            if store.reserve_request('the_odds_api_scores:' + sport, day, limit=1):
                scores = fetch('scores:' + sport, 2, lambda: api.scores(sport, days_from=3))
                for result in score_results(scores.data, scores.fetched_at,
                                            config.get('competition_rules', {})):
                    store.result(result)
        except (ProviderError, ValueError, TypeError, KeyError, AttributeError, OSError) as error:
            errors.append(error.code if isinstance(error, ProviderError) else 'invalid_or_unavailable_data')
    status = 'data_unavailable' if errors and not normalized else 'data_partial' if errors else 'scanned'
    detail = {'events_seen': seen, 'prematch_events': normalized, 'new_forecasts': new_count,
              'requests': requests, 'filtered': dict(reasons), 'errors': errors, 'quota': quota}
    store.record_run(status, detail, timestamp(clock()).isoformat())
    if status == 'data_unavailable':
        if not previous or previous['status'] != status or previous['detail'].get('errors') != errors:
            store.enqueue('feed-error:' + at, '**NHỊP THỂ THAO · CHƯA ĐỦ DỮ LIỆU**\n\n'
                          'Nguồn odds chưa đọc được hoặc không có cấu trúc dữ liệu phù hợp. '
                          'Đây không phải kết luận hôm nay không có trận.\n'
                          'Mã kiểm tra: `' + ', '.join(sorted(set(errors))) + '`')
    else:
        day = timestamp(now).astimezone(timezone(timedelta(hours=7))).date().isoformat()
        score = score_predictions(store.predictions(), store.results(), now=clock())
        count = score['metrics']['count']
        store.enqueue('daily:' + day, '**NHỊP THỂ THAO · BÁO CÁO NGÀY**\n\n'
                      f'Đã nhận {seen} sự kiện từ nguồn; {normalized} trận chưa bắt đầu được kiểm tra.\n'
                      f'Dự báo thử mới trong lượt này: {new_count}.\n'
                      f'Mẫu đã đối chiếu: {count}. Chưa có thành tích sinh lời đã kiểm chứng.\n\n'
                      'Bộ lọc chỉ dùng 1X2 đủ ba cửa, giá còn mới và nhà cái đã đối chiếu luật 90 phút. '
                      'Không đạt điều kiện thì không gửi đề xuất. Baseline từ giá thị trường chưa phải xác suất thắng thật.\n\n'
                      f'Giới hạn: hai giải bóng đá, bốn lượt/ngày; tối đa 450 credits trong 31 ngày.\n'
                      '[Nguồn The Odds API](https://the-odds-api.com/)')
    return {'status': status, 'new_forecasts': new_count, 'network_requests': requests,
            'events_seen': seen, 'filtered': dict(reasons), 'errors': errors, 'quota': quota}
