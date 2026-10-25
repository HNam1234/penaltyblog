"""Small public price collector. Observations never enter the forecast table."""
from datetime import datetime, timedelta, timezone
import re

from adapters import ProviderError
from direct import Polymarket, moneyline_markets, watch_event
from football import timestamp


def literal(value, limit=150):
    value = ' '.join(str(value or '').split())[:limit]
    return re.sub(r'([\\`*_\[\]{}()])', r'\\\1', value)


def local_time(value):
    return timestamp(value).astimezone(timezone(timedelta(hours=7))).strftime('%d/%m %H:%M') + ' VN'


def card(watch):
    lines = ['**THEO DÕI GIÁ · POLYMARKET**', '', literal(watch['title']),
             'Bắt đầu: ' + local_time(watch['start_time']), '',
             f"Đọc được {len(watch['quotes'])}/3 hợp đồng Yes/No trong 90 phút + bù giờ."]
    for quote in watch['quotes']:
        bid, ask = quote['best_bid'], quote['best_ask']
        buy = f"{float(ask['price']):.4f}" if ask else 'không có'
        sell = f"{float(bid['price']):.4f}" if bid else 'không có'
        lines += ['', literal(quote['question']), f'Giá mua Yes: {buy} · bán: {sell}']
        if ask:
            lines.append(f"Lượng tại giá mua tốt nhất: {float(ask['shares']):,.2f} cổ phần")
        lines.append('Mốc sổ lệnh: ' + local_time(quote['as_of']))
    lines += ['', 'Đơn vị: giá mỗi cổ phần nhị phân có mệnh giá trả thưởng 1; giá chưa trừ phí. '
              'Phí áp dụng tùy thị trường; chưa tính tỷ lệ cụ thể.', '',
              '**Đây là giá quan sát từ một sàn, chưa phải dự đoán thắng hoặc đề xuất đặt tiền.** '
              'Trang nguồn có luật riêng về hoãn/hủy trận. Phạm vi thu thập có giới hạn, không phải toàn bộ lịch đấu.']
    slug = watch.get('slug', '')
    if re.fullmatch(r'[a-z0-9-]{1,200}', slug):
        lines += ['', '[Xem nguồn thị trường](https://polymarket.com/event/' + slug + ')']
    return '\n'.join(lines)


def scan(config, store, *, now=None, api=None, clock=None, rank_event=None, observe=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = timestamp(now or clock())
    day = now.date().isoformat()
    api = api or Polymarket(timeout=8)
    previous = store.latest_run()
    if not store.reserve_request('polymarket_poll', day, limit=350):
        store.record_run('local_poll_budget', {'network_requests': 0}, now.isoformat())
        return {'status': 'local_poll_budget', 'network_requests': 0, 'observations': 0}
    observations, errors, calls, checked = [], [], 0, 0
    candidates = {}
    pages = min(5, max(1, int(config.get('metadata_pages', 1))))
    exact_slugs = config.get('fixture_slugs')
    for series_id in config.get('direct_series_ids', ['10188'])[:1]:
        try:
            for page in range(pages):
                calls += 1
                if exact_slugs is not None:
                    if not exact_slugs:
                        calls -= 1
                        break
                    events = api.events_by_slugs(exact_slugs)
                else:
                    events = api.events(series_id, limit=20) if not page else api.events(series_id, limit=20, offset=page*20)
                # Retain original metadata once/day; compact source quotes each poll.
                if store.reserve_request('polymarket_metadata:' + str(series_id) + ':' + str(page), day, limit=1):
                    store.snapshot('polymarket:gamma:' + str(series_id), events.data, events.fetched_at)
                for event in events.data:
                    try:
                        markets = moneyline_markets(event, clock())
                        if not markets:
                            continue
                        start = timestamp(markets[0]['start_time'])
                        if start > now + timedelta(hours=36):
                            continue
                        candidates[str(event['id'])] = (event, markets)
                    except (ValueError, TypeError, KeyError, AttributeError):
                        continue
                if exact_slugs is not None or len(events.data) < 20:
                    break
        except (ProviderError, ValueError, TypeError, KeyError, AttributeError, OSError) as error:
            errors.append(error.code if isinstance(error, ProviderError) else 'invalid_public_feed')
    chosen = list(candidates.values())
    if rank_event:
        chosen.sort(key=lambda pair: rank_event(pair[0]), reverse=True)
    for event, markets in chosen[:4]:
        checked += 1
        books = {}
        for market in markets:
            try:
                calls += 1
                book = api.book(market['token_id'])
                books[market['token_id']] = book.data
            except ProviderError as error:
                errors.append(error.code)
        try:
            observed = watch_event(event, books, timestamp(clock()).isoformat())
            observed['slug'] = event.get('slug', '')
            if observed['status'] == 'quotes_available':
                observations.append(observed)
        except (ValueError, TypeError, KeyError, AttributeError):
            errors.append('invalid_public_book_group')
    at = timestamp(clock())
    if observations:
        store.snapshot('polymarket:compact_quote_observations', observations, at.isoformat())
    status = 'watching_public_prices' if observations else 'feed_unavailable' if errors else 'no_supported_market_in_page'
    detail = {'network_requests': calls, 'checked_events': checked, 'observations': len(observations),
              'quote_count': sum(len(w['quotes']) for w in observations),
              'complete_quote_groups': sum(len(w['quotes']) == 3 for w in observations),
              'errors': sorted(set(errors)), 'independent_venues': 1,
              'new_forecasts': 0, 'candidate_events': len(candidates), 'full_calendar': False}
    store.record_run(status, detail, at.isoformat())
    if status == 'watching_public_prices':
        vn_day = now.astimezone(timezone(timedelta(hours=7))).date().isoformat()
        for observation in observations:
            if len(observation['quotes']) != 3:
                continue
            oldest = min(timestamp(q['as_of']) for q in observation['quotes'])
            expires = min(timestamp(observation['start_time']), oldest + timedelta(minutes=5))
            if expires <= at:
                continue
            if observe:
                observe(observation)
            if config.get('send_price_watch_cards', True):
                store.enqueue('price-watch:' + vn_day + ':' + observation['event_id'], card(observation), expires.isoformat())
    elif not previous or previous['status'] != status or previous['detail'].get('errors') != detail['errors']:
        store.enqueue('public-feed-state:' + at.isoformat(),
                      '**NHỊP THỂ THAO · NGUỒN CÔNG KHAI**\n\n'
                      f'Trạng thái: `{status}`. Chưa có bảng giá đủ điều kiện từ phạm vi đang đọc. '
                      'Không được hiểu là hôm nay không có trận.\n'
                      'Hệ thống tiếp tục polling có giới hạn; không dùng giá cũ thay dữ liệu hiện tại.')
    return {'status': status, **detail}
