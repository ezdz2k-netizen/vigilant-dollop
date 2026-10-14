"""Read-only GOLD flexible/opposite-BB walk-forward research.
Uses the existing GOLD reversal signal and execution functions. Never sends orders.
Time labels are broker-clock epoch labels, NOT verified historical UTC.
"""
import argparse
import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
import gold_bb_reversal as base

FEATURES = ['adx14', 'wick_ratio', 'depth_atr', 'atr_pct', 'session_hour']


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbol', default='GOLD')
    p.add_argument('--months', type=int, default=12)
    p.add_argument('--risk', type=float, default=.02)
    p.add_argument('--starting-equity', type=float, default=10000)
    p.add_argument('--slippage-price', type=float, default=.10)
    p.add_argument('--fallback-spread-price', type=float, default=.30)
    p.add_argument('--commission-lot-roundtrip', type=float, default=7)
    p.add_argument('--atr-stop-buffer', type=float, default=.25)
    p.add_argument('--max-hold-hours', type=int, default=48)
    p.add_argument('--window-hours', type=int, default=12)
    p.add_argument('--m15-reentry-bars', type=int, default=4)
    p.add_argument('--train-months', type=int, default=6)
    p.add_argument('--test-months', type=int, default=2)
    p.add_argument('--final-holdout-months', type=int, default=2)
    p.add_argument('--min-train-trades', type=int, default=12)
    p.add_argument('--output', default='gold_walkforward_results')
    p.add_argument('--max-grid', type=int, default=0, help='Limit grid for smoke testing only; 0 tests all')
    a=p.parse_args()
    if not (0 < a.risk <= .05 and a.months >= a.train_months+a.test_months+a.final_holdout_months and a.train_months>0 and a.test_months>0 and a.final_holdout_months>0 and a.min_train_trades>=1 and a.starting_equity>0 and a.slippage_price>=0 and a.fallback_spread_price>=0 and a.atr_stop_buffer>=0 and a.max_hold_hours>0):
        p.error('Invalid period, cost or risk settings')
    return a


def features_for_m15(m15):
    f=m15.copy()
    up=f.high.diff(); down=-f.low.diff()
    plus=pd.Series(np.where((up>down)&(up>0),up,0.),index=f.index)
    minus=pd.Series(np.where((down>up)&(down>0),down,0.),index=f.index)
    prev=f.close.shift(1)
    tr=pd.concat([f.high-f.low,(f.high-prev).abs(),(f.low-prev).abs()],axis=1).max(axis=1)
    alpha=1/14
    atr=tr.ewm(alpha=alpha,adjust=False,min_periods=14).mean()
    pdi=100*plus.ewm(alpha=alpha,adjust=False,min_periods=14).mean()/atr.replace(0,np.nan)
    mdi=100*minus.ewm(alpha=alpha,adjust=False,min_periods=14).mean()/atr.replace(0,np.nan)
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    f['adx14']=dx.ewm(alpha=alpha,adjust=False,min_periods=14).mean()
    f['atr_pct']=100*f.atr/f.close.replace(0,np.nan)
    return f


def attach_features(signals, m15):
    # Select only known, completed M15 re-entry candle. No future rows.
    by_close=m15.set_index('close_time',drop=False)
    enriched=[]
    for s in signals:
        if s['time'] not in by_close.index:
            continue
        r=by_close.loc[s['time']]
        if isinstance(r,pd.DataFrame):
            raise RuntimeError('Duplicate M15 close timestamps')
        direction=s['side']
        rng=float(r.high-r.low)
        wick=(float(min(r.open,r.close)-r.low) if direction=='BUY' else float(r.high-max(r.open,r.close)))
        sweep_at= s['m15_event']
        # Breakout depth measured at the actual completed sweep candle,
        # compared with the PRIOR fully known band (not current/future band).
        sweep_df=by_close.loc[sweep_at] if sweep_at in by_close.index else None
        if sweep_df is None or isinstance(sweep_df,pd.DataFrame):
            continue
        breach=(float(sweep_df.lower_before-sweep_df.low) if direction=='BUY' else float(sweep_df.high-sweep_df.upper_before))
        depth=max(0.,breach)/float(sweep_df.atr) if float(sweep_df.atr)>0 else np.nan
        d=dict(s)
        d.update(adx14=float(r.adx14),wick_ratio=wick/rng if rng>0 else np.nan,
                 depth_atr=depth,atr_pct=float(r.atr_pct),session_hour=int(r.name.hour),
                 signal_broker_clock=str(s['time']))
        if all(np.isfinite(d[k]) for k in ('adx14','wick_ratio','depth_atr','atr_pct')):
            enriched.append(d)
    return enriched


def grid():
    # 3 x 2 x 2 x 2 x 3 = 72 alternatives, including all-hours baseline.
    for regime, wick, depth, volatility, session in itertools.product(
        ('any','adx_under_25','adx_under_35'),(0.,.3),(0.,.10),('any','middle_80pct'),('all','broker_06_14','broker_14_22')):
        yield dict(adx_regime=regime,min_wick=wick,min_depth_atr=depth,
                   atr_regime=volatility,session=session)


def matches(s, p, vol_limits):
    a=s['adx14']
    if p['adx_regime']=='adx_under_25' and a>=25: return False
    if p['adx_regime']=='adx_under_35' and a>=35: return False
    if s['wick_ratio']<p['min_wick']: return False
    if s['depth_atr']<p['min_depth_atr']: return False
    if p['atr_regime']=='middle_80pct' and not (vol_limits[0]<=s['atr_pct']<=vol_limits[1]): return False
    h=s['session_hour']
    if p['session']=='broker_06_14' and not (6<=h<14): return False
    if p['session']=='broker_14_22' and not (14<=h<22): return False
    return True


def stats(tx):
    result=base.summarize(tx,10000.)
    # Normalize to initial equity in caller; rank with mean per-trade R, lower DD and minimum sample requirement.
    return result


def score(tx, minimum):
    if len(tx)<minimum: return -1e6
    pnls=np.array([float(t['net_pnl']) for t in tx])
    gp=pnls[pnls>0].sum(); gl=-pnls[pnls<0].sum()
    pf=gp/gl if gl>0 else (5. if gp>0 else 0.)
    mean_r=np.mean([float(t['r_multiple']) for t in tx])
    # Penalize tiny samples; avoid an unstable profit-factor-only optimum.
    return float(mean_r + .05*min(pf,5) - .10/np.sqrt(len(tx)))


def eval_trades(m5, sigs, info, args, start, end):
    # Signal filtering precedes simulation. Warm-up must not create positions
    # before the evaluation window. Exit may extend beyond end in a fold;
    # avoid that by excluding the final max-hold interval for entry signals.
    eligible=[s for s in sigs if start<=s['time']<end-pd.Timedelta(hours=args.max_hold_hours)]
    if not eligible: return []
    tx=base.simulate(m5,eligible,info,args,'opposite',end)
    return [t for t in tx if start<=pd.Timestamp(t['signal_broker_clock'])<end]


def summarize_window(tx,starting):
    return base.summarize(tx,starting)


def main():
    a=parse_args(); out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    if not mt5.initialize(): raise RuntimeError(f'MT5 unavailable: {mt5.last_error()}')
    try:
        if not mt5.symbol_select(a.symbol,True):raise RuntimeError(f'Symbol unavailable {a.symbol}')
        info=mt5.symbol_info(a.symbol)
        if info is None: raise RuntimeError('Missing broker symbol specifications')
        end=datetime.now(timezone.utc).replace(second=0,microsecond=0)
        begin=end-pd.DateOffset(months=a.months)
        warmup=begin-timedelta(days=55)
        print('Downloading GOLD M5 history in 7-day chunks. READ ONLY.')
        m5=base.fetch_5min(a.symbol,warmup,end+timedelta(hours=6))
    finally: mt5.shutdown()
    m15=features_for_m15(base.aggregate(m5,900));h1=base.aggregate(m5,3600);h4=base.aggregate(m5,14400)
    signals=attach_features(base.select_signals(m15,h1,h4,a)['flexible'],m15)
    begin=pd.Timestamp(begin); end=pd.Timestamp(end)
    holdout_start=end-pd.DateOffset(months=a.final_holdout_months)
    dev_end=holdout_start
    # Rolling training/test folds strictly before untouched holdout.
    folds=[]; cursor=begin
    while cursor+pd.DateOffset(months=a.train_months+a.test_months)<=dev_end:
        train_end=cursor+pd.DateOffset(months=a.train_months)
        test_end=train_end+pd.DateOffset(months=a.test_months)
        folds.append((cursor,train_end,test_end))
        cursor+=pd.DateOffset(months=a.test_months)
    if not folds:raise RuntimeError('No walk-forward folds possible')
    params=list(grid()); params=params[:a.max_grid] if a.max_grid>0 else params
    # Volatility limits are computed ONLY from each training interval.
    selections=[];oos_trades=[];fold_grid=[]
    for k,(train_start,train_end,test_end) in enumerate(folds,1):
        sample=np.array([s['atr_pct'] for s in signals if train_start<=s['time']<train_end])
        if len(sample)<20:raise RuntimeError(f'Fold {k}: too few training signals')
        bounds=np.quantile(sample,[.1,.9]).tolist()
        candidates=[]
        print(f'Fold {k}/{len(folds)}: training {train_start.date()} to {train_end.date()}')
        for idx,p in enumerate(params):
            chosen=[s for s in signals if matches(s,p,bounds)]
            training=eval_trades(m5,chosen,info,a,train_start,train_end)
            sc=score(training,a.min_train_trades)
            candidates.append((sc,idx,p,len(training)))
            fold_grid.append({'fold':k,'params_id':idx,'train_score':sc,'train_trades':len(training),**p})
        valid=[v for v in candidates if v[0]>-1e5]
        if not valid:
            selections.append({'fold':k,'status':'INSUFFICIENT_TRAIN_TRADES','train_start':str(train_start),'test_start':str(train_end),'test_end':str(test_end)})
            continue
        best=max(valid,key=lambda x:(x[0],-x[1]))
        selected=[s for s in signals if matches(s,best[2],bounds)]
        test=eval_trades(m5,selected,info,a,train_end,test_end)
        for t in test:t.update(fold=k,selection='rolling_oos')
        oos_trades.extend(test)
        selections.append({'fold':k,'status':'OK','train_start':str(train_start),'test_start':str(train_end),'test_end':str(test_end),'params_id':best[1], 'train_score':best[0],'train_trades':best[3],'test_stats':summarize_window(test,a.starting_equity),**best[2]})
    # Final selection uses the entire development period only, never final holdout.
    sample=np.array([s['atr_pct'] for s in signals if begin<=s['time']<dev_end])
    if len(sample)<20:raise RuntimeError('Too few development signals')
    bounds=np.quantile(sample,[.1,.9]).tolist()
    finalist=[]
    for idx,p in enumerate(params):
        chosen=[s for s in signals if matches(s,p,bounds)]
        dev_trades=eval_trades(m5,chosen,info,a,begin,dev_end)
        finalist.append((score(dev_trades,a.min_train_trades),idx,p,len(dev_trades)))
    finalists=[x for x in finalist if x[0]>-1e5]
    if not finalists:raise RuntimeError('No candidate met minimum development trade count')
    winner=max(finalists,key=lambda x:(x[0],-x[1]))
    hold_sigs=[s for s in signals if matches(s,winner[2],bounds)]
    final_tx=eval_trades(m5,hold_sigs,info,a,holdout_start,end)
    base_tx=eval_trades(m5,signals,info,a,holdout_start,end)
    for t in final_tx:t.update(fold='final',selection='selected_holdout')
    for t in base_tx:t.update(fold='final',selection='unfiltered_holdout')
    pd.DataFrame(selections).to_csv(out/'fold_selections.csv',index=False)
    pd.DataFrame(fold_grid).to_csv(out/'training_grid.csv',index=False)
    pd.DataFrame(oos_trades).to_csv(out/'rolling_oos_trades.csv',index=False)
    pd.DataFrame(final_tx).to_csv(out/'selected_holdout_trades.csv',index=False)
    pd.DataFrame(base_tx).to_csv(out/'unfiltered_holdout_trades.csv',index=False)
    result={'symbol':a.symbol,'generated_at':datetime.now(timezone.utc).isoformat(), 'config':vars(a),
        'time_basis':'RAW BROKER CLOCK LABELS; historical UTC/DST not verified',
        'data':{'m5_bars':len(m5),'flexible_signals':len(signals),'first':str(m5.index.min()),'last':str(m5.index.max())},
        'folds':selections, 'rolling_oos':summarize_window(oos_trades,a.starting_equity),
        'final_selection':{'params_id':winner[1],'score':winner[0], 'development_trades':winner[3], 'parameters':winner[2], 'training_atr_pct_10_90':bounds},
        'final_holdout':{'start':str(holdout_start),'end':str(end),'selected':summarize_window(final_tx,a.starting_equity),'unfiltered':summarize_window(base_tx,a.starting_equity)},
        'warnings':['Broker-clock hours are NOT confirmed UTC session hours; DST historical changes are not resolved.',
                    'M5 OHLC cannot determine true intrabar execution; original stop-first tie break retained.',
                    'Slippage, spread, and commission assumptions inherited from original reversal project.',
                    'Fold-wise test results are independent simulated equity paths; aggregate OOS summary concatenates trade PnL, not continuous compounded equity.',
                    'A final holdout is tested once; selecting settings based on its result invalidates its untouched status.',
                    'No trades sent; no AI model performance included.']}
    (out/'optimization_report.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    print('\nFINAL HOLDOUT',json.dumps(result['final_holdout'],indent=2))
    print('Reports saved:',out.resolve(),'\nNO TRADES EXECUTED')

if __name__=='__main__':main()
