from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import high_probability as high
from sports_agent import cycle
from sports_inference import review
from sports_review import MODEL,EFFORT,validate_decisions
import test_high_probability as hp_tests
from test_model import AT


def statuses(config,preview):
    return [{'key':'epl_dc_1x2','state':'ready','blocked_reasons':[],'source_deployed':True},
            {'key':'lol_elo','state':'research_only','blocked_reasons':['no_live_odds'],'source_deployed':True}]


def allow(packet):
    return {'cycle_note':'reviewed' if packet['candidates'] else 'no_new_candidate',
            'decisions':[{'id':r['id'],'digest':r['digest'],'allow':True,
                          'reason_ids':['model_probability','price_edge','historical_group'],
                          'caution_ids':['price_can_change'],'hold_reason':'none'} for r in packet['candidates']]}


class Agent(unittest.TestCase):
    setUp=hp_tests.DedicatedPipeline.setUp
    tearDown=hp_tests.DedicatedPipeline.tearDown
    def test_review_rechecks_and_locks_once_with_factual_explanation(self):
        packets=[]
        def fake(packet):packets.append(packet);return allow(packet)
        result=cycle(self.config,self.source,self.target,clock=lambda:AT,reviewer=fake,statuses=statuses)
        self.assertEqual(result['review_status'],'ok')
        self.assertEqual(result['pipeline']['qualified_this_run'],1)
        row=self.target.predictions()[0]
        self.assertEqual(row['ai_review']['model'],MODEL)
        text=high.card(row)
        self.assertIn('Vì sao chọn',text);self.assertIn('Điểm cần lưu ý',text)
        self.assertIn('Luna · Extra high',text)
        self.assertIn(f"{row['probability']:.1%}",text)
        second=cycle(self.config,self.source,self.target,clock=lambda:AT,reviewer=fake,statuses=statuses)
        self.assertEqual(packets[1]['candidates'],[])
        self.assertEqual(second['pipeline']['qualified_this_run'],0)
        self.assertEqual(len(self.target.predictions()),1)

    def test_no_inference_means_no_forecast(self):
        def down(packet):raise RuntimeError('unavailable')
        r=cycle(self.config,self.source,self.target,clock=lambda:AT,reviewer=down,statuses=statuses)
        self.assertEqual(r['review_status'],'unavailable')
        self.assertEqual(self.target.predictions(),[])

    def test_stale_after_review_does_not_send(self):
        time=[AT]
        def slow(packet):time[0]+=timedelta(minutes=6);return allow(packet)
        r=cycle(self.config,self.source,self.target,clock=lambda:time[0],reviewer=slow,statuses=statuses)
        self.assertEqual(r['pipeline']['qualified_this_run'],0)
        self.assertEqual(self.target.predictions(),[])

    def test_review_cannot_apply_to_changed_quote(self):
        def change(packet):
            report=deepcopy(self.report)
            report['quote_acquired_at']=(AT+timedelta(seconds=1)).isoformat()
            report['created_at']=report['quote_acquired_at']
            self.source.snapshot('analysis:current',{'reports':[report]},AT+timedelta(seconds=1))
            return allow(packet)
        time=[AT]
        def clock():
            value=time[0];time[0]=AT+timedelta(seconds=2);return value
        r=cycle(self.config,self.source,self.target,clock=clock,reviewer=change,statuses=statuses)
        self.assertEqual(r['pipeline']['qualified_this_run'],0)
        self.assertEqual(self.target.predictions(),[])

    def test_blocked_registry_cannot_create_candidate(self):
        def blocked(cfg,preview):
            return [{'key':'epl_dc_1x2','state':'source_missing','blocked_reasons':['missing'],'source_deployed':False}]
        r=cycle(self.config,self.source,self.target,clock=lambda:AT,reviewer=allow,statuses=blocked)
        self.assertEqual(r['new_candidates'],0);self.assertEqual(self.target.predictions(),[])

    def test_ai_cannot_change_probability_or_add_reasons(self):
        row=high.eligible(self.report,self.model,self.fixtures,AT)
        packet={'schema':1,'checked_at':AT.isoformat(),'models':statuses({},{}),
                'candidates':[{'id':row['id'],'digest':high.candidate_digest(row),'evidence':high.evidence(row),'cautions':high.CAUTIONS}]}
        bad=allow(packet);bad['decisions'][0]['probability']=.99
        with self.assertRaises(ValueError):validate_decisions(bad,packet)
        bad=allow(packet);bad['decisions'][0]['reason_ids'].append('confirmed_injury')
        with self.assertRaises(ValueError):validate_decisions(bad,packet)
        bad=allow(packet);bad['decisions'][0]['id']='f'*24
        with self.assertRaises(ValueError):validate_decisions(bad,packet)
        bad=allow(packet);bad['decisions']=[]
        with self.assertRaises(ValueError):validate_decisions(bad,packet)


class InferenceContract(unittest.TestCase):
    def test_exact_luna_ephemeral_no_tools_and_no_parent_secrets(self):
        packet={'schema':1,'checked_at':AT.isoformat(),'models':statuses({},{}),'candidates':[]}
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);(home/'sports-workspace').mkdir()
            def run(command,**kwargs):
                self.assertEqual(command[command.index('--model')+1],MODEL)
                self.assertIn('model_reasoning_effort="xhigh"',command)
                self.assertIn('features.shell_tool=false',command)
                self.assertIn('web_search="disabled"',command)
                self.assertIn('--ephemeral',command)
                self.assertEqual(set(kwargs['env']),{'HOME','CODEX_HOME','PATH','LANG'})
                output=Path(command[command.index('-o')+1]);output.write_text(json.dumps(allow(packet)))
                return subprocess.CompletedProcess(command,0,stderr=b'model: gpt-6-luna\nreasoning effort: xhigh\n')
            self.assertEqual(review(packet,run=run,home=home),allow(packet))


if __name__=='__main__':unittest.main()
