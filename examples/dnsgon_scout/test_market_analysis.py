from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analysis_reports import card,enrich
from dc_runtime import predict,score_grid
from fun88 import normalize_page
from market_analysis import analyze,contract,price_summary
from store import Store
from team_context import normalize as normalize_context
from test_model import artifact,AT
from test_fun88 import listing


class Contracts(unittest.TestCase):
    def test_quarter_lines_whole_stake_not_mean_probability(self):
        row=contract([(0,0,.25),(1,0,.5),(2,0,.25)],'asian_handicap','home',-.75,2.)
        self.assertEqual(row['probabilities'],{'win':.25,'half_win':.5,'push':0.,'half_loss':0.,'loss':.25})
        self.assertAlmostEqual(row['p_profit'],.75)
        self.assertAlmostEqual(row['expected_net_per_unit_before_costs'],.25)
        self.assertAlmostEqual(row['fair_decimal'],1.5)

    def test_quarter_total_and_expected_profit(self):
        row=contract([(0,2,.4),(1,2,.6)],'total_goals','under',2.75,1.9)
        self.assertAlmostEqual(row['probabilities']['win'],.4)
        self.assertAlmostEqual(row['probabilities']['half_loss'],.6)
        self.assertAlmostEqual(row['expected_net_per_unit_before_costs'],.06)

    def test_integer_push_is_not_a_win(self):
        row=contract([(1,0,.4),(0,0,.2),(0,1,.4)],'asian_handicap','home',0.,2.)
        self.assertEqual(row['probabilities']['push'],.2)
        self.assertEqual(row['p_profit'],.4)
        self.assertEqual(row['expected_net_per_unit_before_costs'],0.)

    def test_model_does_not_depend_on_offered_odds(self):
        cells=score_grid(artifact(),'Club H','Club A')
        first=contract(cells,'total_goals','over',2.5,1.5)
        second=contract(cells,'total_goals','over',2.5,2.5)
        self.assertEqual(first['probabilities'],second['probabilities'])
        self.assertNotEqual(first['expected_net_per_unit_before_costs'],second['expected_net_per_unit_before_costs'])
        self.assertFalse(first['qualified_edge'])

    def test_1x2_temperature_does_not_claim_to_calibrate_score_grid(self):
        a=artifact();raw=score_grid(a,'Club H','Club A');p=predict(a,'Club H','Club A')
        a['temperature']=1.7
        self.assertEqual(raw,score_grid(a,'Club H','Club A'))
        self.assertNotEqual(p,predict(a,'Club H','Club A'))

    def test_invalid_probabilities_rejected(self):
        with self.assertRaises(ValueError):price_summary({'win':1},2)


class RealFixtureGates(unittest.TestCase):
    def setUp(self):
        a=artifact()
        for key in ('team_counts','team_last_result'):
            a[key]={('Hull City AFC' if n=='Club H' else 'Brentford FC'):v for n,v in a[key].items()}
        a['params']={k.replace('Club H','Hull City AFC').replace('Club A','Brentford FC'):v for k,v in a['params'].items()}
        a['train_cutoff']='2026-10-25';self.model=a
        d=listing();r=d['Sports'][0]['Events'][0]
        r.update(HomeTeam='Hull City',AwayTeam='Brentford');r['Competition']={'CompetitionName':'*England Premier League','CompetitionId':'191'}
        self.event=normalize_page(d,AT)[0][0]
        self.fixtures=[{'id':44,'home':'Hull City AFC','away':'Brentford FC','start_time':self.event['start_time'],'started':False,'finished':False}]

    def test_all_supported_markets_from_one_verified_fixture(self):
        report=analyze(self.event,self.model,self.fixtures,AT)
        self.assertEqual(len(report['markets']),7)
        self.assertTrue(all(abs(sum(r['probabilities'].values())-1)<1e-8 for r in report['markets']))
        self.assertFalse(report['qualified_edge'])
        self.assertIn('chấp/tài xỉu',card(report,{}))

    def test_wrong_league_pair_kickoff_or_stale_price_refused(self):
        e=deepcopy(self.event);e['competition_id']='555'
        with self.assertRaisesRegex(ValueError,'no_independent_model'):analyze(e,self.model,self.fixtures,AT)
        with self.assertRaisesRegex(ValueError,'official_fixture'):analyze(self.event,self.model,[],AT)
        e=deepcopy(self.event);e['fetched_at']=(AT-timedelta(minutes=6)).isoformat()
        with self.assertRaisesRegex(ValueError,'quote_stale'):analyze(e,self.model,self.fixtures,AT)
        a=deepcopy(self.model);a['trained_at']=(AT+timedelta(minutes=1)).isoformat()
        with self.assertRaises(ValueError):analyze(self.event,a,self.fixtures,AT)

    def test_lock_first_analysis_and_no_repeated_card(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'model.json').write_text(json.dumps(self.model))
            cfg={'enable_market_analysis':True,'model_file':str(path/'model.json'),'send_market_analysis_cards':True}
            store=Store(path/'db.sqlite3')
            with patch('today_fixtures.load',return_value={'fixtures':self.fixtures}),patch('team_context.load',return_value={}):
                first=enrich(cfg,store,[self.event],AT,lambda:AT)
                self.assertEqual(first['events_analyzed'],1)
                self.assertEqual(first['counts']['first_analyses_locked'],1)
                second=enrich(cfg,store,[self.event],AT,lambda:AT)
                self.assertEqual(second['counts'].get('first_analyses_locked',0),0)
                self.assertEqual(second['counts']['cards_queued'],0)
            self.assertEqual(store.predictions(),[]) # Research analysis stays out of validated model scoring.
            store.close()


class Context(unittest.TestCase):
    def test_official_availability_is_context_not_probability_adjustment(self):
        from today_fixtures import NAMES
        names=list(NAMES)[:20]
        payload={'teams':[{'id':i+1,'name':n} for i,n in enumerate(names)],'elements':[{'id':9,'team':1,'web_name':'Example','status':'d','news_added':(AT-timedelta(days=1)).isoformat(),'minutes':200}]}
        result=normalize_context(payload,AT)
        team=result[NAMES[names[0]]]
        self.assertFalse(team['lineup_confirmed'])
        self.assertFalse(team['probability_adjustment_applied'])
        self.assertEqual(team['availability_reports'][0]['status'],'d')


if __name__=='__main__':unittest.main()
