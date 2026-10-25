"""Contracts, clock boundaries and quarter-line outcomes; no network in tests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
import unittest

from adapters import ProviderError
from fun88 import GuestSports, form, normalize_page, recent_results, settlement
from fun88_watch import scan
from store import Store

NOW = datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc)


def listing():
    def line(kind, values):
        return {'MarketlineId': kind, 'BetTypeId': kind, 'PeriodId': 1, 'MarketLineLevel': 1,
                'MarketlineStatusId': 1, 'IsLocked': False,
                'WagerSelections': [{'SelectionId': side, 'OddsType': 3, 'Odds': odds, 'Handicap': handicap}
                                    for side, odds, handicap in values]}
    event = {'EventId': '100', 'EventGroupTypeId': 1, 'RSportId': 1, 'HomeTeam': 'Home', 'AwayTeam': 'Away',
             'HomeTeamId': '10', 'AwayTeamId': '20', 'EventDate': '2026-10-25T21:00:00+07:00',
             'Market': 2, 'EventStatusId': 1, 'RBTimeStatus': 0, 'RBTime': None, 'HomeScore': '', 'AwayScore': '',
             'IsLive': True, 'IsBetTradeOpen': True, 'Competition': {'CompetitionName': 'China League 1', 'CompetitionId': '555'},
             'MarketLines': [line('1', [('1', 1.9, -.75), ('2', 1.9, -.75)]),
                             line('2', [('3', 1.9, 2.75), ('4', 1.9, 2.75)]),
                             line('3', [('5', 1.15, None), ('6', 15., None), ('7', 8., None)])]}
    return {'Sports': [{'SportId': 1, 'Events': [event]}], 'ServerTime': NOW.isoformat(),
            'StatusCode': 100, 'Page': 1, 'PageSize': 1}


def results():
    rows = []
    for i in range(3):
        rows.append({'EventId': str(1000 + i), 'EventTypeId': 1, 'EventGroupTypeId': 1, 'RSportId': 1,
                     'HomeTeamId': '10', 'AwayTeamId': '20', 'EventDate': (NOW-timedelta(days=i+1)).isoformat(),
                     'ResultList': [{'PeriodId': 1, 'HomeScore': '2', 'AwayScore': '1', 'IsCancelled': False}]})
    return {'Competitions': [{'CompetitionId': '555', 'SportId': 1, 'Events': rows}],
            'ServerTime': NOW.isoformat(), 'StatusCode': 100}


class GuestContracts(unittest.TestCase):
    def test_ui_home_handicap_sign_is_inverted_and_1x2_not_ah_probability(self):
        es, reasons = normalize_page(listing(), NOW)
        self.assertFalse(reasons)
        self.assertEqual(es[0]['markets']['asian_handicap']['selections']['home']['line'], .75)
        self.assertEqual(es[0]['markets']['asian_handicap']['selections']['away']['line'], -.75)
        self.assertAlmostEqual(sum(es[0]['market_probabilities'].values()), 1)
        self.assertNotIn('probability', es[0]['markets']['asian_handicap'])

    def test_reject_virtual_special_groups_inplay_tomorrow_and_status(self):
        changes = [{'HomeTeam': 'Barcelona (Cyberbattle)'}, {'EventGroupTypeId': 2}, {'RSportId': 54},
                   {'RBTimeStatus': 1}, {'HomeScore': '0'}, {'IsBetTradeOpen': False}, {'EventStatusId': 2},
                   {'EventDate': '2026-10-26T00:05:00+07:00'}, {'EventDate': NOW.isoformat()}]
        for change in changes:
            with self.subTest(change=change):
                d = listing(); d['Sports'][0]['Events'][0].update(change)
                self.assertEqual(normalize_page(d, NOW)[0], [])

    def test_suspended_incomplete_wrong_format_and_mismatched_handicaps(self):
        for change in [{'IsLocked': True}, {'MarketlineStatusId': 2}, {'PeriodId': 2}]:
            d=listing(); row=d['Sports'][0]['Events'][0]; row['MarketLines'][0].update(change)
            self.assertNotIn('asian_handicap', normalize_page(d, NOW)[0][0]['markets'])
        d=listing(); row=d['Sports'][0]['Events'][0]
        row['MarketLines'][0]['WagerSelections'][0]['OddsType']=2
        self.assertNotIn('asian_handicap', normalize_page(d, NOW)[0][0]['markets'])
        d=listing(); row=d['Sports'][0]['Events'][0]
        row['MarketLines'][0]['WagerSelections'][1]['Handicap']=.75
        self.assertNotIn('asian_handicap', normalize_page(d, NOW)[0][0]['markets'])

    def test_duplicate_main_markets_are_excluded(self):
        d=listing(); row=d['Sports'][0]['Events'][0]
        row['MarketLines'].append(deepcopy(row['MarketLines'][0]))
        self.assertNotIn('asian_handicap', normalize_page(d, NOW)[0][0]['markets'])

    def test_completed_results_exact_ids_no_future_or_cancelled_scores(self):
        d=results(); rows=d['Competitions'][0]['Events']
        rows.append({**rows[0], 'EventId':'500', 'EventDate':(NOW+timedelta(days=1)).isoformat()})
        rows.append({**rows[0], 'EventId':'501', 'HomeTeamId':'999', 'ResultList':[{'PeriodId':1,'HomeScore':'3','AwayScore':'0','IsCancelled':True}]})
        recent=recent_results(d,'555',NOW)
        self.assertEqual(len(recent),3)
        self.assertEqual(form(recent,'10')['wins'],3)
        self.assertEqual(form(recent,'20')['losses'],3)
        self.assertEqual(form(recent,'999')['count'],0)

    def test_contradictory_same_event_is_not_counted(self):
        d=results(); rows=d['Competitions'][0]['Events']
        altered=deepcopy(rows[0]); altered['ResultList'][0]['HomeScore']='0'; rows.append(altered)
        self.assertEqual(len(recent_results(d,'555',NOW)),2)

    def test_quarter_line_settlement(self):
        cases=[('asian_handicap','away',-.75,0,1,'half_win'),
               ('asian_handicap','home',.75,0,1,'half_loss'),
               ('asian_handicap','home',-1,1,0,'push'),
               ('asian_handicap','away',.25,0,0,'half_win'),
               ('total_goals','over',2.75,2,1,'half_win'),
               ('total_goals','under',2.75,2,1,'half_loss'),
               ('total_goals','under',2.25,1,1,'half_win'),
               ('total_goals','over',2.25,1,1,'half_loss'),
               ('total_goals','over',2.5,3,1,'win')]
        for args in cases:
            self.assertEqual(settlement(*args[:-1]),args[-1])

    def test_guest_only_operations_clock_and_anonymous_no_client_secret(self):
        bodies=[]
        def transport(body):
            bodies.append(body)
            return {'response':'public-sign'} if body['url']=='cdb60a1' else listing()
        client=GuestSports(transport,clock=lambda:NOW);client.page(1)
        self.assertEqual([b['url'] for b in bodies],['cdb60a1','601ef09'])
        self.assertEqual(bodies[-1]['data']['MemberCode'],'anonymous')
        self.assertNotIn('cc',bodies[-1]['data'])
        with self.assertRaises(ValueError):client._post('wallet')
        with self.assertRaises(ProviderError):GuestSports(transport,clock=lambda:NOW+timedelta(minutes=5)).page(1)


class WatchPipeline(unittest.TestCase):
    def test_retention_preserves_forecasts_and_sent_card_markers(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'state.db')
            old=NOW-timedelta(days=9)
            for provider in ['fun88:page:1','fun88:cursor','fun88:results:555','fun88:card:fun88:100','independent_model:old']:
                store.snapshot(provider,{},old)
            self.assertEqual(store.prune_fun88_observations(NOW-timedelta(days=7)),3)
            self.assertIsNotNone(store.latest_snapshot('fun88:card:fun88:100'))
            self.assertIsNotNone(store.latest_snapshot('independent_model:old'))
            store.close()

    def test_actual_information_not_model_forecast_idempotence_cache_and_card_limit(self):
        class API:
            calls=0
            def page(self,page): self.calls+=1; return listing()
            def results(self,*args): self.calls+=1; return results()
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'state.db');api=API()
            cfg={'fun88_pages':1,'send_fun88_watch_cards':True}
            first=scan(cfg,store,NOW,api,clock=lambda:NOW)
            self.assertEqual(first['informational_cards_queued'],1)
            self.assertEqual(store.predictions(),[])
            text=store.next_message(NOW)['text']
            self.assertIn('suy từ odds',text);self.assertIn('chưa được kiểm định',text)
            self.assertEqual(scan(cfg,store,NOW,api,clock=lambda:NOW)['informational_cards_queued'],0)
            self.assertEqual(store.latest_snapshot('fun88:enriched')['data']['rows'][0]['qualified_edge'],False)
            store.close()

    def test_short_history_does_not_trigger_watch(self):
        class API:
            calls=0
            def page(self,page): self.calls+=1; return listing()
            def results(self,*args):
                self.calls+=1;d=results();d['Competitions'][0]['Events']=d['Competitions'][0]['Events'][:1];return d
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'state.db')
            result=scan({'fun88_pages':1,'send_fun88_watch_cards':True},store,NOW,API(),clock=lambda:NOW)
            self.assertEqual(result['informational_cards_queued'],0)
            store.close()


if __name__=='__main__':unittest.main()
