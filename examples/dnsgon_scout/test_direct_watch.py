"""Offline public-watch integration; synthetic books, no account or orders."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from adapters import FeedResult, ProviderError
from direct_watch import scan
from publisher import flush
from scout import scan as dispatch
from store import Store
from test_direct import NOW, event, books


AT = datetime.fromisoformat(NOW)


class FakePublic:
    def __init__(self, count=1):
        self.rows, self.books, self.calls = [], {}, []
        self.error = None
        for index in range(count):
            row, source = event(), books()
            row['id'] = str(501 + index)
            row['title'] += ' ' + str(index)
            for offset, market in enumerate(row['markets']):
                old = str(1001 + 2 * offset)
                token = str(1001 + 100 * index + 2 * offset)
                market['id'] = str(101 + 10 * index + offset)
                market['clobTokenIds'] = json.dumps([token, str(int(token) + 1)])
                book = deepcopy(source[old])
                book['asset_id'] = token
                self.books[token] = book
            self.rows.append(row)

    def events(self, series, limit=20):
        self.calls.append(('events', str(series), limit))
        if self.error:
            raise self.error
        return FeedResult(deepcopy(self.rows), {}, NOW, 'fake_public', True)

    def book(self, token):
        self.calls.append(('book', token))
        return FeedResult(deepcopy(self.books[token]), {}, NOW, 'fake_public')


class DirectWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'sports.sqlite3')
        self.api = FakePublic()
        self.config = {'mode': 'polymarket_watch', 'direct_series_ids': ['10188']}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def scan(self, now=AT):
        return scan(self.config, self.store, now=now, clock=lambda: now, api=self.api)

    def observations(self):
        return [json.loads(row[0]) for row in self.store.db.execute("SELECT body FROM scout_snapshots WHERE provider='polymarket:compact_quote_observations' ORDER BY rowid")]

    def test_global_budget_four_events_twelve_books(self):
        self.api = FakePublic(6)
        report = self.scan()
        self.assertEqual(report['checked_events'], 4)
        self.assertEqual(report['observations'], 4)
        self.assertEqual(report['quote_count'], 12)
        self.assertEqual(report['network_requests'], 13)
        self.assertEqual(sum(call[0] == 'book' for call in self.api.calls), 12)
        self.assertEqual(self.store.predictions(), [])
        self.assertFalse(report['full_calendar'])

    def test_watch_dispatch_requires_no_api_key_and_creates_no_predictions(self):
        report = dispatch({**self.config, 'api_key_file': 'does-not-exist'}, self.store,
                          now=AT, clock=lambda: AT, api=self.api)
        self.assertEqual(report['status'], 'watching_public_prices')
        self.assertEqual(report['new_forecasts'], 0)
        self.assertEqual(self.store.predictions(), [])
        observation = self.observations()[0][0]
        self.assertFalse(observation['recommendation'])
        self.assertEqual(observation['independent_venues'], 1)
        self.assertNotIn('probability', observation)

    def test_observations_update_but_channel_card_is_once_per_day(self):
        self.scan()
        sent = []
        def sender(token, channel, message):
            sent.append(message)
            return len(sent)
        self.assertEqual(flush(self.store, 'unused', 'unused', NOW, sender=sender, clock=lambda: AT), 1)
        later = AT + timedelta(minutes=1)
        self.api.books['1001']['asks'][1]['price'] = '0.34'
        self.scan(later)
        self.assertEqual(flush(self.store, 'unused', 'unused', later.isoformat(), sender=sender, clock=lambda: later), 0)
        observations = self.observations()
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0][0]['quotes'][0]['best_ask']['price'], '0.33')
        self.assertEqual(observations[1][0]['quotes'][0]['best_ask']['price'], '0.34')
        self.assertEqual(self.store.predictions(), [])

    def test_stale_source_books_never_enter_quote_storage_or_card(self):
        stale = str(int((AT - timedelta(minutes=6)).timestamp() * 1000))
        for book in self.api.books.values():
            book['timestamp'] = stale
        report = self.scan()
        self.assertEqual(report['observations'], 0)
        self.assertEqual(self.observations(), [])
        self.assertFalse(self.store.db.execute("SELECT 1 FROM scout_outbox WHERE key LIKE 'price-watch:%'").fetchone())

    def test_partial_fresh_quotes_are_audit_only_not_full_card(self):
        self.api.books['1001']['timestamp'] = str(int((AT - timedelta(minutes=6)).timestamp() * 1000))
        report = self.scan()
        self.assertEqual(report['quote_count'], 2)
        self.assertEqual(report['complete_quote_groups'], 0)
        self.assertIsNone(self.store.next_message(NOW))
        self.assertEqual(len(self.observations()[0][0]['quotes']), 2)

    def test_expired_at_exact_deadline_does_not_consume_daily_alert_key(self):
        for book in self.api.books.values():
            book['timestamp'] = str(int((AT - timedelta(minutes=5)).timestamp() * 1000))
        self.scan()
        self.assertFalse(self.store.db.execute("SELECT 1 FROM scout_outbox WHERE key LIKE 'price-watch:%'").fetchone())
        for book in self.api.books.values():
            book['timestamp'] = str(int(AT.timestamp() * 1000))
        self.scan()
        self.assertTrue(self.store.next_message(NOW)['key'].startswith('price-watch:'))

    def test_feed_error_is_not_reported_as_no_fixtures_and_stays_quiet(self):
        self.api.error = ProviderError('network_unavailable')
        report = self.scan()
        self.assertEqual(report['status'], 'feed_unavailable')
        self.assertEqual(report['errors'], ['network_unavailable'])
        message = self.store.next_message(NOW)
        self.assertIn('Không được hiểu là hôm nay không có trận', message['text'])
        self.scan(AT + timedelta(minutes=1))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM scout_outbox').fetchone()[0], 1)
        self.assertEqual(self.store.predictions(), [])

    def test_empty_bounded_page_does_not_claim_complete_calendar(self):
        self.api.rows = []
        report = self.scan()
        self.assertEqual(report['status'], 'no_supported_market_in_page')
        self.assertFalse(report['full_calendar'])
        self.assertIn('Không được hiểu là hôm nay không có trận', self.store.next_message(NOW)['text'])

    def test_poll_budget_blocks_network(self):
        self.store.reserve_request('polymarket_poll', AT.date().isoformat(), limit=350, units=350)
        report = self.scan()
        self.assertEqual(report['status'], 'local_poll_budget')
        self.assertEqual(self.api.calls, [])
        self.assertEqual(report['network_requests'], 0)

    def test_bounded_pagination_ranks_before_spending_book_requests(self):
        api = FakePublic(25)
        def pages(series, limit=20, offset=0):
            api.calls.append(('events', str(series), limit, offset))
            return FeedResult(deepcopy(api.rows[offset:offset+limit]), {}, NOW, 'fake_public', True)
        api.events = pages
        seen = []
        report = scan({**self.config, 'metadata_pages': 3, 'send_price_watch_cards': False}, self.store,
                      now=AT, clock=lambda: AT, api=api,
                      rank_event=lambda e: int(e['id']), observe=lambda w: seen.append(w['event_id']))
        self.assertEqual(report['network_requests'], 14)
        self.assertEqual(report['candidate_events'], 25)
        self.assertEqual(seen, ['polymarket:'+str(i) for i in (525,524,523,522)])
        self.assertIsNone(self.store.next_message(NOW))


if __name__ == '__main__':
    unittest.main()
