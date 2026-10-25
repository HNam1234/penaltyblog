"""Public bookmaker observations, kept separate from validated model forecasts."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from time import monotonic

from adapters import ProviderError
from dc_runtime import utc
from fun88 import GuestSports, SOURCE, VN, form, normalize_page, recent_results
from model_alerts import literal


def card(event, stats, now):
    prices = event['markets'].get('1x2', {}).get('selections', {})
    probabilities = event.get('market_probabilities', {})
    lines = ['**FUN88 · THEO DÕI ODDS**',
             '**Chưa xác nhận lợi thế — đây chưa phải khuyến nghị cược.**',
             f"{literal(event['competition'])} · {utc(event['start_time']).astimezone(VN):%H:%M %d/%m} VN",
             f"**{literal(event['home'])} — {literal(event['away'])}**", '', 'Kèo 90 phút, gồm bù giờ:']
    if prices:
        lines.append('• 1X2: ' + ' / '.join(f"{label} {prices[key]['decimal']:.3f}" for key, label in [('home', 'Chủ'), ('draw', 'Hòa'), ('away', 'Khách')]))
        lines.append('• Xác suất suy từ odds sau bỏ biên: ' + ' / '.join(f"{label} {probabilities[key]:.1%}" for key, label in [('home', 'chủ'), ('draw', 'hòa'), ('away', 'khách')]) + '.')
    for kind, label, labels in [('asian_handicap', 'Chấp', [('home', 'Chủ'), ('away', 'Khách')]),
                                ('total_goals', 'Tài/xỉu', [('over', 'Tài'), ('under', 'Xỉu')])]:
        market = event['markets'].get(kind)
        if market:
            q = market['selections']
            lines.append(f'• {label}: ' + ' / '.join(f"{name} {q[key]['line']:+g} @ {q[key]['decimal']:.3f}" for key, name in labels))
    lines.extend(['', '**Phong độ trong dữ liệu gần đây lấy được** (mới nhất trước):'])
    for side in ('home', 'away'):
        f = stats[side]
        record = ''.join({'W': 'T', 'D': 'H', 'L': 'B'}[x] for x in f['recent_first']) or 'chưa có'
        lines.append(f"• {literal(event[side])}: {f['count']} trận · {record} · {f['wins']}T/{f['draws']}H/{f['losses']}B · bàn {f['goals_for']}–{f['goals_against']}.")
    lines.extend(['T/H/B = thắng/hòa/bại; mẫu này có thể thiếu trận và không phản ánh đội hình hiện tại.', '',
                  'Dự báo độc lập và lợi nhuận kỳ vọng: **chưa được kiểm định cho trận này**. '
                  'Xác suất thắng trận khác xác suất thắng kèo chấp; không suy % thắng kèo từ hai odds chấp/tài xỉu.',
                  f"[Nguồn bảng kèo công khai]({SOURCE}) · lấy lúc {utc(now).astimezone(VN):%H:%M:%S %d/%m} VN."])
    return '\n'.join(lines)


def scan(config, store, now=None, api=None, clock=None, budget_seconds=100):
    clock = clock or (lambda: datetime.now(timezone.utc))
    now, began = utc(now or clock()), monotonic()
    store.prune_fun88_observations(now - timedelta(days=7))
    api = api or GuestSports(clock=clock)
    filtered, collected, conflicts = Counter(), {}, set()
    pages, discovered_pages, queued = [], None, 0
    limit = max(1, min(10, int(config.get('fun88_pages', 8))))
    previous = store.latest_snapshot('fun88:cursor')
    cursor = max(limit + 1, int(previous['data'].get('next_page', limit + 1))) if previous else limit + 1
    stats_calls, stats_available = 0, 0
    analysis_report = {'enabled':False}
    status = 'observed_partial'
    try:
        # Main pages plus two rotating pages: never claim complete coverage.
        desired = list(range(1, limit + 1)) + [cursor, cursor + 1]
        for page in desired:
            if monotonic() - began >= budget_seconds:
                filtered['scan_time_budget'] += 1
                break
            if discovered_pages is not None and page > discovered_pages:
                continue
            payload = api.page(page)
            discovered_pages = payload.get('PageSize')
            if type(discovered_pages) is not int or not 1 <= discovered_pages <= 100 or payload.get('Page') != page:
                raise ProviderError('fun88_paging_schema_changed')
            observed = utc(clock())
            events, rejected = normalize_page(payload, observed)
            filtered.update(rejected)
            pages.append(page)
            for event in events:
                if event['id'] in collected and collected[event['id']] != event:
                    conflicts.add(event['id'])
                collected.setdefault(event['id'], event)
            store.snapshot('fun88:page:' + str(page), {'events': events, 'total_pages': discovered_pages,
                           'partial': True, 'server_time': payload['ServerTime']}, observed.isoformat())
        for identity in conflicts:
            collected.pop(identity, None)
            filtered['event_changed_or_duplicated_across_pages'] += 1
        next_page = cursor + 2 if discovered_pages and cursor + 2 <= discovered_pages else limit + 1
        store.snapshot('fun88:cursor', {'next_page': next_page}, utc(clock()).isoformat())
        ranked = sorted((e for e in collected.values() if e.get('market_probabilities')),
                        key=lambda e: -max(e['market_probabilities'].values()))
        enriched = []
        for event in ranked[:12]:
            if monotonic() - began >= budget_seconds:
                filtered['stats_time_budget'] += 1
                break
            if max(event['market_probabilities'].values()) < .68:
                continue
            key = 'fun88:results:' + event['competition_id']
            cached = store.latest_snapshot(key)
            at = utc(clock())
            if cached and timedelta(0) <= at - utc(cached['fetched_at']) < timedelta(hours=6):
                results = cached['data']['results']
            else:
                if stats_calls >= 6:
                    filtered['stats_request_budget'] += 1
                    continue
                stats_calls += 1
                payload = api.results(event['competition_id'], at)
                results = recent_results(payload, event['competition_id'], at)
                store.snapshot(key, {'results': results, 'partial': True}, utc(clock()).isoformat())
            stats = {side: form(results, event[side + '_id']) for side in ('home', 'away')}
            stats_available += 1
            enriched.append({'event': event, 'form': stats, 'model_probability': None,
                             'probability_basis': 'single_book_market_implied_not_independent', 'qualified_edge': False})
            if any(f['count'] < 3 or not f['last_match'] or at - utc(f['last_match']) > timedelta(days=21) for f in stats.values()):
                filtered['insufficient_recent_team_statistics'] += 1
                continue
            # Cap informational cards at two per VN day. Never put them in model forecasts.
            day = at.astimezone(VN).date().isoformat()
            event_key = 'fun88:card:' + event['id']
            if store.latest_snapshot(event_key) or not config.get('send_fun88_watch_cards', False):
                continue
            expiry = min(utc(event['start_time']), utc(event['fetched_at']) + timedelta(minutes=5))
            if expiry <= at:
                filtered['quote_expired_before_publication'] += 1
                continue
            if store.reserve_request('fun88_daily_cards', day, limit=2):
                queued += store.enqueue(event_key, card(event, stats, at), expiry.isoformat())
                store.snapshot(event_key, {'at': at.isoformat(), 'informational_only': True}, at.isoformat())
        store.snapshot('fun88:enriched', {'rows': enriched, 'coverage': 'partial', 'qualified_edges': 0}, utc(clock()).isoformat())
        if config.get('enable_market_analysis',False):
            from analysis_reports import enrich
            analysis_report=enrich(config,store,list(collected.values()),utc(clock()),clock)
    except (ProviderError, OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        status = error.code if isinstance(error, ProviderError) else 'fun88_invalid_or_unavailable_data'
    detail = {'events_seen': len(collected), 'competitions_seen': len({e['competition_id'] for e in collected.values()}),
              'pages_scanned': pages, 'total_pages_reported': discovered_pages, 'coverage': 'partial',
              'network_requests': api.calls, 'statistics_enriched': stats_available, 'informational_cards_queued': queued,
              'qualified_edges': 0, 'filtered': dict(filtered), 'source': SOURCE,
              'market_analysis':analysis_report}
    store.record_run(status, detail, utc(clock()).isoformat())
    return {'status': status, **detail}
