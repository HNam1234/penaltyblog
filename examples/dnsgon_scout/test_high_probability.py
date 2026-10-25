from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from high_probability import card,eligible,run,scan
from market_analysis import analyze
from store import Store
import test_market_analysis as market_tests
from test_model import AT


def setup():
    helper=market_tests.RealFixtureGates();helper.setUp()
    # Synthetic winning-side prices: no synthetic data leaves these isolated tests.
    helper.event['markets']['1x2']['selections']={
        'home':{'decimal':1.2,'line':None},'away':{'decimal':12.,'line':None},'draw':{'decimal':10.,'line':None}}
    report=analyze(helper.event,helper.model,helper.fixtures,AT)
    return helper.model,helper.fixtures,report


class Eligibility(unittest.TestCase):
    def test_independent_probability_required_and_fair_price_gate(self):
        model,fixtures,report=setup()
        row=eligible(report,model,fixtures,AT)
        self.assertGreaterEqual(row['probability'],.75)
        self.assertGreaterEqual(row['expected_net_before_costs'],.03)
        self.assertIn('THỬ NGHIỆM',card(row))
        poor=deepcopy(report)
        for m in poor['markets']:
            if m['market']=='1x2' and m['side']=='home':m['decimal']=1.01
        with self.assertRaises(ValueError):eligible(poor,model,fixtures,AT)

    def test_wrong_probability_book_only_or_changed_model_cannot_qualify(self):
        model,fixtures,report=setup()
        r=deepcopy(report);r['model_1x2']={'home':.99,'away':.005,'draw':.005}
        with self.assertRaisesRegex(ValueError,'probability_mismatch'):eligible(r,model,fixtures,AT)
        r=deepcopy(report);r['model_version']='some-other-model'
        with self.assertRaisesRegex(ValueError,'version_mismatch'):eligible(r,model,fixtures,AT)
        r=deepcopy(report);r['markets']=[m for m in r['markets'] if m['market']!='1x2']
        with self.assertRaisesRegex(ValueError,'incomplete'):eligible(r,model,fixtures,AT)

    def test_insufficient_history_stale_model_stale_quote_and_started_refused(self):
        model,fixtures,report=setup()
        for key in ('created_at','quote_acquired_at'):
            r=deepcopy(report);r[key]=(AT-timedelta(minutes=6)).isoformat()
            with self.assertRaisesRegex(ValueError,'stale'):eligible(r,model,fixtures,AT)
        r=deepcopy(report);r['start_time']=AT.isoformat()
        with self.assertRaisesRegex(ValueError,'not_upcoming'):eligible(r,model,fixtures,AT)
        a=deepcopy(model);a['trained_at']=(AT-timedelta(hours=40)).isoformat()
        with self.assertRaisesRegex(ValueError,'model_stale'):eligible(report,a,fixtures,AT)
        a=deepcopy(model);a['team_counts'][report['home']]=5
        # A changed artifact must be rebuilt by analysis, never blindly accepted.
        from dc_runtime import artifact_id
        r=deepcopy(report);r['model_version']=artifact_id(a)
        with self.assertRaisesRegex(ValueError,'insufficient_team_history'):eligible(r,a,fixtures,AT)

    def test_official_fixture_and_historical_group_required(self):
        model,fixtures,report=setup()
        with self.assertRaisesRegex(ValueError,'official_fixture'):eligible(report,model,[],AT)
        a=deepcopy(model);a['validation']['high_probability']['count']=10
        from dc_runtime import artifact_id
        r=deepcopy(report);r['model_version']=artifact_id(a)
        with self.assertRaisesRegex(ValueError,'unverified'):eligible(r,a,fixtures,AT)


class DedicatedPipeline(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.source=Store(self.folder/'source.db');self.target=Store(self.folder/'high.db')
        self.model,self.fixtures,self.report=setup()
        self.path=self.folder/'model.json';self.path.write_text(json.dumps(self.model))
        self.results=self.folder/'results.json';self.results.write_text(json.dumps({'matches':[]}))
        self.config={'model_file':str(self.path),'model_results_file':str(self.results)}
        self.source.snapshot('analysis:current',{'reports':[self.report]},AT)
        self.source.snapshot('fpl:today:2026-10-25',self.fixtures,AT)

    def tearDown(self):self.source.close();self.target.close();self.temp.cleanup()

    def test_eligible_match_frozen_once_and_repeated_run_has_no_duplicate(self):
        result=scan(self.config,self.source,self.target,AT)
        self.assertEqual(result['qualified_this_run'],1)
        self.assertEqual(len(self.target.predictions()),1)
        original=self.target.predictions()[0]
        self.assertEqual(scan(self.config,self.source,self.target,AT)['qualified_this_run'],0)
        self.assertEqual(self.target.predictions()[0],original)
        self.assertEqual(self.source.predictions(),[])
        self.assertEqual(self.target.db.execute('SELECT count(*) FROM scout_outbox').fetchone()[0],2) # welcome + one real-test candidate

    def test_stale_feed_sends_no_candidate_and_unknown_scope_never_enters(self):
        result=scan(self.config,self.source,self.target,AT+timedelta(minutes=6))
        self.assertIn('stale',result['status'])
        self.assertEqual(self.target.predictions(),[])
        self.assertEqual(self.target.db.execute("SELECT count(*) FROM scout_outbox WHERE key LIKE 'highprob:forecast:%'").fetchone()[0],0)

    def test_same_channel_as_research_refused(self):
        cfg={**self.config,'channel_id':-1001111111111,'high_probability_channel_id':-1001111111111}
        with self.assertRaisesRegex(ValueError,'dedicated'):run(cfg,self.source,clock=lambda:AT)

    def test_final_scores_store_both_wins_and_losses_after_forecast(self):
        from high_probability import settle
        scan(self.config,self.source,self.target,AT)
        row={'team1':self.report['home'],'team2':self.report['away'],'start_time':self.report['start_time'],
             'known_at':(AT+timedelta(hours=10)).isoformat(),'score':{'ft':[0,1]}}
        self.results.write_text(json.dumps({'matches':[row]}))
        self.assertEqual(settle(self.config,self.target,AT+timedelta(hours=9)),0)
        self.assertEqual(settle(self.config,self.target,AT+timedelta(hours=11)),1)
        self.assertEqual(self.target.results()[0]['outcome'],'away')
        self.assertEqual(settle(self.config,self.target,AT+timedelta(hours=11)),0)


if __name__=='__main__':unittest.main()
