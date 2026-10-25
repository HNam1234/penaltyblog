"""Individual market probabilities from a fitted score model, not bookmaker odds.

Exact EPL fixture mapping is mandatory. Quarter-line outcomes describe a whole
stake, rather than treating the two split stakes as independent events.
"""
from datetime import timedelta
from math import isfinite

from dc_runtime import artifact_id, qualify, score_grid, utc
from fun88 import settlement

NAMES = {'Arsenal':'Arsenal FC', 'Aston Villa':'Aston Villa FC', 'Bournemouth':'AFC Bournemouth',
         'AFC Bournemouth':'AFC Bournemouth', 'Brentford':'Brentford FC',
         'Brighton Hove Albion':'Brighton & Hove Albion FC','Brighton & Hove Albion':'Brighton & Hove Albion FC',
         'Brighton And Hove Albion':'Brighton & Hove Albion FC',
         'Chelsea':'Chelsea FC','Crystal Palace':'Crystal Palace FC','Coventry City':'Coventry City FC',
         'Everton':'Everton FC','Fulham':'Fulham FC','Hull City':'Hull City AFC','Ipswich Town':'Ipswich Town FC',
         'Leeds United':'Leeds United FC','Liverpool':'Liverpool FC','Manchester City':'Manchester City FC',
         'Manchester United':'Manchester United FC','Newcastle United':'Newcastle United FC',
         'Nottingham Forest':'Nottingham Forest FC','Sunderland':'Sunderland AFC',
         'Tottenham Hotspur':'Tottenham Hotspur FC','West Ham United':'West Ham United FC',
         'Wolverhampton Wanderers':'Wolverhampton Wanderers FC'}
OUTCOMES = ('win', 'half_win', 'push', 'half_loss', 'loss')


def price_summary(outcomes, decimal):
    if isinstance(decimal, bool) or not isinstance(decimal,(int,float)) or not isfinite(decimal) or decimal<=1:
        raise ValueError('invalid_decimal_odds')
    if set(outcomes)!=set(OUTCOMES) or any(not isfinite(p) or p<0 for p in outcomes.values()) or abs(sum(outcomes.values())-1)>1e-8:
        raise ValueError('invalid_settlement_probabilities')
    win_weight=outcomes['win'] + .5*outcomes['half_win']
    loss_weight=outcomes['loss'] + .5*outcomes['half_loss']
    return {'p_profit':outcomes['win']+outcomes['half_win'], 'p_loss':outcomes['loss']+outcomes['half_loss'],
            'p_full_win':outcomes['win'], 'p_full_loss':outcomes['loss'],
            'fair_decimal':1+loss_weight/win_weight if win_weight>0 else None,
            'expected_net_per_unit_before_costs':win_weight*(decimal-1)-loss_weight}


def contract(cells, market, side, line, decimal, calibrated_1x2=None):
    probabilities=dict.fromkeys(OUTCOMES,0.)
    if market=='1x2':
        if side not in ('home','draw','away') or line is not None or calibrated_1x2 is None:
            raise ValueError('invalid_1x2_contract')
        p=calibrated_1x2[side]
        probabilities.update(win=p,loss=1-p)
    else:
        for h,a,p in cells:
            probabilities[settlement(market,side,line,h,a)]+=p
    return {'market':market,'side':side,'line':line,'decimal':decimal,
            'probabilities':probabilities,**price_summary(probabilities,decimal),
            'probability_basis':'calibrated_epl_1x2' if market=='1x2' else 'raw_dixon_coles_score_grid',
            'market_validation':'historical_1x2_only_no_profitability_claim' if market=='1x2' else 'not_validated_for_this_market',
            'qualified_edge':False}


def analyze(event, artifact, fixtures, now):
    now=utc(now)
    if event['competition_id']!='191' or event['competition']!='England Premier League':
        raise ValueError('no_independent_model_for_this_competition')
    home,away=NAMES.get(event['home']),NAMES.get(event['away'])
    if home is None or away is None:
        raise ValueError('unmapped_bookmaker_team')
    matches=[f for f in fixtures if f['home']==home and f['away']==away
             and abs((utc(f['start_time'])-utc(event['start_time'])).total_seconds())<=60
             and not f['started'] and not f['finished']]
    if len(matches)!=1:
        raise ValueError('official_fixture_not_confirmed')
    if not timedelta(0)<=now-utc(event['fetched_at'])<=timedelta(minutes=5) or utc(event['start_time'])<=now:
        raise ValueError('quote_stale_or_match_started')
    result=qualify(artifact,home,away,now)
    cells=score_grid(artifact,home,away)
    markets=[]
    for kind,market in event['markets'].items():
        for side,quote in market['selections'].items():
            markets.append(contract(cells,kind,side,quote['line'],quote['decimal'],result['probabilities']))
    expectations={'home':sum(h*p for h,a,p in cells),'away':sum(a*p for h,a,p in cells)}
    return {'schema':1,'event_id':event['id'],'home':home,'away':away,'start_time':event['start_time'],
            'created_at':now.isoformat(),'quote_acquired_at':event['fetched_at'],
            'model_version':artifact_id(artifact),'training_cutoff':artifact['train_cutoff'],
            'data_as_of':artifact['trained_at'],'model_goal_expectation':expectations,
            'team_sample_counts':{home:artifact['team_counts'][home],away:artifact['team_counts'][away]},
            'model_1x2':result['probabilities'],'book_1x2':event.get('market_probabilities'),
            'markets':markets,'official_fixture_id':matches[0]['id'],'qualified_edge':False,
            'limitations':['lineup_and_injury_context_not_in_probability_model',
                           'AH_OU_not_separately_validated','costs_execution_and_limits_not_verified']}
