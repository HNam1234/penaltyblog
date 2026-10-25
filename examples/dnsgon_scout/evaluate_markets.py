"""Exploratory monthly walk-forward checks for fixed EPL AH/OU contracts.

No historical odds: this checks outcome probabilities, never profitability.
Different refit cadence from live daily model; not an alert eligibility certificate.
"""
import argparse,json
from collections import Counter
from math import log
from pathlib import Path

from dc_runtime import score_grid
from fun88 import settlement
from market_analysis import contract,OUTCOMES
from train import fit,load_results

CONTRACTS=[('asian_handicap','home',x) for x in (0.,-.5,-.75,-1.)]+[('total_goals','over',x) for x in (2.,2.25,2.5,2.75,3.)]


def summarize(rows):
    n=len(rows)
    high=[r for r in rows if r['probabilities']['win']+r['probabilities']['half_win']>=.75]
    def profitable(r):return r['actual'] in ('win','half_win')
    prevalence=sum(profitable(r) for r in rows)/n if n else None
    return {'count':n,
            'naive_50pct_profit_event_brier':.25 if n else None,
            'in_sample_constant_profit_event_brier_reference':prevalence*(1-prevalence) if n else None,
            'reference_note':'Reference uses full held-out prevalence, not a deployable baseline fit; descriptive comparison only.',
            'settlement_brier_5way':sum(sum((r['probabilities'][k]-(r['actual']==k))**2 for k in OUTCOMES) for r in rows)/n if n else None,
            'profit_event_brier':sum(((r['probabilities']['win']+r['probabilities']['half_win'])-profitable(r))**2 for r in rows)/n if n else None,
            'log_loss_5way':-sum(log(max(1e-15,r['probabilities'][r['actual']])) for r in rows)/n if n else None,
            'outcome_counts':dict(Counter(r['actual'] for r in rows)),
            'p_profit_at_least_75':{'count':len(high),'profitable_outcomes':sum(profitable(r) for r in high),
                                   'mean_predicted':sum(r['probabilities']['win']+r['probabilities']['half_win'] for r in high)/len(high) if high else None,
                                   'observed_rate':sum(profitable(r) for r in high)/len(high) if high else None}}


def run(data,output):
    rows=load_results(data);model=None;month=None;reports={str(c):[] for c in CONTRACTS};skipped=Counter()
    for row in rows:
        if not '2025-08-01'<=row['date']<'2026-06-01':continue
        if row['date'][:7]!=month:
            month=row['date'][:7];model=fit(rows,month+'-01')
            print('fit',month,flush=True)
        try:cells=score_grid(model,row['home'],row['away'])
        except ValueError as error:skipped[str(error)]+=1;continue
        for market,side,line in CONTRACTS:
            report=contract(cells,market,side,line,2.)
            reports[str((market,side,line))].append({'event':[row['date'],row['home'],row['away']],
                                                  'training_cutoff':model['train_cutoff'],
                                                  'probabilities':report['probabilities'],
                                                  'actual':settlement(market,side,line,row['hg'],row['ag'])})
    result={'evaluation':'exploratory_fixed_contract_monthly_walk_forward',
            'test_period':['2025-08-01','2026-05-31'],'fit':'rolling_prior_three_years_before_first_day_of_month',
            'probability_basis':'raw_Dixon_Coles_score_grid_no_AH_OU_calibration',
            'historical_odds_used':False,'profitability_evaluated':False,'alert_eligibility_changed':False,
            'limitations':['retrospective_current_edition_data_not_live_vintage',
                           'fixed_lines_not_actual_historical_bookmaker_markets',
                           'monthly_refit_differs_from_live_daily_refit',
                           'within_team_and_across_contract_dependence_no_independent_trials_claim'],
            'skipped_matches':dict(skipped),'contracts':{key:summarize(value) for key,value in reports.items()}}
    path=Path(output);path.mkdir(exist_ok=True,parents=True)
    (path/'market-validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    (path/'market-retrospective.json').write_text(json.dumps(reports),encoding='utf-8')
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.data,args.output)
