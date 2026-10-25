from datetime import date
import csv,gzip,io,unittest
from esports_stats import summarize


def packed(rows):
    text=io.StringIO();writer=csv.DictWriter(text,fieldnames=['game_id','date','team_id','team_name','opponent_team_name','result','side','patch','league'])
    writer.writeheader();writer.writerows(rows)
    return gzip.compress(text.getvalue().encode())


def pair(game='one',when='2026-10-24 10:00:00'):
    return [dict(game_id=game,date=when,team_id='a',team_name='Alpha',opponent_team_name='Beta',result='TRUE',side='Blue',patch='16.20',league='Worlds'),
            dict(game_id=game,date=when,team_id='b',team_name='Beta',opponent_team_name='Alpha',result='FALSE',side='Red',patch='16.20',league='Worlds')]


class LoLData(unittest.TestCase):
    def test_maps_not_series_and_descriptive_not_prediction(self):
        result=summarize(packed(pair()+pair('two')),date(2026,10,25))
        self.assertEqual(result['paired_maps'],2)
        self.assertEqual(result['teams']['a']['recent_wins'],2)
        self.assertEqual(result['unit'],'map_not_series')
        self.assertIsNone(result['model_probability'])
        self.assertEqual(result['source']['license'],'CC BY 4.0')

    def test_unpaired_contradictory_or_same_day_data_excluded(self):
        rows=pair('valid')+pair('today','2026-10-25 01:00:00')+pair('invalid')[:1]+pair('contradictory')
        bad=dict(rows[-1]);bad['result']='TRUE';rows.append(bad)
        result=summarize(packed(rows),date(2026,10,25))
        self.assertEqual(result['paired_maps'],1)
        self.assertEqual(result['filtered']['unpaired_or_conflicting_map'],2)

    def test_duplicate_rows_do_not_inflate_wins(self):
        rows=pair();result=summarize(packed(rows+rows),date(2026,10,25))
        self.assertEqual(result['paired_maps'],1)


if __name__=='__main__':unittest.main()
