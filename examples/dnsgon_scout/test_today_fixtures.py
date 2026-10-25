from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from adapters import ProviderError
from direct_watch import scan as collect
from model_alerts import scan
from store import Store
from today_fixtures import normalize, load, publish, NAMES
from test_direct_watch import AT, NOW, FakePublic


def teams():
    return [{'id':i+1,'name':name} for i,name in enumerate(list(NAMES)[:20])]


def fixtures():
    rows = [{'id':i+1,'team_h':1,'team_a':2,'kickoff_time':None,'started':False,'finished':False} for i in range(380)]
    rows[0].update(kickoff_time='2026-10-25T14:00:00Z')
    rows[1].update(team_h=2,team_a=1,kickoff_time='2026-10-25T18:00:00Z')  # 01:00 tomorrow VN
    return rows


class TodayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=Store(Path(self.temp.name)/'scout.sqlite3')
        self.calls=[]; self.error=None

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def fetch(self,url,*unused):
        self.calls.append(url)
        if self.error: raise self.error
        return ({'teams':teams()} if 'bootstrap' in url else fixtures()),{}

    def test_vietnam_day_includes_previous_utc_date_and_excludes_next_local_day(self):
        self.assertEqual([r['id'] for r in normalize(fixtures(),teams(),'2026-10-25')],[1])
        tomorrow=normalize(fixtures(),teams(),'2026-10-26')
        self.assertEqual([r['id'] for r in tomorrow],[2])
        self.assertEqual(tomorrow[0]['slug'],'epl-ast-ars-2026-10-25')

    def test_cache_refresh_and_day_card_are_deduplicated(self):
        result=load(self.store,AT,fetch=self.fetch,clock=lambda:AT)
        self.assertEqual(result['requests'],2)
        self.assertEqual(len(result['fixtures']),1)
        self.assertTrue(publish(self.store,result,AT))
        self.assertFalse(publish(self.store,result,AT))
        again=load(self.store,AT+timedelta(minutes=1),fetch=self.fetch,clock=lambda:AT+timedelta(minutes=1))
        self.assertEqual(again['requests'],0)
        later=AT+timedelta(minutes=16)
        self.assertEqual(load(self.store,later,fetch=self.fetch,clock=lambda:later)['requests'],1)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(self.store.next_message(NOW)['expires_at'],'2026-10-25T17:00:00.000000Z')

    def test_cache_refilter_prevents_started_match_from_becoming_an_alert(self):
        load(self.store,AT,fetch=self.fetch,clock=lambda:AT)
        # A fresh cache recorded one minute before kick-off is refiltered at kick-off.
        before=AT.replace(hour=13,minute=59)
        load(self.store,before,fetch=self.fetch,clock=lambda:before)
        start=before+timedelta(minutes=1)
        result=load(self.store,start,fetch=self.fetch,clock=lambda:start)
        self.assertEqual(result['status'],'no_upcoming_today')
        self.assertEqual(result['fixtures'],[])
        publish(self.store,result,start)
        self.assertIn('đọc thành công',self.store.next_message(start.isoformat())['text'])

    def test_stale_cache_never_turns_feed_error_into_no_matches(self):
        load(self.store,AT,fetch=self.fetch,clock=lambda:AT)
        self.error=ProviderError('network_unavailable')
        later=AT+timedelta(minutes=16)
        with self.assertRaises(ProviderError): load(self.store,later,fetch=self.fetch,clock=lambda:later)
        self.assertEqual(self.store.latest_snapshot('fpl:today:2026-10-25')['data'][0]['id'],1)

    def test_truncated_calendar_unmapped_teams_and_conflicting_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            load(self.store,AT,fetch=lambda url,*args: ({'teams':teams()} if 'bootstrap' in url else [] ,{}),clock=lambda:AT)
        unknown=teams(); unknown[0]['name']='Unknown'
        with self.assertRaises(ValueError): normalize(fixtures(),unknown,'2026-10-25')
        data=fixtures(); data[1].update(id=1,kickoff_time='2026-10-25T15:00:00Z')
        with self.assertRaises(ValueError): normalize(data,teams(),'2026-10-25')

    def test_today_window_and_cross_source_kickoff_are_enforced(self):
        api=FakePublic()
        row=api.rows[0]
        tomorrow='2026-10-25T18:00:00Z'
        row['startTime']=tomorrow
        for market in row['markets']: market['gameStartTime']=tomorrow
        result=collect({'today_only':True},self.store,now=AT,clock=lambda:AT,api=api)
        self.assertEqual(result['checked_events'],0)
        api=FakePublic(); api.rows[0]['slug']='epl-ars-ast-2026-10-25'
        result=collect({'today_only':True,'fixture_kickoffs':{'epl-ars-ast-2026-10-25':'2026-10-25T15:00:00Z'}},
                       self.store,now=AT,clock=lambda:AT,api=api)
        self.assertIn('kickoff_source_disagreement',result['errors'])
        self.assertEqual(result['checked_events'],0)

    def test_schedule_failure_stops_scan_without_claiming_empty_calendar(self):
        api=FakePublic(); self.error=ProviderError('network_unavailable')
        report=scan({'today_only':True},self.store,now=AT,clock=lambda:AT,api=api,schedule_fetch=self.fetch)
        self.assertEqual(report['status'],'schedule_unavailable')
        self.assertEqual(api.calls,[])
        self.assertIn('không có nghĩa hôm nay không có trận',self.store.next_message(NOW)['text'])

    def test_latest_snapshot_uses_time_and_does_not_modify_observations(self):
        self.store.snapshot('test',{'value':2},(AT+timedelta(seconds=2)).isoformat())
        self.store.snapshot('test',{'value':1},AT.isoformat())
        self.assertEqual(self.store.latest_snapshot('test')['data'],{'value':2})
        self.assertIsNone(self.store.latest_snapshot('missing'))


if __name__=='__main__':unittest.main()
