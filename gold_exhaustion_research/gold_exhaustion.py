"""Read-only GOLD H1/H4 developing-BB exhaustion reversal research.
Broker epoch timestamps are BROKER CLOCK LABELS, not verified UTC.
No MT5 trading methods or order submission are used.
"""
import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd

BAND_PARITY_VERSION = '2026-10-13-session-aware-v5'

MODES = ('H1', 'H4', 'BOTH')
CONFIRM = ('reentry', 'rsi_div', 'adx_fade', 'choch', 'liquidity_sweep')
TARGETS = ('middle', 'opposite')


def load_history(symbol, months, cache='', chunk_days=7):
    if cache:
        df = pd.read_csv(cache)
        if 'time' not in df: raise ValueError('CSV requires raw MT5 time epoch seconds column')
        df = df.drop_duplicates('time').sort_values('time')
        info = None
    else:
        import MetaTrader5 as mt5
        if not mt5.initialize(): raise RuntimeError(f'MT5 init failed: {mt5.last_error()}')
        try:
            if not mt5.symbol_select(symbol, True): raise RuntimeError(f'MT5 symbol unavailable: {symbol}')
            info = mt5.symbol_info(symbol)
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=round(months * 365.25/12)+100)
            frames=[]; cursor=start
            while cursor<end:
                nxt=min(end,cursor+timedelta(days=chunk_days))
                rates=mt5.copy_rates_range(symbol,mt5.TIMEFRAME_M5,cursor,nxt)
                if rates is None: raise RuntimeError(f'M5 historical download failed {cursor} to {nxt}: {mt5.last_error()}')
                if len(rates): frames.append(pd.DataFrame(rates))
                cursor=nxt
            if not frames: raise RuntimeError('No M5 historical rates')
            df=pd.concat(frames,ignore_index=True).drop_duplicates('time').sort_values('time')
        finally:
            mt5.shutdown()
    if (df.time.astype('int64')%300!=0).any(): raise RuntimeError('Raw M5 timestamps are not 5-minute aligned')
    required={'open','high','low','close','tick_volume'}
    if not required.issubset(df.columns): raise ValueError('Missing OHLCV fields: '+str(required-set(df.columns)))
    if 'spread' not in df: df['spread']=0
    df=df.copy()
    df.index=pd.to_datetime(df['time'],unit='s',utc=True)
    df=df[~df.index.duplicated(keep='last')].sort_index()
    return df,info


def tf_bars(m5, rule, expected):
    f=m5.resample(rule,closed='left',label='left',origin='epoch').agg(
        open=('open','first'), high=('high','max'),low=('low','min'),close=('close','last'),
        tick_volume=('tick_volume','sum'),bars=('close','count'))
    f=f[f.bars==expected].copy()
    if len(f)<40: raise ValueError(f'Insufficient completed {rule} bars')
    f['mid']=f.close.rolling(20,min_periods=20).mean()
    f['std']=f.close.rolling(20,min_periods=20).std(ddof=0)
    f['upper']=f.mid+2*f['std'];f['lower']=f.mid-2*f['std']
    prev=f.close.shift()
    tr=pd.concat([f.high-f.low,(f.high-prev).abs(),(f.low-prev).abs()],axis=1).max(axis=1)
    f['atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    delta={'15min':15,'1h':60,'4h':240}[rule]
    f['close_time']=f.index+pd.Timedelta(minutes=delta)
    return f


def rsi(close, length=14):
    delta=close.diff();gain=delta.clip(lower=0).ewm(alpha=1/length,adjust=False,min_periods=length).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/length,adjust=False,min_periods=length).mean()
    return 100-100/(1+gain/loss.replace(0,np.nan))


def adx_components(f,length=14):
    up=f.high.diff();dn=-f.low.diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0.),index=f.index)
    minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.),index=f.index)
    prev=f.close.shift(1)
    tr=pd.concat([f.high-f.low,(f.high-prev).abs(),(f.low-prev).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/length,adjust=False,min_periods=length).mean()
    p=100*plus.ewm(alpha=1/length,adjust=False,min_periods=length).mean()/atr.replace(0,np.nan)
    m=100*minus.ewm(alpha=1/length,adjust=False,min_periods=length).mean()/atr.replace(0,np.nan)
    dx=100*(p-m).abs()/(p+m).replace(0,np.nan)
    return dx.ewm(alpha=1/length,adjust=False,min_periods=length).mean(),p,m


def developing_bands(m5, tf, boundary):
    """Session-aware reference for *developing* H1/H4 BB(20,2).

    A higher-timeframe trading bar is an observed nonempty calendar block;
    a GOLD trading break may shorten it. Use the 19 most recent observed
    PRIOR blocks and the currently known price in the developing block.
    Never use any M5 bar whose close is later than ``boundary``.

    The current observation must end at the M15 decision boundary; do not
    synthesize a fill or interpolate quotes across a market closure.
    """
    seconds = {'H1': 3600, 'H4': 14400}[tf]
    boundary = pd.Timestamp(boundary)
    start = boundary.floor(f'{seconds}s')
    if boundary == start:
        start -= pd.Timedelta(seconds=seconds)
    partial = m5[(m5.index >= start) & (m5.index < boundary)]
    if partial.empty or partial.index[-1] + pd.Timedelta(minutes=5) != boundary:
        return None
    prior = m5[m5.index < start]
    rule = '1h' if tf == 'H1' else '4h'
    blocks = prior.resample(rule, closed='left', label='left', origin='epoch').agg(
        close=('close', 'last'), n=('close', 'size'))
    last19 = blocks[blocks.n > 0].tail(19)
    if len(last19) < 19:
        return None
    vals = np.append(last19.close.to_numpy(dtype=float), float(partial.close.iloc[-1]))
    mid = float(np.mean(vals)); sd = float(np.std(vals, ddof=0))
    return {'upper': mid+2*sd, 'lower': mid-2*sd, 'middle': mid,
            'price': float(partial.close.iloc[-1]),
            'above': float(partial.close.iloc[-1]) > mid+2*sd,
            'below': float(partial.close.iloc[-1]) < mid-2*sd}


class DevelopingBandLookup:
    """Efficient implementation of the session-aware reference above.

    Prior bars are **observed** H1/H4 trading blocks; no calendar adjacency
    is required across daily/weekend breaks. This changes the old overly
    restrictive definition and should be benchmarked against FxPro H1/H4 data.
    """
    def __init__(self, m5, tf):
        self.seconds = {'H1': 3600, 'H4': 14400}[tf]
        rule = '1h' if tf == 'H1' else '4h'
        blocks = m5.resample(rule, closed='left', label='left', origin='epoch').agg(
            close=('close', 'last'), n=('close', 'size'))
        populated = blocks[blocks.n > 0]
        self.block_times = populated.index.as_unit('ns').asi8
        self.block_closes = populated.close.to_numpy(dtype=float)
        self.m5_times = m5.index.as_unit('ns').asi8
        self.m5_closes = m5.close.to_numpy(dtype=float)
        self.block_ns = self.seconds * 1_000_000_000
        self.five_ns = 300 * 1_000_000_000

    def at(self, boundary):
        t_ns = pd.Timestamp(boundary).as_unit('ns').value
        start_ns = (t_ns // self.block_ns) * self.block_ns
        if t_ns == start_ns:
            start_ns -= self.block_ns
        left = int(np.searchsorted(self.m5_times, start_ns, side='left'))
        right = int(np.searchsorted(self.m5_times, t_ns, side='left'))
        if right <= left or self.m5_times[right - 1] + self.five_ns != t_ns:
            return None
        endprior = int(np.searchsorted(self.block_times, start_ns, side='left'))
        if endprior < 19:
            return None
        vals = np.append(self.block_closes[endprior-19:endprior], self.m5_closes[right - 1])
        mid = float(np.mean(vals)); sd = float(np.std(vals, ddof=0))
        observed = float(self.m5_closes[right - 1])
        return {'upper': mid+2*sd, 'lower': mid-2*sd, 'middle': mid,
                'price': observed, 'above': observed > mid+2*sd,
                'below': observed < mid-2*sd}


def build_features(m5,months=12, min_depth=0.10):
    """Candidate events at M15 close; developing H1/H4 bands are causal.
    Conservative completeness check: M15 must have three M5 constituents.
    """
    m15=tf_bars(m5,'15min',3)
    m15['rsi']=rsi(m15.close)
    m15['adx'],m15['pdi'],m15['mdi']=adx_components(m15)
    m15['ema20']=m15.close.ewm(span=20,adjust=False).mean()
    m15['volume_ratio']=m15.tick_volume/m15.tick_volume.rolling(20).mean()
    m15['swing_high']=m15.high.shift(1).rolling(3).max()
    m15['swing_low']=m15.low.shift(1).rolling(3).min()
    # RSI divergence uses two KNOWN past M15 windows, no future-centered pivot detection
    m15['prior_high']=m15.high.shift(1).rolling(5).max()
    m15['prior_low']=m15.low.shift(1).rolling(5).min()
    m15['prior_rsi_high']=m15.rsi.shift(1).rolling(5).max()
    m15['prior_rsi_low']=m15.rsi.shift(1).rolling(5).min()
    # mild volume condition for liquidity sweep research; no real order-flow inference
    m15['buy_div']=(m15.low<m15.prior_low)&(m15.rsi>m15.prior_rsi_low)
    m15['sell_div']=(m15.high>m15.prior_high)&(m15.rsi<m15.prior_rsi_high)
    m15['adx_falling']=m15.adx<m15.adx.shift(1)
    records=[]
    h1_lookup=DevelopingBandLookup(m5,'H1')
    h4_lookup=DevelopingBandLookup(m5,'H4')
    m5time=m5.index
    for row in m15.itertuples():
        t=row.close_time
        if not np.isfinite(row.atr) or row.atr<=0 or not np.isfinite(row.rsi):continue
        # ensure 3 x M5 fully available, including final M5 closing exactly at t
        if t-pd.Timedelta(minutes=5) not in m5time:continue
        h1=h1_lookup.at(t);h4=h4_lookup.at(t)
        if h1 is None or h4 is None:continue
        for side in ('BUY','SELL'):
            depth=((row.lower-row.close) if side=='BUY' else (row.close-row.upper))/row.atr
            outside=depth>0
            rec=dict(time=t,side=side,close=float(row.close),high=float(row.high),low=float(row.low),
                atr=float(row.atr),depth=float(depth),outside=bool(outside),mid=float(row.mid),
                target=float(row.upper if side=='BUY' else row.lower),
                h1_out=bool(h1['below'] if side=='BUY' else h1['above']),
                h4_out=bool(h4['below'] if side=='BUY' else h4['above']),
                h1_upper=h1['upper'],h1_lower=h1['lower'],h4_upper=h4['upper'],h4_lower=h4['lower'],
                m15_upper=float(row.upper),m15_lower=float(row.lower),adx=float(row.adx),
                volume_ratio=float(row.volume_ratio),
                rsi_div=bool(row.buy_div if side=='BUY' else row.sell_div),
                adx_fade=bool(row.adx_falling and ((row.mdi<getattr(m15.loc[t-pd.Timedelta(minutes=15)],'mdi',np.nan)) if side=='BUY' else (row.pdi<getattr(m15.loc[t-pd.Timedelta(minutes=15)],'pdi',np.nan))) ) if t-pd.Timedelta(minutes=15) in m15.index else False,
                choch=bool(row.close>row.swing_high if side=='BUY' else row.close<row.swing_low),
                liquidity_sweep=bool((row.low<row.prior_low and row.close>row.prior_low) if side=='BUY' else (row.high>row.prior_high and row.close<row.prior_high)))
            records.append(rec)
    if not records:
        raise RuntimeError('No feature records: inspect H1/H4 history, session calendar and M15 data coverage')
    return pd.DataFrame(records).sort_values(['time','side']).reset_index(drop=True)


def signal_events(features,mode,confirm,min_depth=0.1,expire_bars=6):
    """Arms on OUTSIDE M15 close + required developing H1/H4 extension.
    If M15 is subsequently INSIDE and BOTH H1 & H4 are inside, invalidate.
    Confirmation must be observable at or after arming; next M5 bar fills.
    No repeated entry until a fresh strong outside M15 close re-arms.
    """
    out=[]
    for side,g in features.groupby('side',sort=False):
        armed=None
        last_reset=None
        for x in g.itertuples(index=False):
            higher= x.h1_out if mode=='H1' else (x.h4_out if mode=='H4' else x.h1_out and x.h4_out)
            # A later strong outside close can re-arm after a previous signal/expiration
            strong=x.outside and x.depth>=min_depth and higher
            if armed is not None:
                age=(x.time-armed['arm_time']).total_seconds()/900
                if age>expire_bars or (not x.outside and not x.h1_out and not x.h4_out):
                    armed=None
            if armed is None and strong:
                armed={'arm_time':x.time,'depth':x.depth,'sweep_low':x.low,'sweep_high':x.high,
                       'atr':x.atr,'middle':x.mid,'opposite':x.target}
            if armed is None: continue
            armed['sweep_low']=min(armed['sweep_low'],x.low)
            armed['sweep_high']=max(armed['sweep_high'],x.high)
            # Re-entry must occur after arming; a breach candle cannot be simultaneously a re-entry.
            if confirm=='reentry': ok=(x.time>armed['arm_time'] and not x.outside)
            else: ok=(x.time>=armed['arm_time'] and bool(getattr(x,confirm)))
            if ok:
                out.append({'time':x.time,'arm_time':armed['arm_time'],'side':side,'mode':mode,
                            'confirmation':confirm,'depth':armed['depth'],'sweep_low':armed['sweep_low'],
                            'sweep_high':armed['sweep_high'],'atr':armed['atr'],
                            'middle':x.mid,'opposite':x.target,'adx':x.adx})
                armed=None
    return sorted(out,key=lambda s:s['time'])


def info_specs(info,kwargs):
    if info is None:
        return SimpleNamespace(trade_tick_size=kwargs.tick_size,trade_tick_value=kwargs.tick_value,
                               point=kwargs.point,volume_min=kwargs.min_lot,volume_max=kwargs.max_lot,
                               volume_step=kwargs.lot_step)
    return info


def simulate(m5,signals,info,cfg,target='opposite',segment_start=None,segment_end=None):
    """Sequential virtual fills; one open trade per variant; no orders.
    Bid OHLC assumed. Entry at next available M5 open, stop-first if both touch.
    """
    times=m5.index; rows=list(m5.itertuples()); equity=cfg.equity; initial=equity
    used_until=pd.Timestamp.min.tz_localize('UTC'); ledger=[]
    ticksize=float(info.trade_tick_size);tickvalue=float(info.trade_tick_value);point=float(info.point)
    if min(ticksize,tickvalue,point)<=0:raise ValueError('Invalid broker tick specifications')
    for s in signals:
        t=s['time']
        if (segment_start is not None and t<segment_start) or (segment_end is not None and t>=segment_end):continue
        if t<used_until:continue
        if segment_end is not None and t+pd.Timedelta(hours=cfg.max_hold)>segment_end: continue
        ix=times.searchsorted(t,side='left')
        if ix>=len(rows) or times[ix]>t+pd.Timedelta(minutes=5):continue
        r=rows[ix];buy=s['side']=='BUY'
        spr=float(r.spread)*point if float(r.spread)>0 else cfg.spread
        entry=float(r.open)+(spr if buy else 0)+(cfg.slippage if buy else -cfg.slippage)
        # Stop beyond sweep extreme, stop levels are BID for BUY and ASK for SELL
        stop=(s['sweep_low']-cfg.stop_atr*s['atr']) if buy else (s['sweep_high']+cfg.stop_atr*s['atr']+spr)
        level=s['middle'] if target=='middle' else s['opposite']
        target_px=level if buy else level+spr
        riskpx=(entry-stop) if buy else (stop-entry)
        rewardpx=(target_px-entry) if buy else (entry-target_px)
        if riskpx<=0 or rewardpx<=0:continue
        unitcost=riskpx/ticksize*tickvalue+cfg.commission
        if unitcost<=0:continue
        lots=math.floor((equity*cfg.risk/unitcost)/float(info.volume_step)+1e-9)*float(info.volume_step)
        lots=min(float(info.volume_max),lots)
        if lots<float(info.volume_min):continue
        exitprice=None;exitat=None;reason='TIME'; maxend=r.Index+pd.Timedelta(hours=cfg.max_hold)
        for j in range(ix,len(rows)):
            bar=rows[j]
            if bar.Index>maxend: break
            bspread=float(bar.spread)*point if float(bar.spread)>0 else cfg.spread
            stop_hit=(float(bar.low)<=stop if buy else float(bar.high)+bspread>=stop)
            tp_hit=(float(bar.high)>=target_px if buy else float(bar.low)+bspread<=target_px)
            if stop_hit or tp_hit:
                reason='STOP' if stop_hit else 'TARGET'
                exitprice=(stop if stop_hit else target_px)+(-cfg.slippage if buy else cfg.slippage)
                exitat=bar.Index+pd.Timedelta(minutes=5)
                break
            exitprice=float(bar.close)+(0 if buy else bspread)+(-cfg.slippage if buy else cfg.slippage)
            exitat=bar.Index+pd.Timedelta(minutes=5)
        if exitprice is None:continue
        pnl=((exitprice-entry) if buy else (entry-exitprice))/ticksize*tickvalue*lots-lots*cfg.commission
        before=equity;equity+=pnl;used_until=exitat
        ledger.append(dict(signal_time=str(t),entry_time=str(r.Index),exit_time=str(exitat),side=s['side'],
                           mode=s['mode'],confirmation=s['confirmation'],target_type=target,
                           entry=entry,stop=stop,target=target_px,exit=exitprice,lots=lots,
                           pnl=pnl,equity_before=before,equity_after=equity,reason=reason,depth=s['depth']))
        if equity<=0:break
    return ledger


def metrics(ledger,starting=10000):
    if not ledger:return {'trades':0,'return_pct':0.,'profit_factor':None,'win_rate_pct':None,'max_drawdown_pct':0.,'net_pnl':0.}
    p=np.array([r['pnl'] for r in ledger],float)
    gain=p[p>0].sum();loss=-p[p<0].sum()
    # separate fold initial equity; trades generated with fold equity
    eq=np.r_[starting,np.array([r['equity_after'] for r in ledger])]
    peak=np.maximum.accumulate(eq);dd=np.where(peak>0,(peak-eq)/peak,0)
    return {'trades':len(ledger),'return_pct':round(float(100*(eq[-1]/starting-1)),2),
            'profit_factor':round(float(gain/loss),3) if loss>0 else None,
            'win_rate_pct':round(float(100*(p>0).mean()),2),
            'max_drawdown_pct':round(float(100*dd.max()),2),'net_pnl':round(float(p.sum()),2)}


def run(cfg):
    out=Path(cfg.output).resolve();out.mkdir(parents=True,exist_ok=True)
    m5,mt5info=load_history(cfg.symbol,cfg.months,cfg.cache)
    info=info_specs(mt5info,cfg)
    # Don't assume timestamps are UTC: these are raw broker clock epochs.
    print('Loaded M5 bars:',len(m5),'broker-clock:',m5.index[0],m5.index[-1],flush=True)
    f=build_features(m5,cfg.months)
    if f.empty:raise RuntimeError('No eligible indicator snapshots')
    f.to_csv(out/'candidate_features.csv',index=False)
    end=m5.index[-1]
    start=end-pd.Timedelta(days=round(cfg.months*365.25/12))
    holdout_start=end-pd.Timedelta(days=60)
    development_end=holdout_start
    allcfg=[]
    for mode in MODES:
        for conf in CONFIRM:
            for depth in (0.1,0.3):
                for tgt in TARGETS:
                    allcfg.append((mode,conf,depth,tgt))
    # Cache signals once, not once per fold.
    cache={(mode,conf,depth):signal_events(f,mode,conf,depth) for mode in MODES for conf in CONFIRM for depth in (0.1,0.3)}
    # Chronological expanding-free rolling windows: 6m train, 2m test, 2m step, holdout final 2m.
    fold_bounds=[]; train=pd.Timedelta(days=180);test=pd.Timedelta(days=60)
    cursor=start+train
    while cursor+test<=development_end+pd.Timedelta(days=1):
        fold_bounds.append((cursor-train,cursor,cursor+test));cursor+=test
    if not fold_bounds:raise RuntimeError('Not enough history for walk-forward folds')
    fold_rows=[]; leaderboard=[]; holdout_rows=[]; selected=[]
    for mode in MODES:
        # Select confirmation/depth/target separately within each higher timeframe mode
        candidates=[x for x in allcfg if x[0]==mode]
        fold_selected=[]
        for fi,(trainstart,trainend,testend) in enumerate(fold_bounds):
            train_scores=[]
            for variant,conf,depth,target in candidates:
                trades=simulate(m5,cache[(variant,conf,depth)],info,cfg,target,trainstart,trainend)
                met=metrics(trades,cfg.equity)
                score=(met['net_pnl']/cfg.equity) - 0.5*met['max_drawdown_pct']/100 if met['trades']>=cfg.min_trades else -999.
                train_scores.append((score,variant,conf,depth,target,met['trades']))
            best=max(train_scores)
            _,var,conf,depth,target,ntrain=best
            test_trades=simulate(m5,cache[(var,conf,depth)],info,cfg,target,trainend,testend)
            met=metrics(test_trades,cfg.equity)
            fold_rows.append(dict(mode=mode,fold=fi+1,train_from=str(trainstart),train_to=str(trainend),test_to=str(testend),
                                  confirmation=conf,min_depth=depth,target=target,training_trades=ntrain,**met))
            fold_selected.append((var,conf,depth,target))
        # development-only pick by fold OOS net return; no holdout peeking
        devscores={c:sum(r['return_pct'] for r in fold_rows if r['mode']==mode and (r['mode'],r['confirmation'],r['min_depth'],r['target'])==c) for c in set(fold_selected)}
        selected_combo=max(devscores,key=devscores.get)
        var,conf,depth,target=selected_combo
        trades=simulate(m5,cache[(var,conf,depth)],info,cfg,target,holdout_start,end+pd.Timedelta(minutes=5))
        met=metrics(trades,cfg.equity)
        holdout_rows.append(dict(mode=mode,confirmation=conf,min_depth=depth,target=target,**met))
        selected.extend(trades)
    pd.DataFrame(fold_rows).to_csv(out/'walkforward_folds.csv',index=False)
    pd.DataFrame(holdout_rows).to_csv(out/'final_holdout.csv',index=False)
    pd.DataFrame(selected).to_csv(out/'holdout_trades.csv',index=False)
    # fixed baseline per mode: reentry depth 0.1, opposite BB, same holdout
    baselines=[]
    for mode in MODES:
        t=simulate(m5,cache[(mode,'reentry',0.1)],info,cfg,'opposite',holdout_start,end+pd.Timedelta(minutes=5))
        baselines.append(dict(mode=mode,**metrics(t,cfg.equity)))
    pd.DataFrame(baselines).to_csv(out/'holdout_baselines.csv',index=False)
    report={'symbol':cfg.symbol,'time_basis':'RAW MT5 BROKER CLOCK LABELS; historical DST/UTC unverified',
            'data':{'bars':len(m5),'first':str(m5.index[0]),'last':str(m5.index[-1])},
            'config':vars(cfg),'holdout_start':str(holdout_start),'holdout_results':holdout_rows,
            'warnings':['No tick-perfect execution: M5 OHLC and bar spread approximations; stop first on ties.',
                        'Current forming H1/H4 are computed using only M5 bars available at M15 close.',
                        'This tests completed M15 decisions, not sub-M15 live signals.',
                        'Training/rolling fold samples are insufficient if few signals; selection can overfit.',
                        'No swaps, funding, market impact or broker margin calls.',
                        'Historical broker-clock UTC/DST conversion not verified. No real MT5 orders.']}
    (out/'report.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print('Saved reports to:',out,flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbol',default='GOLD');p.add_argument('--months',type=int,default=12)
    p.add_argument('--cache',default='',help='Optional MT5 M5 CSV with raw time epoch values')
    p.add_argument('--output',default='gold_exhaustion_results')
    p.add_argument('--equity',type=float,default=10000);p.add_argument('--risk',type=float,default=.005)
    p.add_argument('--spread',type=float,default=.30);p.add_argument('--slippage',type=float,default=.10)
    p.add_argument('--commission',type=float,default=7.);p.add_argument('--stop-atr',type=float,default=.25)
    p.add_argument('--max-hold',type=float,default=48);p.add_argument('--min-trades',type=int,default=10)
    # These are used only when replaying a cached CSV: require user-supplied broker specs.
    p.add_argument('--tick-size',type=float,default=.01);p.add_argument('--tick-value',type=float,default=1.)
    p.add_argument('--point',type=float,default=.01);p.add_argument('--min-lot',type=float,default=.01)
    p.add_argument('--max-lot',type=float,default=10.);p.add_argument('--lot-step',type=float,default=.01)
    a=p.parse_args()
    if not (0<a.months<=36 and 0<a.risk<=.02 and a.equity>0 and a.spread>0 and a.slippage>=0 and a.min_trades>0):p.error('Invalid parameters')
    return a

if __name__=='__main__':run(parser())
