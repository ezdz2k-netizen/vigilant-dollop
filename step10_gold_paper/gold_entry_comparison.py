"""GOLD flexible triple-BB reversal: four entry styles, read-only MT5 research.
Uses gold_bb_reversal.py from the previous GOLD project. No trading API calls.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
import gold_bb_reversal as base

STYLES = ('reentry', 'confirmation', 'structure', 'momentum')

def arguments():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbol',default='GOLD')
    p.add_argument('--months',type=int,default=12)
    p.add_argument('--risk',type=float,default=.02)
    p.add_argument('--starting-equity',type=float,default=10000)
    p.add_argument('--train-months',type=int,default=6)
    p.add_argument('--test-months',type=int,default=2)
    p.add_argument('--holdout-months',type=int,default=2)
    p.add_argument('--confirmation-bars',type=int,default=3)
    p.add_argument('--structure-lookback',type=int,default=3)
    p.add_argument('--momentum-body-min',type=float,default=.55)
    p.add_argument('--momentum-volume-mult',type=float,default=1.0)
    p.add_argument('--atr-stop-buffer',type=float,default=.25)
    p.add_argument('--slippage-price',type=float,default=.10)
    p.add_argument('--fallback-spread-price',type=float,default=.30)
    p.add_argument('--commission-lot-roundtrip',type=float,default=7)
    p.add_argument('--max-hold-hours',type=int,default=48)
    p.add_argument('--window-hours',type=int,default=12)
    p.add_argument('--m15-reentry-bars',type=int,default=4)
    p.add_argument('--output',default='gold_entry_results')
    a=p.parse_args()
    if not (0<a.risk<=.05 and a.months>=a.train_months+a.test_months+a.holdout_months and a.train_months>0 and a.test_months>0 and a.holdout_months>0 and 1<=a.confirmation_bars<=12 and 1<=a.structure_lookback<=12 and 0<=a.momentum_body_min<=1 and a.momentum_volume_mult>=0 and a.starting_equity>0 and a.max_hold_hours>0):
        p.error('Invalid settings')
    return a

def annotate(m15):
    f=m15.copy()
    f['vavg20']=f.tick_volume.rolling(20,min_periods=20).mean().shift(1)
    f['ema20']=f.close.ewm(span=20,adjust=False,min_periods=20).mean()
    # Momentum uses only completed candle and earlier values.
    f['ema20_prior']=f.ema20.shift(1)
    return f

def shifted_signal(original, style, candles, j, setup_j, reason):
    row=candles.iloc[j]
    d=dict(original)
    d['time']=row.close_time
    # Update targets to known bands at confirmed entry, not future bands.
    d['middle']=float(row.middle)
    d['opposite']=float(row.upper if d['side']=='BUY' else row.lower)
    d['entry_style']=style
    d['original_reentry_time']=str(original['time'])
    d['confirmation_delay_bars']=int(j-setup_j)
    d['confirmation_reason']=reason
    return d

def make_entries(raw_signals, m15, args):
    candles=annotate(m15)
    index={pd.Timestamp(t):i for i,t in enumerate(candles.close_time)}
    results={k:[] for k in STYLES}
    for sig in sorted(raw_signals,key=lambda s:s['time']):
        j=index.get(pd.Timestamp(sig['time']))
        if j is None or j<max(args.structure_lookback,20): continue
        side=sig['side']; buy=side=='BUY'
        results['reentry'].append(shifted_signal(sig,'reentry',candles,j,j,'close back inside band'))
        # The first eligible confirmation must occur after the completed reentry bar.
        for k in range(j+1,min(j+args.confirmation_bars+1,len(candles))):
            cur=candles.iloc[k]; prev=candles.iloc[k-1]
            # Do not bridge gaps or weekends as if adjacent candles existed.
            if cur.name-prev.name != pd.Timedelta(minutes=15): break
            if not (cur.lower < cur.close < cur.upper): continue
            if buy and cur.low <= sig['sweep_low']: continue
            if not buy and cur.high >= sig['sweep_high']: continue
            bull = (cur.close>cur.open and cur.close>prev.close)
            bear = (cur.close<cur.open and cur.close<prev.close)
            if (bull if buy else bear):
                results['confirmation'].append(shifted_signal(sig,'confirmation',candles,k,j,'directional confirmation candle'))
                break
        # Structure: after reversal bar, close crosses highest high/lowest low
        # of PRIOR N completed candles (including reversal candle). No next-bar leakage.
        for k in range(j+1,min(j+args.confirmation_bars+1,len(candles))):
            cur=candles.iloc[k]
            if cur.name-candles.iloc[k-1].name != pd.Timedelta(minutes=15): break
            prior=candles.iloc[k-args.structure_lookback:k]
            if len(prior)!=args.structure_lookback or prior.index.to_series().diff().iloc[1:].ne(pd.Timedelta(minutes=15)).any(): continue
            passed=(cur.close>prior.high.max()) if buy else (cur.close<prior.low.min())
            if passed:
                results['structure'].append(shifted_signal(sig,'structure',candles,k,j,'close breaks prior completed candle structure'))
                break
        # Momentum: next eligible M15 candle has strong directional body,
        # increasing EMA20 and volume compared with pre-candle rolling average.
        for k in range(j+1,min(j+args.confirmation_bars+1,len(candles))):
            cur=candles.iloc[k]
            if cur.name-candles.iloc[k-1].name != pd.Timedelta(minutes=15): break
            rng=float(cur.high-cur.low)
            body=abs(float(cur.close-cur.open))/rng if rng>0 else 0
            directional=(cur.close>cur.open and cur.close>cur.ema20 and cur.ema20>cur.ema20_prior) if buy else (cur.close<cur.open and cur.close<cur.ema20 and cur.ema20<cur.ema20_prior)
            if directional and body>=args.momentum_body_min and pd.notna(cur.vavg20) and cur.tick_volume>=args.momentum_volume_mult*cur.vavg20:
                results['momentum'].append(shifted_signal(sig,'momentum',candles,k,j,'strong body, EMA20 direction and volume'))
                break
    # Avoid duplicates from overlapping setup windows: earliest known event first.
    for style in STYLES:
        unique={}
        for s in results[style]:
            key=(str(s['time']),s['side'])
            unique.setdefault(key,s)
        results[style]=sorted(unique.values(),key=lambda s:s['time'])
    return results

def window_trades(m5, signals, info, args, start, end):
    # Exclude entries too close to window end to enforce complete trade exits.
    latest=end-pd.Timedelta(hours=args.max_hold_hours)
    chosen=[s for s in signals if start<=s['time']<latest]
    if not chosen:return []
    # Avoid lookahead across split, and do not allow any entry before confirmation.
    tx=base.simulate(m5,chosen,info,args,'opposite',end)
    return [t for t in tx if start<=pd.Timestamp(t['signal_broker_clock'])<latest]

def summarize(trades, start_eq):
    return base.summarize(trades,start_eq)

def score(trades):
    if len(trades)<12:return -1000000.
    r=np.asarray([x['r_multiple'] for x in trades],dtype=float)
    # Predeclared score; no final-holdout tuning.
    return float(np.mean(r)-.10/np.sqrt(len(r)))

def main():
    a=arguments(); out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    if not mt5.initialize():raise RuntimeError(f'MT5 connection: {mt5.last_error()}')
    try:
        if not mt5.symbol_select(a.symbol,True):raise RuntimeError(f'Symbol {a.symbol} not found: {mt5.last_error()}')
        info=mt5.symbol_info(a.symbol)
        if info is None:raise RuntimeError('No GOLD specifications returned')
        end=datetime.now(timezone.utc).replace(second=0,microsecond=0)
        begin=end-pd.DateOffset(months=a.months)
        warmup=begin-timedelta(days=65)
        print('Downloading M5 broker history in 7-day chunks. READ ONLY.')
        m5=base.fetch_5min(a.symbol,warmup,end+timedelta(hours=6))
    finally:mt5.shutdown()
    m15=base.aggregate(m5,900);h1=base.aggregate(m5,3600);h4=base.aggregate(m5,14400)
    raw=base.select_signals(m15,h1,h4,a)['flexible']
    entries=make_entries(raw,m15,a)
    begin=pd.Timestamp(begin);end=pd.Timestamp(end)
    hold_start=end-pd.DateOffset(months=a.holdout_months)
    dev_end=hold_start
    folds=[];cursor=begin
    while cursor+pd.DateOffset(months=a.train_months+a.test_months)<=dev_end:
        tr_end=cursor+pd.DateOffset(months=a.train_months)
        te_end=tr_end+pd.DateOffset(months=a.test_months)
        folds.append((cursor,tr_end,te_end))
        cursor+=pd.DateOffset(months=a.test_months)
    if not folds:raise RuntimeError('No walk-forward folds with requested periods')
    rows=[];oos=[]
    for fold,(tr_start,tr_end,te_end) in enumerate(folds,1):
        values=[]
        for style in STYLES:
            training=window_trades(m5,entries[style],info,a,tr_start,tr_end)
            testing=window_trades(m5,entries[style],info,a,tr_end,te_end)
            values.append((style,score(training)))
            rows.append({'fold':fold,'style':style,'train_start':str(tr_start),'train_end':str(tr_end),'test_end':str(te_end),'train_score':score(training),'train_trades':len(training),'test_trades':len(testing),'train_return_pct':summarize(training,a.starting_equity)['return_pct'],'test_return_pct':summarize(testing,a.starting_equity)['return_pct'],'test_profit_factor':summarize(testing,a.starting_equity)['profit_factor']})
        best=max(values,key=lambda x:(x[1],-STYLES.index(x[0])))
        if best[1]<=-1e5:
            print('Fold',fold,'insufficient training trades; no selection');continue
        tests=window_trades(m5,entries[best[0]],info,a,tr_end,te_end)
        for t in tests:t.update(walkforward_fold=fold,selection='rolling_oos',entry_style=best[0])
        oos.extend(tests)
        print(f'Fold {fold}: selected {best[0]}, {len(tests)} OOS trades')
    # Final pre-committed selection uses *development period only*.
    all_dev={style:window_trades(m5,entries[style],info,a,begin,dev_end) for style in STYLES}
    ranked=sorted(STYLES,key=lambda s:(-score(all_dev[s]),STYLES.index(s)))
    winner=ranked[0] if score(all_dev[ranked[0]])>-1e5 else None
    hold_rows=[];hold_trades=[]
    for style in STYLES:
        tx=window_trades(m5,entries[style],info,a,hold_start,end)
        for t in tx:t.update(entry_style=style)
        hold_trades.extend(tx)
        hold_rows.append({'style':style,**summarize(tx,a.starting_equity),'is_selected':style==winner})
    pd.DataFrame(rows).to_csv(out/'fold_comparison.csv',index=False)
    pd.DataFrame(hold_rows).to_csv(out/'holdout_comparison.csv',index=False)
    pd.DataFrame(hold_trades).to_csv(out/'holdout_trades.csv',index=False)
    pd.DataFrame(oos).to_csv(out/'rolling_oos_trades.csv',index=False)
    report={
        'generated_at':datetime.now(timezone.utc).isoformat(), 'symbol':a.symbol,
        'config':vars(a),'time_basis':'RAW MT5 BROKER EPOCH LABELS, historical UTC/DST not verified',
        'observed_bars':{'m5':len(m5),'m15':len(m15),'h1':len(h1),'h4':len(h4)},
        'entry_candidate_counts':{s:len(entries[s]) for s in STYLES},
        'folds':len(folds),'selected_development_style':winner,
        'development_scores':{s:score(all_dev[s]) for s in STYLES},
        'rolling_oos_selected':summarize(oos,a.starting_equity),
        'holdout_start':str(hold_start),'holdout_end':str(end),
        'final_holdout':hold_rows,
        'warnings':[
            'Holdout strategy comparison is descriptive. Do not choose a new winner based on holdout results.',
            'All indicator values are available only at completed candle closes; next M5 bar entry is simulated.',
            'H4/H1 sweep events are confirmed only when the corresponding higher-timeframe bar closes.',
            'Market regimes/costs use broker-clock labels, historical DST conversion is not verified.',
            'M5 bid OHLC + spread approximates ask; stop-first on same-bar stop/target; slippage and commission are assumptions.',
            'Momentum EMA and volume confirmation are definitions chosen in advance; not calibrated on holdout.',
            'Rolling OOS aggregate is a descriptive sum of separately sized fold trades, not a compounded continuous equity curve.',
            'No margin calls, swap, funding or exact tick execution simulated. No trades placed.'
        ]}
    (out/'entry_report.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print('Selected on development:',winner)
    print(pd.DataFrame(hold_rows).to_string(index=False))
    print('Files saved to',out.resolve(),'— NO TRADES EXECUTED')

if __name__=='__main__':main()
