"""GOLD M15 BB excursion outcome research, historical only; NEVER places orders.

Consumes Step 7/V5 `candidate_features.csv` and actual MT5 M5 bars.
Outcomes: Which comes first, fixed M15 middle/opposite band or ATR stop,
from the NEXT M5 bid open, with modeled spread and slippage.
Broker-clock timestamps are labels, NOT verified historical UTC.
"""
import argparse
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HORIZONS = (4, 8, 16, 32)  # M15 bars
MODES = ('H1', 'H4', 'BOTH')
TARGETS = ('middle', 'opposite')
FEATURES = ['depth', 'adx', 'volume_ratio', 'atr', 'rsi_div', 'adx_fade', 'choch', 'liquidity_sweep', 'side']


def options():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidates', default='gold_exhaustion_research/gold_exhaustion_results/candidate_features.csv')
    p.add_argument('--m5-csv', default='', help='Optional previously exported GOLD M5 CSV, with MT5 raw epoch `time` column')
    p.add_argument('--symbol', default='GOLD')
    p.add_argument('--months', type=int, default=12)
    p.add_argument('--output', default='gold_reversal_probability/results')
    p.add_argument('--min-depth', type=float, default=0.0)
    p.add_argument('--stop-atr', type=float, default=0.25)
    p.add_argument('--fallback-spread', type=float, default=0.30, help='Gold price units when historical spread missing')
    p.add_argument('--point', type=float, default=0.01, help='Gold price units per MT5 point; overridden by live symbol info')
    p.add_argument('--slippage', type=float, default=0.10, help='Gold price units per fill')
    p.add_argument('--calibration-fraction', type=float, default=0.20)
    p.add_argument('--holdout-fraction', type=float, default=0.20)
    p.add_argument('--min-model-events', type=int, default=70)
    a = p.parse_args()
    if not (0<=a.min_depth<=10 and 0<a.months<=36 and a.stop_atr>=0 and a.fallback_spread>=0 and a.slippage>=0 and a.point>0 and 0<a.calibration_fraction<.4 and 0<a.holdout_fraction<.4):
        p.error('Invalid research parameters')
    return a


def load_m5(a):
    if a.m5_csv:
        d = pd.read_csv(a.m5_csv)
    else:
        import MetaTrader5 as mt5
        from datetime import timedelta
        if not mt5.initialize():
            raise RuntimeError(f'MT5 initialize failed: {mt5.last_error()}')
        try:
            if not mt5.symbol_select(a.symbol, True):
                raise RuntimeError(f'MT5 symbol missing: {a.symbol}')
            info=mt5.symbol_info(a.symbol)
            if info and info.point > 0: a.point=float(info.point)
            end=datetime.now(timezone.utc)
            # Source candidate file may include a 100-day warmup, hence extra buffer.
            start=end-timedelta(days=round(a.months*365.25/12)+110)
            cur=start; frames=[]
            while cur<end:
                nxt=min(cur+timedelta(days=7),end)
                bars=mt5.copy_rates_range(a.symbol,mt5.TIMEFRAME_M5,cur,nxt)
                if bars is None: raise RuntimeError(f'M5 request failed {cur}: {mt5.last_error()}')
                if len(bars): frames.append(pd.DataFrame(bars))
                cur=nxt
            if not frames: raise RuntimeError('No GOLD M5 history downloaded')
            d=pd.concat(frames,ignore_index=True)
        finally:
            mt5.shutdown()
    for col in ('time','open','high','low','close'):
        if col not in d: raise ValueError(f'M5 is missing {col}')
    d['time']=pd.to_numeric(d['time'],errors='raise').astype('int64')
    d=d.drop_duplicates('time').sort_values('time').reset_index(drop=True)
    if (d.time%300!=0).any(): raise ValueError('Raw broker M5 epochs not aligned to 300 seconds')
    if 'spread' not in d: d['spread']=0
    d['spread']=pd.to_numeric(d.spread, errors='coerce').fillna(0)
    d['time_dt']=pd.to_datetime(d.time,unit='s',utc=True)
    if (d[['open','high','low','close']]<=0).any().any(): raise ValueError('Invalid prices in M5 history')
    return d


def load_candidates(path, min_depth):
    d=pd.read_csv(path)
    needed={'time','side','close','high','low','atr','depth','outside','mid','target','h1_out','h4_out','adx','volume_ratio'}
    missing=needed-set(d.columns)
    if missing: raise ValueError(f'candidate_features missing: {sorted(missing)}')
    d['time']=pd.to_datetime(d.time,utc=True,format='mixed',errors='raise')
    d=d.loc[d.outside.astype(str).str.lower().eq('true') & (d.depth>=min_depth)].copy()
    for field in ('h1_out','h4_out'):
        d[field]=d[field].astype(str).str.lower().eq('true')
    for field in ('rsi_div','adx_fade','choch','liquidity_sweep'):
        d[field]=d[field].astype(str).str.lower().eq('true') if field in d else False
    d=d.loc[d.side.isin(['BUY','SELL']) & (d.atr>0)].sort_values(['time','side'])
    return d


def mode_matches(row):
    valid=[]
    if row.h1_out: valid.append('H1')
    if row.h4_out: valid.append('H4')
    if row.h1_out and row.h4_out: valid.append('BOTH')
    return valid


def evaluate_path(row, ix, m5, horizon_bars, stop_atr, fallback_spread, point, slippage, target_type):
    """Labels M15-close decisions from next M5 open; fixed target/stop from snapshot.
    Stops win if both levels touch in same M5 bar. Beyond last data: unlabelled.
    No weekend bridging entries: first M5 open must equal decision boundary.
    """
    if ix>=len(m5) or m5.time_dt.iloc[ix] != row.time:
        return None
    n=3*horizon_bars
    if ix+n>len(m5):return None
    path=m5.iloc[ix:ix+n]
    if (path.time_dt.iloc[-1]+pd.Timedelta(minutes=5) != row.time+pd.Timedelta(minutes=15*horizon_bars)
            or not path.time_dt.diff().iloc[1:].dt.total_seconds().eq(300).all()):
        return None  # require an uninterrupted M5 path; exclude market closures/gaps
    buy=row.side=='BUY'
    first=path.iloc[0]
    spread=float(first.spread)*point if first.spread>0 else fallback_spread
    entry=float(first.open)+(spread if buy else 0)+(slippage if buy else -slippage)
    stop=float(row.low-stop_atr*row.atr) if buy else float(row.high+stop_atr*row.atr+spread)
    level=float(row.mid if target_type=='middle' else row.target)
    target=level if buy else level+spread
    if (buy and not (stop<entry<target)) or (not buy and not (target<entry<stop)):
        return None  # impossible target at entry; excluded from probability denominator
    max_fav=0.;max_adv=0.; reason='TIMEOUT'; elapsed=15*horizon_bars
    for j,bar in enumerate(path.itertuples(index=False)):
        curr_spread=max(0,float(bar.spread))*point if bar.spread>0 else fallback_spread
        high=float(bar.high) if buy else float(bar.high)+curr_spread
        low=float(bar.low) if buy else float(bar.low)+curr_spread
        max_fav=max(max_fav,(high-entry) if buy else (entry-low))
        max_adv=max(max_adv,(entry-low) if buy else (high-entry))
        stop_hit=low<=stop if buy else high>=stop
        tp_hit=high>=target if buy else low<=target
        if stop_hit or tp_hit:
            reason='STOP' if stop_hit else 'TARGET'
            elapsed=(j+1)*5
            break
    # Both time-to-target and MFE/MAE measured from bar OHLC, not tick path.
    return dict(outcome=reason, success=int(reason=='TARGET'), mins_to_event=elapsed,
                mfe_price=round(max_fav,5),mae_price=round(max_adv,5),
                mfe_atr=round(max_fav/row.atr,5),mae_atr=round(max_adv/row.atr,5),
                entry=round(entry,5),stop=round(stop,5),target_price=round(target,5))


def generate_labels(candidates,m5,a):
    idx=m5.time.to_numpy(dtype=np.int64)
    rows=[]
    for i,row in enumerate(candidates.itertuples(index=False)):
        modes=mode_matches(row)
        if not modes:continue
        epoch=int(row.time.timestamp())
        ix=int(np.searchsorted(idx,epoch,'left'))
        for target in TARGETS:
            for horizon in HORIZONS:
                value=evaluate_path(row,ix,m5,horizon,a.stop_atr,a.fallback_spread,a.point,a.slippage,target)
                if value is None:continue
                for mode in modes:
                    rows.append({'time':row.time,'side':row.side,'mode':mode,'target_type':target,'horizon_m15':horizon,
                                 'depth':row.depth,'atr':row.atr,'adx':row.adx,'volume_ratio':row.volume_ratio,
                                 'rsi_div':bool(row.rsi_div),'adx_fade':bool(row.adx_fade),'choch':bool(row.choch),
                                 'liquidity_sweep':bool(row.liquidity_sweep),**value})
        if i and i%1000==0:print(f'Labeled {i:,}/{len(candidates):,} candidate rows',flush=True)
    if not rows:raise RuntimeError('No labelable trades. Check M5 history and setup/target constraints.')
    return pd.DataFrame(rows).sort_values(['time','mode','target_type','horizon_m15']).reset_index(drop=True)


def split_periods(d, fraction_cal, fraction_test):
    unique=np.sort(d['time'].unique())
    if len(unique)<25:raise ValueError('Too few unique decision times for chronological split')
    cal=unique[int(len(unique)*(1-fraction_test-fraction_cal))]
    test=unique[int(len(unique)*(1-fraction_test))]
    embargo=pd.Timedelta(hours=8)
    return [d[d.time<cal-embargo].copy(),
            d[(d.time>=cal)&(d.time<test-embargo)].copy(),
            d[d.time>=test].copy()], (str(cal),str(test))


def calibration_report(train,cal,test,minimum):
    """Independent model per mode+target+horizon. No holdout model selection.
    Cross-validated? No: fixed 60/20/20 chronological train/cal/test, with 8h purge.
    """
    from sklearn.compose import ColumnTransformer
    from sklearn.preprocessing import OneHotEncoder,StandardScaler
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.linear_model import LogisticRegression
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import brier_score_loss,log_loss,roc_auc_score
    numeric=['depth','adx','volume_ratio','atr','rsi_div','adx_fade','choch','liquidity_sweep']
    cat=['side']
    results=[];pred=[]
    for mode in MODES:
        for target in TARGETS:
            for horizon in HORIZONS:
                sel=lambda d:d[(d['mode']==mode)&(d.target_type==target)&(d.horizon_m15==horizon)]
                a,b,c=(sel(d) for d in (train,cal,test))
                row={'mode':mode,'target_type':target,'horizon_m15':horizon,
                     'n_train':len(a),'n_calibration':len(b),'n_holdout':len(c),
                     'holdout_base_rate':round(float(c.success.mean()),5) if len(c) else None,
                     'brier_raw':None,'brier_calibrated':None,'brier_constant':None,'auc_calibrated':None,'status':'not_fitted'}
                if min(len(a),len(b),len(c))<minimum or min(a.success.nunique(),b.success.nunique())<2:
                    row['status']='insufficient_examples_or_classes';results.append(row);continue
                pre=ColumnTransformer([('num',Pipeline([('impute',SimpleImputer(strategy='median')),('scale',StandardScaler())]),numeric),
                                       ('side',OneHotEncoder(handle_unknown='ignore'),cat)])
                model=Pipeline([('pre',pre),('lr',LogisticRegression(max_iter=1000,class_weight=None))])
                model.fit(a[numeric+cat],a.success)
                prob_b=model.predict_proba(b[numeric+cat])[:,1]
                prob_c=model.predict_proba(c[numeric+cat])[:,1]
                if len(np.unique(prob_b))<5:
                    row['status']='insufficient_calibration_variation';results.append(row);continue
                iso=IsotonicRegression(y_min=0,y_max=1,out_of_bounds='clip')
                iso.fit(prob_b,b.success)
                calibrated=iso.predict(prob_c)
                baseline=float(a.success.mean())
                row.update({'brier_raw':round(float(brier_score_loss(c.success,prob_c)),5),
                            'brier_calibrated':round(float(brier_score_loss(c.success,calibrated)),5),
                            'brier_constant':round(float(brier_score_loss(c.success,np.full(len(c),baseline))),5),
                            'auc_calibrated':round(float(roc_auc_score(c.success,calibrated)),5) if c.success.nunique()>1 else None,
                            'status':'fitted'})
                predictions=c[['time','side','mode','target_type','horizon_m15','success','depth','outcome']].copy()
                predictions['prob_raw']=prob_c
                predictions['prob_calibrated']=calibrated
                predictions['train_base_rate']=baseline
                pred.append(predictions)
                results.append(row)
    return pd.DataFrame(results),pd.concat(pred,ignore_index=True) if pred else pd.DataFrame()


def bins_summary(holdout):
    t=holdout.copy()
    boundaries=[-np.inf,0.1,0.3,0.6,np.inf]
    names=['0–0.10','0.10–0.30','0.30–0.60','>0.60']
    t['depth_bucket']=pd.cut(t.depth,boundaries,labels=names,right=False)
    result=t.groupby(['mode','target_type','horizon_m15','depth_bucket'],observed=True).agg(
        count=('success','size'),successes=('success','sum'),hit_rate=('success','mean'),
        avg_mfe_atr=('mfe_atr','mean'),avg_mae_atr=('mae_atr','mean'),
        avg_mins_to_event=('mins_to_event','mean')).reset_index()
    result['hit_rate']=result.hit_rate.round(5)
    from statsmodels.stats.proportion import proportion_confint
    low,high=proportion_confint(result.successes,result['count'],method='wilson')
    result['ci_low']=np.round(low,5);result['ci_high']=np.round(high,5)
    return result


def run(a):
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    candidates=load_candidates(a.candidates,a.min_depth)
    print(f'Outside M15 candidate-side rows: {len(candidates):,}',flush=True)
    m5=load_m5(a)
    print(f'Loaded M5 bars: {len(m5):,} from {m5.time_dt.iloc[0]} to {m5.time_dt.iloc[-1]} (raw broker labels)',flush=True)
    labeled=generate_labels(candidates,m5,a)
    labeled.to_csv(out/'labeled_events.csv',index=False)
    splits,cutoffs=split_periods(labeled,a.calibration_fraction,a.holdout_fraction)
    train,cal,test=splits
    for name,section in zip(('train','calibration','holdout'),splits):
        print(f'{name}: {len(section):,} labeled variants, {section.time.nunique()} unique decisions',flush=True)
    bins=bins_summary(test)
    bins.to_csv(out/'depth_outcomes.csv',index=False)
    from sklearn.exceptions import ConvergenceWarning
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',ConvergenceWarning)
        model_metrics,pred=calibration_report(train,cal,test,a.min_model_events)
    model_metrics.to_csv(out/'probability_metrics.csv',index=False)
    pred.to_csv(out/'holdout_predictions.csv',index=False)
    totals=test.groupby(['mode','target_type','horizon_m15']).agg(n=('success','size'),hits=('success','sum'),rate=('success','mean')).reset_index()
    totals.to_csv(out/'holdout_rates.csv',index=False)
    report={
        'generated_utc':datetime.now(timezone.utc).isoformat(),
        'symbol':a.symbol,'candidate_side_rows':len(candidates),'labeled_variants':len(labeled),
        'cutoff_calibration_start':cutoffs[0],'cutoff_holdout_start':cutoffs[1],
        'train_examples':len(train),'calibration_examples':len(cal),'holdout_examples':len(test),
        'config':vars(a),'time_basis':'RAW MT5 BROKER EPOCH LABELS; historical UTC/DST unverified',
        'warnings':[
          'Data includes ALL observed outside-band M15 side-events, NOT exclusively armed state-machine reversal setups.',
          'Mode H1 is inclusive: H1 may qualify even when H4 also qualifies; groups overlap.',
          'Probabilities answer fixed band target BEFORE stop within the horizon, subject to trade viability, not probability of a market turning.',
          'Targets are fixed at M15 decision; contrary to dynamically moving Bollinger Bands.',
          'No target-first guarantees: if stop and target in same M5 candle stop is assumed first.',
          'M5 bid candles and approximated asks/spread, fixed slippage; true tick fills may differ.',
          'Sequential candidate events overlap in time and are correlated; effective sample size is smaller.',
          'Training/calibration/holdout use chronological boundaries with eight-hour purge around calibration and holdout.',
          'Holdout results are descriptive; do not select a new winning model from multiple holdout comparisons.',
          'Current historical broker timestamp labels may differ from true UTC, particularly around DST.',
          'Read-only research. No trading orders, no OpenAI API requests.'
        ]}
    (out/'report.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print('Saved probability research to:',out.resolve())
    return report

if __name__=='__main__':run(options())
