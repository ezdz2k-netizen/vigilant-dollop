"""Step 13: read-only FxPro GOLD BB lifecycle, four entries, walk-forward research.
Historical timestamps retain unverified broker-clock epoch labels.
Never calls order_send or places trades.
"""
import argparse
import json
import math
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
import numpy as np
import pandas as pd
import gold_exhaustion as base

MODES=('H1','H4','BOTH')
METHODS=('reentry','confirmation','structure','failed_retest')
TARGETS=('middle','opposite')
LIFECYCLE_COLUMNS=['episode','mode','side','arm_time','depth','horizon_m15','mfe_atr','mae_atr','net_excursion_atr']
FEATURE_COLUMNS=['time','side','close','high','low','atr','depth','outside','mid','target','h1_out','h4_out',
                 'm15_upper','m15_lower','adx','rsi_div','adx_fade','choch','liquidity_sweep']

def qualifies(row,mode):
    return bool(row.h1_out if mode=='H1' else row.h4_out if mode=='H4' else (row.h1_out and row.h4_out))

def inside(row):
    return bool(row.m15_lower<row.close<row.m15_upper)

def arm_events(features,mode,depth=.1,expiry=6):
    """Causal state machine. One episode per arm; fresh strong M15 outside close rearms.
    An armed episode invalidates when an M15 close is INSIDE and both developing HTF prices inside.
    Note completed-candle H1/H4 values, not intra-M15 H1/H4 extrema.
    """
    episodes=[]; checks=[]
    for side,g in features.groupby('side',sort=False):
        g=g.sort_values('time'); active=None; episode_id=0
        for x in g.itertuples(index=False):
            stamp=pd.Timestamp(x.time); strong=bool(x.outside and x.depth>=depth and qualifies(x,mode))
            if active is not None:
                active['sweep_low']=min(active['sweep_low'],float(x.low))
                active['sweep_high']=max(active['sweep_high'],float(x.high))
                age=(stamp-active['armed_at']).total_seconds()/900
                reset = bool(inside(x) and getattr(x,'h1_inside',not x.h1_out) and getattr(x,'h4_inside',not x.h4_out))
                if reset or age>expiry:
                    active['end']=stamp;active['status']='INVALIDATED' if reset else 'EXPIRED'; active=None
            if active is None and strong:
                episode_id+=1
                active={'key':f'{mode}:{side}:{episode_id}','mode':mode,'side':side,'armed_at':stamp,
                        'depth':float(x.depth),'atr':float(x.atr),'sweep_low':float(x.low),
                        'sweep_high':float(x.high),'start_price':float(x.close),'middle_at_arm':float(x.mid),
                        'h1_at_arm':bool(x.h1_out),'h4_at_arm':bool(x.h4_out),'status':'ARMED','end':pd.NaT,
                        'trigger':None,'entry_time':pd.NaT}
                episodes.append(active)
            checks.append({'time':stamp,'side':side,'mode':mode,'strong':int(strong),'inside':int(inside(x)),
                           'active':int(active is not None),'invalidated':int(active is None and not strong)})
    return episodes

def create_episodes(features,mode,depth=.1,expiry=6):
    """Return episodes and in-episode M15 observations, without future-centered pivot logic."""
    episodes=[]; observations=[]
    for side,g in features.groupby('side',sort=False):
        active=None; counter=0
        for x in g.sort_values('time').itertuples(index=False):
            t=pd.Timestamp(x.time)
            if active is not None:
                age=(t-active['armed_at']).total_seconds()/900
                if age>expiry or (inside(x) and getattr(x,'h1_inside',not x.h1_out) and getattr(x,'h4_inside',not x.h4_out)):
                    active['status']='EXPIRED' if age>expiry else 'INVALIDATED';active['end']=t
                    active=None
            if active is None and x.outside and x.depth>=depth and qualifies(x,mode):
                counter+=1
                active={'key':f'{mode}:{side}:{counter}','mode':mode,'side':side,'armed_at':t,
                        'depth':float(x.depth),'atr':float(x.atr),'sweep_low':float(x.low),'sweep_high':float(x.high),
                        'start_price':float(x.close),'h1_at_arm':bool(x.h1_out),'h4_at_arm':bool(x.h4_out),
                        'status':'ARMED','end':pd.NaT}
                episodes.append(active)
            if active is not None:
                active['sweep_low']=min(active['sweep_low'],float(x.low))
                active['sweep_high']=max(active['sweep_high'],float(x.high))
                observations.append({'key':active['key'],'time':t,'side':side,'close':float(x.close),
                    'high':float(x.high),'low':float(x.low),'mid':float(x.mid),'opposite':float(x.target),
                    'inside':inside(x),'rsi_div':bool(x.rsi_div),'adx_fade':bool(x.adx_fade),
                    'choch':bool(x.choch),'liquidity_sweep':bool(x.liquidity_sweep),
                    'atr':float(x.atr),'depth':float(x.depth)})
    return episodes,pd.DataFrame(observations)

def make_entry_signals(episodes, obs, method, filter_name='none', retest_buffer=.15):
    """All predicates decided at a completed M15 boundary; next available M5 open is fill.
    confirmation: first inside close followed by further reversal-direction close.
    structure: causal prior-3-candle CHOCH proxy from feature file.
    failed_retest: after inside close, retest within ATR buffer of sweep extreme,
                   then a reversal-direction close away from it (two observation stages).
    """
    if obs.empty:return []
    grouped={key:g.sort_values('time') for key,g in obs.groupby('key')}
    outputs=[]
    for ep in episodes:
        g=grouped.get(ep['key'])
        if g is None or len(g)<2:continue
        buy=ep['side']=='BUY'; first_inside=None; retested=False
        for r in g.itertuples(index=False):
            if r.time<=ep['armed_at']:continue
            if pd.notna(ep['end']) and r.time>=ep['end']:break
            if r.inside and first_inside is None:first_inside=r
            ok=False
            if method=='reentry':ok=bool(first_inside is not None and r.time==first_inside.time)
            elif method=='confirmation':
                ok=bool(first_inside is not None and r.time>first_inside.time and
                        ((r.close>first_inside.close) if buy else (r.close<first_inside.close)))
            elif method=='structure':ok=bool(r.choch)
            elif method=='failed_retest':
                if first_inside is not None and r.time>first_inside.time:
                    testlevel=ep['sweep_low'] if buy else ep['sweep_high']
                    near= (r.low<=testlevel+retest_buffer*ep['atr']) if buy else (r.high>=testlevel-retest_buffer*ep['atr'])
                    if not retested and near:retested=True;continue
                    if retested and ((r.close>first_inside.close) if buy else (r.close<first_inside.close)) and r.inside:ok=True
            if not ok:continue
            if filter_name=='rsi' and not r.rsi_div:continue
            if filter_name=='adx' and not r.adx_fade:continue
            if filter_name=='either' and not (r.rsi_div or r.adx_fade):continue
            outputs.append({'time':r.time,'side':ep['side'],'mode':ep['mode'],'confirmation':method,
                'episode':ep['key'],'depth':ep['depth'],'sweep_low':ep['sweep_low'],
                'sweep_high':ep['sweep_high'],'atr':ep['atr'],'middle':r.mid,'opposite':r.opposite,
                'rsi_div':bool(r.rsi_div),'adx_fade':bool(r.adx_fade),'filter':filter_name})
            break
    return sorted(outputs,key=lambda x:x['time'])

def lifecycle_paths(m5,episodes,horizons=(4,8,16,32)):
    """Measure paths from arm-time with no trading assumptions. MFE/MAE direction-normalized."""
    times=m5.index; highs=m5.high.to_numpy(float);lows=m5.low.to_numpy(float); closes=m5.close.to_numpy(float)
    out=[]
    for ep in episodes:
        # The arm signal is known at M15 close: start at the FIRST M5 bar
        # opening on/after that boundary; never count a bar preceding the decision.
        start=times.searchsorted(ep['armed_at'],'left');buy=ep['side']=='BUY'; ref=ep['start_price']; atr=ep['atr']
        if start>=len(times):continue
        for n in horizons:
            stop=times.searchsorted(ep['armed_at']+pd.Timedelta(minutes=15*n),'left')
            if stop<=start:continue
            h=highs[start:stop];l=lows[start:stop]
            if not len(h):continue
            mfe=max(0.,((h.max()-ref) if buy else (ref-l.min()))/atr)
            mae=max(0.,((ref-l.min()) if buy else (h.max()-ref))/atr)
            out.append({'episode':ep['key'],'mode':ep['mode'],'side':ep['side'],'arm_time':str(ep['armed_at']),
                        'depth':ep['depth'],'horizon_m15':n,'mfe_atr':mfe,'mae_atr':mae,'net_excursion_atr':mfe-mae})
    return pd.DataFrame(out,columns=LIFECYCLE_COLUMNS)

def simulate(m5,signals,info,cfg,target='middle',start=None,end=None):
    """Same next-open, stop-first OHLC approximation as previous project;
    adds MFE/MAE. End-of-window censored; do not cross fold boundary.
    """
    ticksz=float(info.trade_tick_size);tickval=float(info.trade_tick_value);point=float(info.point)
    if min(ticksz,tickval,point)<=0:raise ValueError('Invalid broker tick specs')
    idx=m5.index; equity=cfg.equity;until=pd.Timestamp.min.tz_localize('UTC');out=[]
    a=m5[['open','high','low','close','spread']].to_numpy(float)
    for sig in signals:
        t=pd.Timestamp(sig['time'])
        if start is not None and t<start:continue
        if end is not None and t>=end:continue
        if t<until:continue
        if end is not None and t+pd.Timedelta(hours=cfg.max_hold)>end:continue
        # Signal is confirmed at t (M15 close). Execute at the first available
        # M5 OPEN on/after t, never earlier and never >5 minutes later.
        i=idx.searchsorted(t,'left')
        if i>=len(idx) or idx[i]<t or idx[i]>t+pd.Timedelta(minutes=5):continue
        b=sig['side']=='BUY'; row=a[i];sp=row[4]*point if row[4]>0 else cfg.spread
        entry=row[0]+(sp if b else 0)+(cfg.slippage if b else -cfg.slippage)
        stop=sig['sweep_low']-cfg.stop_atr*sig['atr'] if b else sig['sweep_high']+cfg.stop_atr*sig['atr']+sp
        rawtarget=sig['middle'] if target=='middle' else sig['opposite'];target_px=rawtarget if b else rawtarget+sp
        riskpx=entry-stop if b else stop-entry;reward=target_px-entry if b else entry-target_px
        if min(riskpx,reward)<=0:continue
        step=float(info.volume_step);unitrisk=(riskpx/ticksz)*tickval+cfg.commission
        if step<=0 or unitrisk<=0:continue
        lots=math.floor((equity*cfg.risk/unitrisk)/step+1e-9)*step
        lots=min(float(info.volume_max),lots)
        if lots<float(info.volume_min):continue
        end_ix=min(len(idx),idx.searchsorted(idx[i]+pd.Timedelta(hours=cfg.max_hold),'right'))
        if end is not None:end_ix=min(end_ix,idx.searchsorted(end,'left'))
        if end_ix<=i:continue
        ex=None;reason='TIME';ext_high=-np.inf;ext_low=np.inf
        for j in range(i,end_ix):
            bar=a[j];spr=bar[4]*point if bar[4]>0 else cfg.spread
            ext_high=max(ext_high,bar[1]);ext_low=min(ext_low,bar[2])
            hitstop=(bar[2]<=stop) if b else (bar[1]+spr>=stop)
            hittarget=(bar[1]>=target_px) if b else (bar[2]+spr<=target_px)
            if hitstop or hittarget:
                reason='STOP' if hitstop else 'TARGET'
                px=(stop if hitstop else target_px)+(-cfg.slippage if b else cfg.slippage)
                ex=(idx[j]+pd.Timedelta(minutes=5),px);break
        if ex is None:
            bar=a[end_ix-1];spr=bar[4]*point if bar[4]>0 else cfg.spread
            ex=(idx[end_ix-1]+pd.Timedelta(minutes=5),bar[3]+(0 if b else spr)+(-cfg.slippage if b else cfg.slippage))
        pnl=((ex[1]-entry) if b else (entry-ex[1]))/ticksz*tickval*lots-lots*cfg.commission
        mfe=((ext_high-entry) if b else (entry-ext_low))/sig['atr']
        mae=((entry-ext_low) if b else (ext_high-entry))/sig['atr']
        before=equity;equity+=pnl;until=ex[0]
        out.append({'mode':sig['mode'],'method':sig['confirmation'],'filter':sig['filter'],'target_type':target,
                    'signal_time':str(t),'signal_known_at':str(t),
                    'entry_time':str(idx[i]),'entry_bar_open_time':str(idx[i]),
                    'entry_delay_seconds':float((idx[i]-t).total_seconds()),
                    'entry_fill_basis':'next_available_M5_open_bid_plus_modeled_spread_slippage',
                    'entry_bid_open':float(row[0]),'entry_spread_price':float(sp),
                    'entry_slippage_price':float(cfg.slippage),
                    'exit_time':str(ex[0]),'side':sig['side'],
                    'entry':entry,'stop':stop,'target':target_px,'exit':ex[1],'lots':lots,'pnl':pnl,
                    'equity_before':before,'equity_after':equity,'exit_reason':reason,
                    'mfe_atr':mfe,'mae_atr':mae,'depth':sig['depth'],'episode':sig['episode']})
        if equity<=0:break
    return out

def summarize(rows,starting):
    out=base.metrics(rows,starting) if rows else base.metrics([],starting)
    return out

def run(cfg):
    out=Path(cfg.output).resolve();out.mkdir(parents=True,exist_ok=True)
    m5,mt5_info=base.load_history(cfg.symbol,cfg.months,cfg.cache)
    info=base.info_specs(mt5_info,cfg)
    print(f'Loaded {len(m5)} M5 bars; generating causal indicator features...',flush=True)
    features=base.build_features(m5,cfg.months)
    features.to_csv(out/'feature_snapshots.csv',index=False)
    end=m5.index[-1];start=end-pd.Timedelta(days=round(cfg.months*365.25/12));
    final_start=end-pd.Timedelta(days=60);train_len=pd.Timedelta(days=180);test_len=pd.Timedelta(days=60)
    episodes_by_mode={};signals={};lifecycle=[]
    for mode in MODES:
        eps,obs=create_episodes(features,mode,cfg.depth,cfg.expiry)
        episodes_by_mode[mode]=eps
        # DataFrame iteration yields column LABELS, not rows.
        # Convert each mode's paths into record dictionaries before extending.
        lifecycle.extend(lifecycle_paths(m5,eps).to_dict(orient='records'))
        for method in METHODS:
            for filt in ('none','rsi','adx','either'):
                signals[(mode,method,filt)]=make_entry_signals(eps,obs,method,filt)
        print(f'{mode}: {len(eps)} armed episodes',flush=True)
    pd.DataFrame(lifecycle,columns=LIFECYCLE_COLUMNS).to_csv(out/'lifecycle_paths.csv',index=False)
    # Reentry timing and further countertrend extension, measured after arming.
    episode_timing=[]
    for mode in MODES:
        eps,obs=create_episodes(features,mode,cfg.depth,cfg.expiry)
        grouped={k:g.sort_values('time') for k,g in obs.groupby('key')} if not obs.empty else {}
        for ep in eps:
            g=grouped.get(ep['key'])
            later=g[g.time>ep['armed_at']] if g is not None else pd.DataFrame()
            reentered=later[later.inside] if not later.empty else pd.DataFrame()
            first=reentered.iloc[0].time if not reentered.empty else pd.NaT
            buy=ep['side']=='BUY'
            extra=(max(0.,(ep['start_price']-later.low.min())/ep['atr']) if buy
                    else max(0.,(later.high.max()-ep['start_price'])/ep['atr'])) if not later.empty else 0.
            episode_timing.append({'key':ep['key'],'mode':mode,'side':ep['side'],
                'arm_time':str(ep['armed_at']),'status':ep['status'],'depth':ep['depth'],
                'first_reentry_time':str(first) if pd.notna(first) else '',
                'bars_to_reentry':(first-ep['armed_at']).total_seconds()/900 if pd.notna(first) else np.nan,
                'extra_extension_atr':extra})
    pd.DataFrame(episode_timing).to_csv(out/'reentry_timing.csv',index=False)
    all_episodes=[{**x,'armed_at':str(x['armed_at']),'end':str(x['end'])} for seq in episodes_by_mode.values() for x in seq]
    pd.DataFrame(all_episodes).to_csv(out/'episodes.csv',index=False)
    folds=[];selected=[];comparison=[];trade_rows=[]
    cursor=start+train_len
    while cursor+test_len<=final_start+pd.Timedelta(days=1):
        folds.append((cursor-train_len,cursor,cursor+test_len));cursor+=test_len
    if not folds:raise RuntimeError('Insufficient history for walk-forward folds; use at least 12 months')
    for mode in MODES:
        folds_for_mode=[]
        for i,(tr0,tr1,te1) in enumerate(folds,1):
            candidates=[]
            for method in METHODS:
                for filt in ('none','rsi','adx','either'):
                    for tgt in TARGETS:
                        trades=simulate(m5,signals[(mode,method,filt)],info,cfg,tgt,tr0,tr1)
                        m=summarize(trades,cfg.equity)
                        score=m['return_pct']-0.5*m['max_drawdown_pct'] if m['trades']>=cfg.min_trades else -1e6
                        candidates.append((score,method,filt,tgt,m['trades']))
            best=max(candidates,key=lambda x:x[0]);_,method,filt,tgt,ntrain=best
            test_trades=simulate(m5,signals[(mode,method,filt)],info,cfg,tgt,tr1,te1)
            m=summarize(test_trades,cfg.equity)
            row={'mode':mode,'fold':i,'train_start':str(tr0),'train_end':str(tr1),'test_end':str(te1),
                 'method':method,'filter':filt,'target':tgt,'train_trades':ntrain,'development_score':best[0],**m}
            folds_for_mode.append(row);comparison.append(row)
        # Development-only fixed parameter choice: choose variant by frequency of winning folds,
        # then mean fold net return; no use of final holdout.
        variants={(r['method'],r['filter'],r['target']) for r in folds_for_mode}
        def devscore(v):
            rr=[r for r in folds_for_mode if (r['method'],r['filter'],r['target'])==v]
            return (sum(x['return_pct']>0 for x in rr),sum(x['return_pct'] for x in rr))
        pick=max(variants,key=devscore)
        method,filt,tgt=pick
        # Compare selected candidate to fixed baseline on SAME holdout dates.
        for label,method_,filt_,tgt_ in [('selected',method,filt,tgt),('baseline','reentry','none','middle')]:
            trades=simulate(m5,signals[(mode,method_,filt_)],info,cfg,tgt_,final_start,end+pd.Timedelta(minutes=5))
            m=summarize(trades,cfg.equity)
            selected.append({'mode':mode,'variant':label,'method':method_,'filter':filt_,'target':tgt_,**m})
            trade_rows.extend([{**r,'variant':label,'period':'final_holdout'} for r in trades])
    # Descriptive comparison of all four entries on final holdout. NEVER use to pick
    # an ostensibly 'winning' strategy after observing these outcomes.
    all_methods=[]
    for mode in MODES:
        for method in METHODS:
            for tgt in TARGETS:
                tr=simulate(m5,signals[(mode,method,'none')],info,cfg,tgt,final_start,end+pd.Timedelta(minutes=5))
                all_methods.append({'mode':mode,'method':method,'filter':'none','target':tgt,**summarize(tr,cfg.equity)})
    pd.DataFrame(all_methods).to_csv(out/'holdout_method_comparison.csv',index=False)
    pd.DataFrame(comparison).to_csv(out/'walkforward_folds.csv',index=False)
    pd.DataFrame(selected).to_csv(out/'final_holdout.csv',index=False)
    pd.DataFrame(trade_rows).to_csv(out/'holdout_trades.csv',index=False)
    report={'generated_utc':datetime.now(timezone.utc).isoformat(),'symbol':cfg.symbol,
       'data':{'m5_bars':len(m5),'from_broker_label':str(m5.index[0]),'to_broker_label':str(end)},
       'config':vars(cfg),'folds':len(folds),'holdout_from':str(final_start),'final_holdout':selected,
       'warnings':['Historical broker clock labels are not verified UTC or DST corrected.',
       'Developing H1/H4 values are reconstructed only at completed M15 boundaries from M5 bars.',
       'Spread, slippage, fees and stop-first same-bar fills are modeled, not tick-perfect.',
       'Lifecycle MFE/MAE are path extrema, not an executable strategy or reversal probability.',
       'Research has no live orders and no OpenAI API calls.',
       'Previously examined dates are not a genuinely untouched holdout; obtain prospective validation.']}
    (out/'report.json').write_text(json.dumps(report,indent=2,default=str))
    print('Saved Step 13 results:',out,flush=True)
    return report

def parse_args():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbol',default='GOLD');p.add_argument('--months',type=int,default=12)
    p.add_argument('--cache',default='');p.add_argument('--output',default='step13_gold_lifecycle/results')
    p.add_argument('--equity',type=float,default=10000);p.add_argument('--risk',type=float,default=.005)
    p.add_argument('--spread',type=float,default=.30);p.add_argument('--slippage',type=float,default=.10)
    p.add_argument('--commission',type=float,default=7.0);p.add_argument('--stop-atr',dest='stop_atr',type=float,default=.25)
    p.add_argument('--max-hold',dest='max_hold',type=float,default=48);p.add_argument('--min-trades',dest='min_trades',type=int,default=5)
    p.add_argument('--depth',type=float,default=.10);p.add_argument('--expiry',type=int,default=6)
    p.add_argument('--tick-size',dest='tick_size',type=float,default=.01);p.add_argument('--tick-value',dest='tick_value',type=float,default=1.)
    p.add_argument('--point',type=float,default=.01);p.add_argument('--min-lot',dest='min_lot',type=float,default=.01)
    p.add_argument('--max-lot',dest='max_lot',type=float,default=10.);p.add_argument('--lot-step',dest='lot_step',type=float,default=.01)
    a=p.parse_args()
    if not (a.months>=12 and 0<a.risk<=.02 and a.equity>0 and a.depth>=0 and a.expiry>=1):p.error('Invalid configuration')
    return a

if __name__=='__main__':run(parse_args())
