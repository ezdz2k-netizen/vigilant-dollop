"""Step 13.2 GOLD read-only entry/exit research. Never calls order_send.
M1 OHLC execution is approximate; tick CSV replay optional for higher fidelity.
"""
import argparse, json, math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import numpy as np
import pandas as pd
import step13_engine as core
import gold_exhaustion as base

MODES=('H1','H4','BOTH')
METHODS=('reentry','confirmation','structure','failed_retest')
# Staged controlled experiments: no combinatorial brute-force selection.
CONFIGS=[
 ('baseline','immediate','hold','none','middle'),
 ('delay05','favorable_0.05','hold','none','middle'),
 ('delay10','favorable_0.10','hold','none','middle'),
 ('delay20','favorable_0.20','hold','none','middle'),
 ('next_close','next_m15_close','hold','none','middle'),
 ('weak_exit','immediate','exit_weak','none','middle'),
 ('weak_be','immediate','be_weak','none','middle'),
 ('trail_atr','immediate','hold','atr_1.5','middle'),
 ('trail_chandelier','immediate','hold','chandelier_2.0','middle'),
 ('be_then_trail','immediate','hold','be_trail_1.5','middle'),
 ('opposite_base','immediate','hold','none','opposite'),
 ('fixed_2r','immediate','hold','none','2R'),
 ('spread_1.5x','immediate','hold','none','middle'),
 ('slippage_0.25','immediate','hold','none','middle'),
]
RESULT_COLS=['mode','method','config','signal_time','entry_time','exit_time','side','entry','exit','stop_initial','target','lots','pnl','r_net','equity_before','equity_after','exit_reason','holding_minutes','mfe_r','mae_r','entry_delay_minutes','spread_entry','slippage','commission','data_quality']

def _utcindex(d):
    if 'time' not in d: raise ValueError('CSV requires time column')
    if pd.api.types.is_numeric_dtype(d['time']): idx=pd.to_datetime(d.time,unit='s',utc=True)
    else: idx=pd.to_datetime(d.time,utc=True,format='mixed')
    if idx.isna().any():raise ValueError('Invalid timestamps')
    d=d.copy();d.index=idx;return d.sort_index().loc[lambda x:~x.index.duplicated(keep='last')]

def load_m1(cfg, from_dt, to_dt):
    path=Path(cfg.m1_csv) if cfg.m1_csv else None
    if path:
        d=_utcindex(pd.read_csv(path));print('Loaded cached M1',len(d),flush=True)
    else:
        import MetaTrader5 as mt5
        if not mt5.initialize():raise RuntimeError('MT5 initialize failed: '+str(mt5.last_error()))
        frames=[];cursor=from_dt;failed=[]
        try:
            if not mt5.symbol_select(cfg.symbol,True):raise RuntimeError('MT5 GOLD symbol unavailable')
            while cursor<to_dt:
                finish=min(cursor+timedelta(days=3),to_dt)
                bars=mt5.copy_rates_range(cfg.symbol,mt5.TIMEFRAME_M1,cursor,finish)
                if bars is None:failed.append((str(cursor),str(mt5.last_error())))
                elif len(bars):frames.append(pd.DataFrame(bars))
                cursor=finish
            if failed: raise RuntimeError(f'M1 retrieval failed in {len(failed)} chunks: {failed[:4]}')
        finally: mt5.shutdown()
        if not frames:raise RuntimeError('No M1 history returned')
        d=_utcindex(pd.concat(frames,ignore_index=True))
        print('Downloaded M1',len(d),flush=True)
        if cfg.save_m1: d.reset_index(drop=True).to_csv(cfg.save_m1,index=False)
    required={'open','high','low','close'}
    if not required.issubset(d.columns):raise ValueError(f'Missing M1 OHLC: {required-set(d.columns)}')
    d=d[(d.index>=pd.Timestamp(from_dt))&(d.index<pd.Timestamp(to_dt))].copy()
    if len(d)<1000:raise RuntimeError('M1 data insufficient')
    if (d.index.second!=0).any() or (d.index.minute%1!=0).any():raise RuntimeError('M1 boundaries invalid')
    return d

def validate_tick_sample(path, m1):
    """Optional independent evidence check only; does NOT claim tick-based fills."""
    if not path:return {'tick_validation':'not_supplied'}
    sample=pd.read_csv(path)
    if not {'time','bid','ask'}.issubset(sample.columns):raise ValueError('Tick CSV requires time,bid,ask')
    sample=_utcindex(sample)
    if len(sample)==0 or (sample.ask<=sample.bid).any():raise ValueError('Tick CSV invalid or empty')
    overlap=((sample.index>=m1.index[0])&(sample.index<=m1.index[-1])).sum()
    if overlap==0:raise ValueError('Tick history timestamps have no overlap with M1; verify broker clock')
    return {'tick_validation':'sample_checked_only_not_used_for_fills','tick_rows':len(sample),'tick_overlap_rows':int(overlap),'tick_first':str(sample.index[0]),'tick_last':str(sample.index[-1])}

def coverage(m1, m5, start, end):
    # Require M1 minute coverage near every potential trade. Do NOT require fills
    # during known broker shutdown/weekend: per-signal validation handles those.
    intervals=m1.index.to_series().diff().dt.total_seconds().dropna()
    stats={'m1_rows':len(m1),'m5_rows':len(m5),'first_m1':str(m1.index[0]),'last_m1':str(m1.index[-1]),'gaps_over_5_minutes':int((intervals>300).sum()),'largest_gap_minutes':float(intervals.max()/60),'m1_fraction_of_12_month_calendar':round(len(m1)/max(1,(end-start).total_seconds()/60),4)}
    return stats

def trade_replay(m1,sig,info,cfg,variation,start=None,end=None, equity=10000):
    name,entry_rule,weak_rule,trail_rule,target_rule=variation
    t=pd.Timestamp(sig['time']);idx=m1.index
    if start is not None and t<start:return None
    if end is not None and t>=end:return None
    # M1 replay strictly after signal availability; a quote at the identical
    # boundary is an M1 opening proxy, not an observed executable tick.
    first=idx.searchsorted(t,'left')
    if first>=len(idx) or idx[first]>t+pd.Timedelta(minutes=2):return None
    buy=sig['side']=='BUY'; direction=1 if buy else -1
    atr=float(sig['atr']); ifail=not np.isfinite(atr) or atr<=0
    if ifail:return None
    expire=min(t+pd.Timedelta(minutes=15),pd.Timestamp(end) if end is not None else t+pd.Timedelta(minutes=15))
    enter_ix=None
    for j in range(first,len(idx)):
        bar_time=idx[j]
        if bar_time>=expire and entry_rule not in ('immediate','next_m15_close'):break
        if bar_time>t+pd.Timedelta(minutes=30):break
        if entry_rule=='immediate':enter_ix=j;break
        # No intraminute unknown event ordering: threshold touched in M1 bar,
        # fill at NEXT available M1 opening, not favorable threshold itself.
        if entry_rule.startswith('favorable_'):
            threshold=float(entry_rule.split('_')[1])*atr
            initial=float(m1.iloc[first]['open'])
            touched=(m1.iloc[j]['high']>=initial+threshold) if buy else (m1.iloc[j]['low']<=initial-threshold)
            if touched:
                nxt=j+1
                if nxt<len(idx) and idx[nxt]<=bar_time+pd.Timedelta(minutes=2):enter_ix=nxt
                break
        if entry_rule=='next_m15_close' and bar_time>=t+pd.Timedelta(minutes=15):
            # candle [t,t+15) must have closed favorably, then use next M1 open
            slice_=m1[(m1.index>=t)&(m1.index<t+pd.Timedelta(minutes=15))]
            if len(slice_)>=12 and direction*(float(slice_.iloc[-1]['close'])-float(slice_.iloc[0]['open']))>0:enter_ix=j
            break
    if enter_ix is None:return None
    start_time=idx[enter_ix]
    if start_time>t+pd.Timedelta(minutes=17):return None
    initial_stop=(float(sig['sweep_low'])-cfg.stop_atr*atr if buy else float(sig['sweep_high'])+cfg.stop_atr*atr)
    point=float(info.point);tick_size=float(info.trade_tick_size);tick_value=float(info.trade_tick_value)
    if min(point,tick_size,tick_value)<=0:raise ValueError('Invalid symbol specs')
    bar=m1.iloc[enter_ix];spread=(float(bar.get('spread',0))*point if float(bar.get('spread',0))>0 else cfg.spread)*cfg.spread_mult
    opening=float(bar.open);entry=opening+(spread if buy else 0)+(-1 if not buy else 1)*cfg.slippage
    risk_dist=direction*(entry-initial_stop)
    if risk_dist<=0:return None
    target=(float(sig['middle']) if target_rule=='middle' else float(sig['opposite']) if target_rule=='opposite' else entry+direction*2*risk_dist)
    if direction*(target-entry)<=0:return None
    perlot=risk_dist/tick_size*tick_value+cfg.commission
    lots=math.floor((equity*cfg.risk/perlot)/info.volume_step)*info.volume_step
    lots=min(float(info.volume_max),lots)
    if lots<float(info.volume_min):return None
    max_t=min(start_time+pd.Timedelta(hours=cfg.max_hold),end if end is not None else pd.Timestamp.max.tz_localize('UTC'))
    stop=initial_stop;best=entry;mfe=0.;mae=0.;exit_price=None;exit_time=None;reason='TIME';checked_momentum=False
    for j in range(enter_ix,len(idx)):
        ti=idx[j]
        if ti>=max_t:break
        # Missing minute history makes stop/target ordering unverifiable.
        if j>enter_ix and ti-idx[j-1]>pd.Timedelta(minutes=2):return None
        row=m1.iloc[j];s=(float(row.get('spread',0))*point if float(row.get('spread',0))>0 else cfg.spread)*cfg.spread_mult
        hi=float(row.high);lo=float(row.low)
        mfe=max(mfe,direction*((hi if buy else lo)-entry));mae=max(mae,-direction*((lo if buy else hi)-entry))
        # PREEXISTING stop/target checked before new trailing update.
        hit_stop=lo<=stop if buy else hi+s>=stop
        hit_target=hi>=target if buy else lo+s<=target
        if hit_stop or hit_target:
            reason='STOP' if hit_stop else 'TARGET'
            exit_price=(stop if hit_stop else target)+(-cfg.slippage if buy else cfg.slippage)
            exit_time=ti+pd.Timedelta(minutes=1);break
        if not checked_momentum and weak_rule!='hold':
            # First FULL M15 candle beginning on/after entry. Decisions become
            # available only at its close, never retroactively.
            first_full=start_time.ceil('15min')
            check_at=first_full+pd.Timedelta(minutes=15)
            if ti>=check_at:
                block=m1[(m1.index>=first_full)&(m1.index<check_at)]
                checked_momentum=True
                if len(block)>=12:
                    o=float(block.iloc[0].open);c=float(block.iloc[-1].close)
                    high=float(block.high.max());low=float(block.low.min());rng=max(high-low,1e-9)
                    strong=(direction*(c-o)>0 and abs(c-o)/rng>=cfg.momentum_body and direction*(c-entry)>=cfg.momentum_progress*atr)
                    if not strong:
                        if weak_rule=='exit_weak':
                            # decide at completed M15, exit at available M1 opening
                            exit_price=float(row.open)+(0 if buy else s)+(-cfg.slippage if buy else cfg.slippage)
                            exit_time=ti;reason='WEAK_EXIT';break
                        elif weak_rule=='be_weak':
                            cost_price=(cfg.commission*lots/(tick_value*lots/tick_size)) if lots else 0
                            be=entry+direction*(cost_price+2*cfg.slippage)
                            stop=max(stop,be) if buy else min(stop,be)
        # Trail only from ALREADY completed M1 bars; updated stop activates
        # at next M1 bar, not against the current bar high-low.
        best=max(best,hi) if buy else min(best,lo)
        if trail_rule!='none':
            profit=direction*(best-entry)
            if profit>=risk_dist*cfg.activation_r:
                mult=2. if 'chandelier' in trail_rule else 1.5
                proposed=best-direction*(mult*atr)
                if trail_rule.startswith('be_trail'):
                    proposed=max(proposed,entry) if buy else min(proposed,entry)
                stop=max(stop,proposed) if buy else min(stop,proposed)
    if exit_price is None:
        j=max(enter_ix,min(len(idx)-1,idx.searchsorted(max_t,'left')-1)); row=m1.iloc[j]
        s=(float(row.get('spread',0))*point if float(row.get('spread',0))>0 else cfg.spread)*cfg.spread_mult
        exit_price=float(row.close)+(0 if buy else s)+(-cfg.slippage if buy else cfg.slippage)
        exit_time=idx[j]+pd.Timedelta(minutes=1)
    pnl=direction*(exit_price-entry)/tick_size*tick_value*lots-lots*cfg.commission
    return {'mode':sig['mode'],'method':sig['confirmation'],'config':name,'signal_time':str(t),'entry_time':str(start_time),'exit_time':str(exit_time),'side':sig['side'],'entry':entry,'exit':exit_price,'stop_initial':initial_stop,'target':target,'lots':lots,'pnl':pnl,'r_net':pnl/(perlot*lots),'equity_before':equity,'equity_after':equity+pnl,'exit_reason':reason,'holding_minutes':(exit_time-start_time).total_seconds()/60,'mfe_r':mfe/risk_dist,'mae_r':mae/risk_dist,'entry_delay_minutes':(start_time-t).total_seconds()/60,'spread_entry':spread,'slippage':cfg.slippage,'commission':lots*cfg.commission,'data_quality':'M1_OHLC_APPROX'}

def replay_portfolio(m1,signals,info,cfg,variation,start,end):
    cfg=SimpleNamespace(**vars(cfg))
    if variation[0]=='spread_1.5x':cfg.spread_mult*=1.5
    if variation[0]=='slippage_0.25':cfg.slippage+=0.25
    equity=cfg.equity;out=[];last_exit=pd.Timestamp.min.tz_localize('UTC');eligible=0
    for sig in sorted(signals,key=lambda x:x['time']):
        stamp=pd.Timestamp(sig['time'])
        if stamp<start or stamp>=end or stamp<last_exit:continue
        if stamp+pd.Timedelta(hours=cfg.max_hold)>end:continue
        eligible+=1
        trade=trade_replay(m1,sig,info,cfg,variation,start,end,equity)
        if trade:
            out.append(trade);equity=trade['equity_after'];last_exit=pd.Timestamp(trade['exit_time'])
            if equity<=0:break
    return out,eligible

def stat(rows,initial=10000):
    if not rows:return dict(trades=0,return_pct=0.,profit_factor=None,max_drawdown_pct=0.,win_rate_pct=None,net_pnl=0.,avg_r=None)
    pnl=np.array([r['pnl'] for r in rows]);eq=np.array([initial]+[r['equity_after'] for r in rows]);peak=np.maximum.accumulate(eq)
    gain=pnl[pnl>0].sum();loss=-pnl[pnl<0].sum()
    return dict(trades=len(rows),return_pct=round((eq[-1]/initial-1)*100,3),profit_factor=round(gain/loss,3) if loss else None,max_drawdown_pct=round(float(np.max((peak-eq)/np.maximum(peak,1e-9))*100),3),win_rate_pct=round(float((pnl>0).mean()*100),2),net_pnl=round(float(pnl.sum()),2),avg_r=round(float(np.mean([r['r_net'] for r in rows])),3))

def generate_signals(cfg,m5):
    feature=base.build_features(m5,cfg.months)
    result={}
    for mode in MODES:
        eps,obs=core.create_episodes(feature,mode,cfg.depth,cfg.expiry)
        for method in METHODS:
            result[(mode,method)]=core.make_entry_signals(eps,obs,method,'none')
    return result

def run(cfg):
    out=Path(cfg.output);out.mkdir(parents=True,exist_ok=True)
    m5,live_info=base.load_history(cfg.symbol,cfg.months,cfg.m5_cache)
    info=base.info_specs(live_info,cfg)
    end=m5.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)+timedelta(minutes=5)
    begin=end-timedelta(days=round(cfg.months*365.25/12))
    m1=load_m1(cfg,begin-timedelta(days=7),end)
    quality=coverage(m1,m5,begin,end)
    quality.update(validate_tick_sample(cfg.tick_csv,m1))
    if m1.index[-1]<pd.Timestamp(end)-pd.Timedelta(minutes=10):raise RuntimeError('M1 data does not cover latest M5 segment')
    signals=generate_signals(cfg,m5)
    # Two development folds: training 6m, test 2m; final 2m descriptive holdout.
    holdout_start=pd.Timestamp(end)-pd.Timedelta(days=60)
    fold_starts=[pd.Timestamp(begin)+pd.Timedelta(days=180),pd.Timestamp(begin)+pd.Timedelta(days=240)]
    fold_starts=[x for x in fold_starts if x+pd.Timedelta(days=60)<=holdout_start]
    fold_rows=[];holdout_rows=[];trades=[];selection=[]
    for mode in MODES:
      for method in METHODS:
        scores=[]
        for variant in CONFIGS:
          tests=[]
          for fold_i,te0 in enumerate(fold_starts,1):
            te1=te0+pd.Timedelta(days=60)
            rr,eligible=replay_portfolio(m1,signals[(mode,method)],info,cfg,variant,te0,te1)
            st=stat(rr,cfg.equity)
            fold_rows.append(dict(mode=mode,method=method,config=variant[0],fold=fold_i,eligible_signals=eligible,**st))
            tests.append(st)
          # Don't select from low-count evidence; select only from development folds.
          n=sum(s['trades'] for s in tests);avg_r=np.mean([s['avg_r'] for s in tests if s['avg_r'] is not None]) if any(s['avg_r'] is not None for s in tests) else -999
          worst_dd=max([s['max_drawdown_pct'] for s in tests] or [100]);score=avg_r-0.02*worst_dd if n>=cfg.min_trades else -999
          scores.append((score,variant[0],n))
        selected=max(scores,key=lambda x:x[0])[1]
        selection.append(dict(mode=mode,method=method,selected_config=selected,development_score=max(scores)[0]))
        for variant in CONFIGS:
          rr,eligible=replay_portfolio(m1,signals[(mode,method)],info,cfg,variant,holdout_start,pd.Timestamp(end))
          stats=stat(rr,cfg.equity)
          holdout_rows.append(dict(mode=mode,method=method,config=variant[0],selected=(variant[0]==selected),eligible_signals=eligible,**stats))
          trades.extend([{**r,'period':'holdout'} for r in rr])
    pd.DataFrame(fold_rows).to_csv(out/'walkforward_folds.csv',index=False)
    pd.DataFrame(selection).to_csv(out/'development_selections.csv',index=False)
    pd.DataFrame(holdout_rows).to_csv(out/'holdout_comparison.csv',index=False)
    pd.DataFrame(trades,columns=RESULT_COLS+['period']).to_csv(out/'holdout_trades.csv',index=False)
    report={'symbol':cfg.symbol,'created_utc':datetime.now(timezone.utc).isoformat(),'config':vars(cfg),'data_quality':quality,'folds':len(fold_starts),'holdout_start':str(holdout_start),'warnings':['Historical M1 OHLC does not prove intraminute ordering or executable quotes.','All trade prices are indicative M1 opening prices with modeled spread/slippage.','No historical tick-level execution unless separately verified.','Previously inspected GOLD dates cannot serve as fresh independent validation.','Broker UTC/DST labels not verified.','No MT5 orders or account mutations.']}
    (out/'report.json').write_text(json.dumps(report,indent=2,default=str))
    print('Backtest results saved:',out)

def arguments():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--symbol',default='GOLD');p.add_argument('--months',type=int,default=12)
 p.add_argument('--m5-cache',default='');p.add_argument('--m1-csv',default='');p.add_argument('--save-m1',default='');p.add_argument('--tick-csv',default='')
 p.add_argument('--output',default='step13_2_gold/results');p.add_argument('--equity',type=float,default=10000);p.add_argument('--risk',type=float,default=.005)
 p.add_argument('--spread',type=float,default=.3);p.add_argument('--spread-mult',type=float,default=1.)
 p.add_argument('--slippage',type=float,default=.1);p.add_argument('--commission',type=float,default=7.)
 p.add_argument('--stop-atr',type=float,default=.25);p.add_argument('--max-hold',type=float,default=48)
 p.add_argument('--depth',type=float,default=.1);p.add_argument('--expiry',type=int,default=6)
 p.add_argument('--activation-r',type=float,default=1.);p.add_argument('--momentum-body',type=float,default=.5)
 p.add_argument('--momentum-progress',type=float,default=.1);p.add_argument('--min-trades',type=int,default=15)
 for n,default in [('tick_size',.01),('tick_value',1.),('point',.01),('min_lot',.01),('max_lot',10.),('lot_step',.01)]:p.add_argument('--'+n.replace('_','-'),type=float,default=default)
 a=p.parse_args()
 if a.months<12 or not 0<a.risk<=.02 or a.spread_mult<=0 or a.slippage<0:p.error('Invalid research settings')
 return a
if __name__=='__main__':run(arguments())
