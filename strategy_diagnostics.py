"""Step 9: Offline, read-only trade-ledger diagnostics. No MetaTrader connection.
Usage: python strategy_diagnostics.py --trades trades.csv --summary summary.json
"""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd

NUMERIC = ['entry','stop','target','exit','entry_spread_pips','exit_spread_pips','atr14','lots','commission_usd','pnl_usd','r_multiple','equity_after']
DATES = ['signal_time_utc','entry_time_utc','exit_time_utc']
REQUIRED = ['strategy','direction','exit_reason'] + NUMERIC + DATES

def load_inputs(trades_path, summary_path):
    df = pd.read_csv(trades_path)
    missing = sorted(set(REQUIRED) - set(df.columns))
    if missing: raise ValueError(f'Missing required trade columns: {missing}')
    for c in DATES:
        df[c] = pd.to_datetime(df[c], errors='coerce', utc=True, format='mixed')
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    with open(summary_path, encoding='utf-8') as f: summary = json.load(f)
    return df, summary

def stable_group(df, groupcols):
    if isinstance(groupcols, str): groupcols = [groupcols]
    output = []
    for keys, group in df.groupby(groupcols, dropna=False, observed=True):
        if not isinstance(keys, tuple): keys = (keys,)
        vals = group['pnl_usd']
        gains = vals[vals>0].sum(); losses = -vals[vals<0].sum()
        output.append({**dict(zip(groupcols,[str(x) for x in keys])), 'trades':len(group),
                       'wins':int((vals>0).sum()),'win_rate_pct':round(100*(vals>0).mean(),2),
                       'net_pnl_usd':round(float(vals.sum()),2),'mean_pnl_usd':round(float(vals.mean()),2),
                       'avg_r':round(float(group['r_multiple'].mean()),3),
                       'profit_factor':round(float(gains/losses),3) if losses>0 else None})
    return output

def analyze(df, summary):
    d = df.copy()
    for c in DATES:
        if d[c].isna().any(): raise ValueError(f'Invalid timestamps in {c}: {d[c].isna().sum()}')
    if d[NUMERIC].isna().any().any(): raise ValueError('Missing/non-numeric values in numeric columns')
    d['holding_minutes'] = (d.exit_time_utc-d.entry_time_utc).dt.total_seconds()/60
    d['entry_hour_utc'] = d.entry_time_utc.dt.hour
    d['entry_weekday_utc'] = d.entry_time_utc.dt.day_name()
    d['entry_month_utc'] = d.entry_time_utc.dt.to_period('M').astype(str)
    # These are UTC-clock buckets, NOT verified geographical trading sessions.
    d['session_utc_proxy'] = pd.cut(d.entry_hour_utc, bins=[-1,6,12,20,23],
                                     labels=['00-06 UTC','07-12 UTC','13-20 UTC','21-23 UTC']).astype(str)
    d['atr_pips'] = d.atr14/float(summary.get('config',{}).get('pip_size',0.0001))
    d['spread_entry_pips'] = d.entry_spread_pips
    d['total_spread_pips'] = d.entry_spread_pips+d.exit_spread_pips
    d['risk_distance_pips'] = (d.entry-d.stop).abs()/float(summary.get('config',{}).get('pip_size',0.0001))
    d['commission_as_pct_of_abs_pnl'] = np.where(d.pnl_usd.abs()>0,100*d.commission_usd/d.pnl_usd.abs(),np.nan)
    d['atr_bucket'] = 'unavailable'
    for strat, g in d.groupby('strategy'):
        if g.atr_pips.nunique()>1:
            d.loc[g.index,'atr_bucket'] = pd.qcut(g.atr_pips.rank(method='first'),4,labels=['Q1 low ATR','Q2','Q3','Q4 high ATR']).astype(str).to_numpy()
    flags=[]
    def flag(mask, name, severity, explanation):
        ix=d.index[mask.fillna(False)]
        for i in ix:
            r=d.loc[i]
            flags.append({'row_index':int(i)+2,'strategy':str(r.strategy),'entry_time_utc':r.entry_time_utc.isoformat(),
                          'issue':name,'severity':severity,'detail':explanation})
    flag(d.exit_time_utc<d.entry_time_utc,'negative_holding_period','high','Exit earlier than entry')
    flag(d.exit_time_utc==d.entry_time_utc,'same_bar_entry_exit','review','Same timestamp: confirm next-bar fill/intrabar order assumptions')
    flag(d.entry_time_utc<d.signal_time_utc,'entry_before_signal','high','Entry precedes signal timestamp')
    flag(d.lots<=0,'invalid_volume','high','Volume not positive')
    flag(d.risk_distance_pips<=0,'invalid_stop_distance','high','Entry and stop coincide')
    flag((d.direction=='BUY')&(d.stop>=d.entry) | (d.direction=='SELL')&(d.stop<=d.entry),'wrong_stop_side','high','Stop on wrong side of entry')
    flag((d.direction=='BUY')&(d.target<=d.entry) | (d.direction=='SELL')&(d.target>=d.entry),'wrong_target_side','high','Target on wrong side of entry')
    flag((d.entry_spread_pips<0)|(d.exit_spread_pips<0),'negative_spread','high','Negative recorded spread')
    # Overlaps within each separately simulated strategy.
    for strategy,g in d.sort_values(['strategy','entry_time_utc','exit_time_utc']).groupby('strategy'):
        max_exit=None
        for idx,r in g.iterrows():
            if max_exit is not None and r.entry_time_utc < max_exit:
                flags.append({'row_index':int(idx)+2,'strategy':str(strategy),'entry_time_utc':r.entry_time_utc.isoformat(),
                              'issue':'overlapping_positions','severity':'review','detail':'Overlaps previous position within strategy; verify one-at-a-time assumption'})
            max_exit = r.exit_time_utc if max_exit is None else max(max_exit,r.exit_time_utc)
    checks=[]
    for strategy,g in d.groupby('strategy'):
        g=g.sort_values('exit_time_utc',kind='stable')
        initial=float(summary.get('config',{}).get('initial_equity',10000))
        net=float(g.pnl_usd.sum()); reported=summary.get('stats',{}).get(strategy,{})
        eq_expected=initial+g.pnl_usd.cumsum()
        residual=float((eq_expected-g.equity_after).abs().max())
        checks.append({'strategy':strategy,'trade_count_csv':len(g),'trade_count_summary':reported.get('trades'),
          'net_pnl_csv':round(net,2),'net_pnl_summary':reported.get('net_profit_usd'),
          'trade_pnl_discrepancy':round(net-float(reported.get('net_profit_usd',net)),2),
          'max_equity_sequence_difference_usd':round(residual,2),
          'commission_total_usd':round(float(g.commission_usd.sum()),2),
          'entry_spread_mean_pips':round(float(g.entry_spread_pips.mean()),3),
          'exit_spread_mean_pips':round(float(g.exit_spread_pips.mean()),3),
          'median_holding_minutes':round(float(g.holding_minutes.median()),1)})
    grouped = {}
    for name, cols in [('direction','direction'),('exit_reason','exit_reason'),('utc_hour','entry_hour_utc'),
                       ('utc_session_proxy','session_utc_proxy'),('weekday','entry_weekday_utc'),
                       ('month','entry_month_utc'),('atr_quartile','atr_bucket')]:
        grouped[name] = stable_group(d,['strategy',cols])
    funnel=[]
    for strategy, vals in summary.get('signal_funnel',{}).items():
        n=vals.get('candidate_signals'); c=vals.get('confirmed_signals')
        funnel.append({'strategy':strategy,'candidates':n,'confirmed':c,
                       'rejected':n-c if isinstance(n,int) and isinstance(c,int) else None,
                       'confirmation_pct':round(100*c/n,2) if n else None})
    report={'metadata':{'created_utc':pd.Timestamp.now(tz='UTC').isoformat(), 'source_trades':len(d),
                        'strategies':sorted(d.strategy.unique().tolist())},
            'source_backtest_summary':summary.get('stats',{}),'source_backtest_warnings':summary.get('warnings',[]),
            'reconciliation':checks,'signal_funnel':funnel,'grouped':grouped,
            'issues':flags,'issue_counts':pd.Series([f['issue'] for f in flags]).value_counts().to_dict(),
            'limitations':[
              'UTC hours are based on recorded trade timestamps; historical timezone/DST correction is unverified.',
              'ATR quartiles are entry-time volatility proxies, not independently classified trend or volatility regimes.',
              'Only executed trade records supplied: cannot measure rejection reasons or outcomes of rejected candidate signals.',
              'Available trade-level spreads and commissions are observable modeled costs; no tick-level fills or counterfactual zero-cost trades.',
              'Historical price path within M5 candles is unknown; same-bar stop/target sequencing cannot be independently verified.',
              'This analysis is descriptive, in-sample and not a strategy optimization or forecast.'
            ]}
    return d,report

def main():
    p=argparse.ArgumentParser();p.add_argument('--trades',default='backtest_results/trades.csv');p.add_argument('--summary',default='backtest_results/summary.json');p.add_argument('--output',default='step9_results')
    args=p.parse_args();df,summary=load_inputs(args.trades,args.summary);enriched,report=analyze(df,summary)
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    enriched.to_csv(output/'enriched_trades.csv',index=False)
    (output/'diagnostic_report.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    pd.DataFrame(report['issues'],columns=['row_index','strategy','entry_time_utc','issue','severity','detail']).to_csv(output/'execution_flags.csv',index=False)
    print(f"Analyzed {len(enriched)} trades -> {output.resolve()}")
    for s in report['reconciliation']:print(s)
    print('Issue counts:',report['issue_counts'])
    print('No MT5 connection, API calls, or trade execution.')

if __name__=='__main__':main()
