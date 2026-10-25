"""Read-only football reports, immutable forecasts and private-channel delivery."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

from adapters import APISportsFootball, ProviderError
from core import analyze_event, score_predictions
from football import normalize, final_results, text, timestamp
from publisher import flush
from store import Store


UTC = timezone.utc
VN = timezone(timedelta(hours=7))


def literal(value):
    return re.sub(r'([\\`*_\[\]{}()])', r'\\\1', text(value))


def local_time(value):
    return timestamp(value).astimezone(VN).strftime('%d/%m %H:%M') + ' VN'


def candidate_card(row):
    selection = {'home': row['home'], 'away': row['away'], 'draw': 'Hòa'}.get(row['selection'], row['selection'])
    source = '[Nguồn: The Odds API](https://the-odds-api.com/)' if row['provider'] == 'the_odds_api' else '[Nguồn: API-Football](https://www.api-football.com/)'
    return (
        '**THEO DÕI THỬ · CHƯA KIỂM ĐỊNH**\n\n'
        f"{literal(row['home'])} — {literal(row['away'])}\n"
        f"Bắt đầu: {local_time(row['start_time'])}\n"
        f"1X2 · 90 phút · Lựa chọn: {literal(selection)}\n\n"
        f"Giá tham khảo: {row['offered_odds']:.3f} · {literal(row['bookmaker'])}\n"
        f"Baseline từ giá các nhà cái khác: {row['probability']:.1%}\n"
        f"Chênh lệch so baseline: {row['estimated_edge']:+.1%}\n"
        f"Số nhà cái tham chiếu: {len(row['reference_books'])}\n"
        f"Giá cũ nhất: {local_time(row['oldest_snapshot_at'])}\n\n"
        'Baseline này chưa phải xác suất thắng đã kiểm định. Chưa kiểm tra khả năng đặt đúng giá, '
        'thanh khoản hoặc giới hạn tài khoản.\n'
        f'{source}\n'
        f"Mã lưu trước trận: `{row['id']}`"
    )


def status_card(config):
    reason = literal(config.get('blocked_reason', 'Chưa có dữ liệu trận hiện tại đã xác minh.'))
    return (
        '**NHỊP THỂ THAO · TRẠNG THÁI HỆ THỐNG**\n\n'
        'Đã nối kênh riêng với VPS. Hệ thống lưu dữ liệu gốc, khóa dự báo trước trận, '
        'đối chiếu kết quả và chống gửi lặp.\n\n'
        f'**Đang chờ nguồn dữ liệu:** {reason}\n\n'
        'Key API-Football đã xác thực, nhưng gói Free từ chối mùa 2026; máy chủ yêu cầu dùng mùa 2022–2024. '
        'Thông tin coverage có mùa 2026 không đồng nghĩa tài khoản được đọc dữ liệu mùa đó.\n\n'
        '**Hiện chưa có đề xuất trận hoặc thành tích sinh lời.** Không sử dụng trận giả, '
        'dữ liệu cũ hay phần trăm do AI đoán để lấp báo cáo.\n\n'
        'Khi có nguồn phù hợp: kiểm tra odds đủ ba cửa, thời gian giá và luật thanh toán; '
        'lưu cả dự báo đúng lẫn sai. Báo cáo thử luôn ghi “chưa kiểm định”.\n\n'
        '[Nguồn API-Football](https://www.api-football.com/pricing/) · '
        'Kênh này chỉ gửi thống kê; chưa có chức năng đặt cược.'
    )


def scan(config, store, *, now=None, api=None, clock=None):
    clock = clock or (lambda: datetime.now(UTC))
    now = now or clock()
    at = now.isoformat()
    if config.get('mode') == 'polymarket_model':
        from model_alerts import scan as model_scan
        result = model_scan(config, store, now=now, api=api, clock=clock)
        if config.get('enable_fun88_guest', False) and api is None:
            from fun88_watch import scan as guest_scan
            result['fun88'] = guest_scan(config, store, clock=clock)
        return result
    if config.get('mode') == 'polymarket_watch':
        from direct_watch import scan as direct_scan
        return direct_scan(config, store, now=now, api=api, clock=clock)
    if config.get('mode') == 'the_odds_api':
        from odds_scout import scan as odds_scan
        return odds_scan(config, store, candidate_card, now=now, api=api, clock=clock)
    if config.get('mode') == 'blocked_plan':
        store.record_run('blocked_plan', {'reason': config.get('blocked_reason')}, at)
        store.enqueue('setup:blocked-api-football-2026:v1', status_card(config))
        return {'status': 'blocked_plan', 'new_forecasts': 0, 'network_requests': 0}
    if config.get('mode') != 'api_football':
        raise ValueError('unsupported_mode')
    if api is None:
        key = Path(config['api_key_file']).read_text(encoding='utf-8').strip()
        api = APISportsFootball(key)
    calls = 0
    def fetch(label, call):
        nonlocal calls
        request_day = timestamp(clock()).date().isoformat()
        if not store.reserve_request('api_football', request_day, limit=80):
            raise ProviderError('local_daily_budget_exhausted')
        calls += 1
        response = call()
        store.snapshot('api_football:' + label, response.data, response.fetched_at)
        if response.quota.get('daily_remaining', 100) <= 5:
            raise ProviderError('provider_quota_reserve_reached')
        return response
    created, fixtures_count, reasons, coverage = 0, 0, Counter(), []
    try:
        for league_id in config.get('league_ids', [39]):
            catalog = fetch('coverage', lambda: api.leagues(league=league_id))
            rows = [r for r in catalog.data['response'] if r.get('league', {}).get('id') == league_id]
            seasons = [s for row in rows for s in row.get('seasons', []) if s.get('current') is True]
            if len(seasons) != 1:
                reasons['current_season_unknown'] += 1
                continue
            season = seasons[0]['year']
            coverage.append({'league': league_id, 'season': season, 'coverage': seasons[0].get('coverage', {})})
            # Include yesterday for settlement; no fictitious end time from kickoff.
            past = fetch('results', lambda: api.fixtures((now - timedelta(days=1)).date().isoformat(), league=league_id, season=season))
            for row in final_results(past.data['response'], past.fetched_at):
                store.result(row)
            for offset in (0, 1):
                match_day = (now + timedelta(days=offset)).date().isoformat()
                fixtures = fetch('fixtures', lambda: api.fixtures(match_day, league=league_id, season=season))
                fixtures_count += len(fixtures.data['response'])
                if not fixtures.data['response']:
                    continue
                if not seasons[0].get('coverage', {}).get('odds'):
                    reasons['odds_not_covered'] += 1
                    continue
                odds_rows = []
                for page in range(1, 4):
                    odds = fetch('odds', lambda: api.odds(match_day, league=league_id, season=season, page=page))
                    odds_rows.extend(odds.data['response'])
                    total_pages = odds.data.get('paging', {}).get('total', 1)
                    if page >= total_pages:
                        break
                if total_pages > 3:
                    reasons['partial_odds_pages'] += 1
                events, rejected = normalize(fixtures.data['response'], odds_rows, config.get('book_rules', {}))
                reasons.update(rejected)
                # Acquisition finished after the scan began; use the real decision time.
                decision_at = max(now, timestamp(odds.fetched_at), timestamp(clock()))
                for event in events:
                    report = analyze_event(event, now=decision_at)
                    reasons.update(r['reason'] for r in report['skipped'])
                    for group in report['markets']:
                        reasons.update(group.get('filtered', {}))
                    for candidate in report['candidates']:
                        if store.forecast(candidate):
                            created += 1
                            # Expire at 5min from oldest quote as well as kickoff.
                            expires = min(timestamp(candidate['start_time']),
                                          timestamp(candidate['oldest_snapshot_at']) + timedelta(minutes=5))
                            store.enqueue('forecast:' + candidate['id'], candidate_card(candidate), expires.isoformat())
        data_issues = {'unreviewed_book', 'invalid_row', 'unknown_fixture', 'ambiguous_book_market',
                       'current_season_unknown', 'odds_not_covered', 'partial_odds_pages',
                       'missing_odds', 'stale_snapshot', 'invalid_aware_timestamp'}
        status = 'data_partial' if any(reasons[k] > 0 for k in data_issues) else 'scanned'
    except (ProviderError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        status = error.code if isinstance(error, ProviderError) else 'invalid_or_unavailable_data'
    detail = {'fixtures_seen': fixtures_count, 'new_forecasts': created, 'requests': calls,
              'filtered': dict(reasons), 'coverage': coverage}
    store.record_run(status, detail, timestamp(clock()).isoformat())
    # Each error reason posts once; repeating outages stay quiet.
    if status not in {'scanned', 'data_partial'}:
        store.enqueue('feed-state:' + status, '**NHỊP THỂ THAO · NGUỒN DỮ LIỆU**\n\n'
                      f'Mã trạng thái: `{status}`. Chưa thể xác nhận dữ liệu hiện tại; '
                      'không được hiểu là hôm nay không có trận.\n'
                      'Các dự báo đã lưu vẫn được giữ nguyên để đối chiếu.')
    return {'status': status, 'new_forecasts': created, 'network_requests': calls, 'filtered': dict(reasons)}


def main():
    import fcntl  # Linux service lock, independent of the existing life bot.
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/etc/dnsgon-sports.json')
    parser.add_argument('--report', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    db_path = Path(config.get('database', '/var/lib/dnsgon-sports/scout.sqlite3'))
    db_path.parent.mkdir(exist_ok=True)
    with db_path.with_suffix('.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        store = Store(db_path)
        if args.report:
            print(json.dumps({'latest_run': store.latest_run(),
                              'evaluation': score_predictions(store.predictions(), store.results())}))
            return
        recovered = store.recover_sending()
        result = scan(config, store)
        result.update(messages_sent=flush(store, config['telegram_token'], config['channel_id'], datetime.now(UTC).isoformat()),
                      interrupted_messages_held=recovered)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
