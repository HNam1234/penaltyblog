"""Offline integration tests. Synthetic fixtures never reach the live DB/channel."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from dc_runtime import predict, qualify, metrics
from model_data import result_from_event, CLAUSE, refresh
from model_alerts import ModelAlerts, scan
from store import Store
from test_direct_watch import FakePublic, AT, NOW


def artifact():
    return {'schema': 1, 'method': 'penaltyblog_dixon_coles_v1', 'temperature': 1.0,
            'trained_at': (AT-timedelta(hours=1)).isoformat(), 'latest_result_date': '2026-10-24',
            'team_counts': {'Club H': 100, 'Club A': 100},
            'team_last_result': {'Club H': '2026-10-24', 'Club A': '2026-10-24'},
            'data_coverage': {'checked_at': (AT-timedelta(hours=1)).isoformat(), 'missing_past_fixtures': []},
            'params': {'attack_Club H': 1.0, 'defence_Club H': -0.3, 'attack_Club A': -0.2,
                       'defence_Club A': 0.2, 'home_advantage': 0.3, 'rho': -0.05},
            'validation': metrics([{'probabilities': {'home': .85, 'draw': .10, 'away': .05},
                                   'outcome': 'home' if i < 80 else 'away'} for i in range(100)])}


def fixture():
    return {'date': '2026-10-24', 'team1': 'Club H', 'team2': 'Club A'}


def final_event():
    return {'id': '501', 'title': 'Club H vs. Club A', 'closed': True, 'ended': True, 'period': 'VFT',
            'score': '2-1', 'startTime': '2026-10-24T14:00:00Z', 'markets': [
                {'question': question, 'sportsMarketType': 'moneyline', 'closed': True,
                 'umaResolutionStatus': 'resolved', 'description': CLAUSE,
                 'outcomes': '["Yes","No"]', 'outcomePrices': json.dumps(['1','0'] if i == 0 else ['0','1'])}
                for i, question in enumerate(('Will Club H win on 2026-10-24?',
                    'Will Club H vs. Club A end in a draw?', 'Will Club A win on 2026-10-24?'))]}


class ModelTests(unittest.TestCase):
    def test_below_68_is_rejected_and_68_boundary_is_inclusive(self):
        from unittest.mock import patch
        for p,expected in ((.679999,False),(.68,True),(.70,True)):
            with patch('dc_runtime.predict',return_value={'home':p,'draw':(1-p)/2,'away':(1-p)/2}):
                self.assertEqual(qualify(artifact(),'Club H','Club A',AT)['qualified'],expected)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.store = Store(self.folder/'scout.sqlite3')
        self.model = artifact()
        self.path = self.folder/'model.json'
        self.save()
        self.api = FakePublic()
        self.api.rows[0]['title'] = 'Club H vs. Club A'
        for m, q in zip(self.api.rows[0]['markets'], ('Will Club H win on 2026-10-25?',
                          'Will Club H vs. Club A end in a draw?', 'Will Club A win on 2026-10-25?')):
            m['question'] = q
        self.config = {'model_file': str(self.path), 'model_results_file': str(self.folder/'results.json')}

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def save(self):
        self.path.write_text(json.dumps(self.model), encoding='utf-8')

    def run_scan(self):
        return scan(self.config, self.store, now=AT, api=self.api, clock=lambda: AT)

    def test_prediction_normalizes_and_does_not_depend_on_market_price(self):
        p = predict(self.model, 'Club H', 'Club A')
        self.assertAlmostEqual(sum(p.values()), 1)
        self.assertGreater(p['home'], .68)
        first = self.run_scan()
        self.assertEqual(first['new_forecasts'], 1)
        self.assertEqual(first['model_counts']['new_qualified_alerts'], 1)
        self.api.books['1001']['asks'] = [{'price': '.99', 'size': '999'}]
        self.run_scan()
        self.assertEqual(len(self.store.predictions()), 1)
        self.assertEqual(self.store.predictions()[0]['probabilities'], p)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM scout_outbox WHERE key LIKE 'model-alert:%'").fetchone()[0], 1)

    def test_immutable_first_forecast_and_daily_status_dedup(self):
        self.run_scan()
        original = self.store.predictions()[0]
        self.model['params']['attack_Club H'] -= .1
        self.save(); self.run_scan()
        self.assertEqual(self.store.predictions(), [original])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM scout_outbox').fetchone()[0], 2)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM scout_outbox WHERE key LIKE 'price-watch:%'").fetchone())

    def test_price_above_model_chance_never_alerts(self):
        self.api.books['1001']['asks'] = [{'price': '.99', 'size': '999'}]
        r = self.run_scan()
        self.assertEqual(r['model_counts']['model_price_gap_below_3_points'], 1)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM scout_outbox WHERE key LIKE 'model-alert:%'").fetchone())
        self.assertEqual(len(self.store.predictions()), 1)

    def test_below_threshold_is_stored_for_honest_scoring(self):
        self.model['params']['attack_Club H'] = 0
        self.save(); r = self.run_scan()
        self.assertEqual(r['model_counts']['below_probability_threshold'], 1)
        self.assertFalse(self.store.predictions()[0]['qualified'])

    def test_stale_or_incomplete_team_data_blocks_forecasts(self):
        for mutate in (lambda a: a.update(latest_result_date='2026-09-01'),
                       lambda a: a['team_last_result'].update({'Club A': '2026-09-01'}),
                       lambda a: a['data_coverage'].update(checked_at='2026-10-20T00:00:00Z'),
                       lambda a: a['data_coverage']['missing_past_fixtures'].append({'home':'Club A','away':'Other'}),
                       lambda a: a.update(trained_at='2026-10-26T00:00:00Z')):
            data = artifact(); mutate(data)
            with self.assertRaises(ValueError): qualify(data, 'Club H', 'Club A', AT)

    def test_history_and_validation_gates(self):
        a = artifact(); a['team_counts']['Club A'] = 14
        with self.assertRaises(ValueError): qualify(a, 'Club H', 'Club A', AT)
        a = artifact(); a['validation']['high_probability']['count'] = 49
        self.assertFalse(qualify(a, 'Club H', 'Club A', AT)['qualified'])

    def test_model_missing_fails_closed_without_network(self):
        self.path.unlink()
        self.assertEqual(self.run_scan()['status'], 'model_unavailable')
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.store.predictions(), [])

    def test_book_staleness_does_not_lock_forecast(self):
        for book in self.api.books.values():
            book['timestamp'] = str(int((AT-timedelta(minutes=6)).timestamp()*1000))
        self.run_scan(); self.assertEqual(self.store.predictions(), [])

    def test_true_result_requires_three_resolutions_and_score_agreement(self):
        row = result_from_event(final_event(), fixture(), NOW)
        self.assertEqual(row['score']['ft'], [2,1])
        for field, value in [('score','0-1'), ('period','HT'), ('ended',False), ('title','Other'),
                             ('startTime','2026-10-23T14:00:00Z')]:
            data = final_event(); data[field] = value
            self.assertIsNone(result_from_event(data, fixture(), NOW))
        for prices in ('["0.5","0.5"]', '["0","1"]'):
            data = final_event(); data['markets'][0]['outcomePrices'] = prices
            self.assertIsNone(result_from_event(data, fixture(), NOW))
        data = final_event(); data['markets'].pop()
        self.assertIsNone(result_from_event(data, fixture(), NOW))

    def test_settlement_never_uses_future_knowledge(self):
        r = result_from_event(final_event(), fixture(), '2026-10-26T00:00:00Z')
        path = self.folder/'results.json'; path.write_text(json.dumps({'matches':[r]}))
        model = ModelAlerts(self.path, self.store, AT)
        model.settle(path); self.assertEqual(self.store.results(), [])
        r['known_at'] = NOW; path.write_text(json.dumps({'matches':[r]}))
        model.settle(path); model.settle(path)
        self.assertEqual(len(self.store.results()), 1)
        self.assertEqual(self.store.results()[0]['outcome'], 'home')

    def test_result_refresh_marks_missing_team_data(self):
        def fetch(url, *unused):
            if 'raw.githubusercontent' in url:
                return {'matches':[{'date':'2026-10-24','team1':'Arsenal FC','team2':'Chelsea FC'}]}, {}
            return [], {}
        report = refresh(self.folder/'data', now=AT, fetch=fetch)
        self.assertEqual(len(report['missing_past_fixtures']), 1)
        self.assertEqual(report['stored_recent_results'], 0)

    def test_calendar_uses_exact_slugs_instead_of_large_props_pages(self):
        folder = self.folder/'calendar'; folder.mkdir()
        (folder/'2026-27.json').write_text(json.dumps({'matches':[
            {'date':'2026-10-25','team1':'Liverpool FC','team2':'Brighton & Hove Albion FC'}]}))
        calls=[]
        def exact(slugs):
            calls.append(slugs)
            from adapters import FeedResult
            return FeedResult([],{},NOW,'fake_public',True)
        self.api.events_by_slugs=exact
        scan({**self.config,'model_data_dir':str(folder)},self.store,now=AT,api=self.api,clock=lambda:AT)
        self.assertEqual(calls,[['epl-liv-bri-2026-10-25']])
        self.assertEqual(self.api.calls,[])

    def test_old_club_names_keep_history_and_conflicts_are_rejected(self):
        from train import load_results
        p = self.folder/'training'; p.mkdir()
        first={'date':'2019-10-01','team1':'Manchester City','team2':'Aston Villa','score':{'ft':[2,1]}}
        (p/'first.json').write_text(json.dumps({'matches':[first]}))
        rows=load_results(p)
        self.assertEqual((rows[0]['home'],rows[0]['away']),('Manchester City FC','Aston Villa FC'))
        second={**first,'team1':'Manchester City FC','team2':'Aston Villa FC'}
        (p/'second.json').write_text(json.dumps({'matches':[second]}))
        self.assertEqual(len(load_results(p)),1)
        second['score']={'ft':[0,0]}; (p/'second.json').write_text(json.dumps({'matches':[second]}))
        with self.assertRaises(ValueError): load_results(p)


if __name__ == '__main__':
    unittest.main()
