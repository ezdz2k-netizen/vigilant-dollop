"""Step 14 GOLD read-only MT5 market monitor. No order_send or trading operations."""
import argparse, sys, time, traceback
from pathlib import Path
from datetime import datetime,timezone,timedelta
import pandas as pd
from paper_core14 import connect,seed,log,paper_open,manage_quotes,assess_first_full_bar,utcnow,MODES,POLICIES
from step13_engine import create_episodes,make_entry_signals
import gold_exhaustion as base

ROOT=Path(__file__).resolve().parent

def parse():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--symbol',default='GOLD');p.add_argument('--db',default=str(ROOT/'step14_paper.db'))
 p.add_argument('--poll',type=int,default=10);p.add_argument('--risk',type=float,default=.005)
 p.add_argument('--history-bars',type=int,default=4800);p.add_argument('--max-signal-lag',type=int,default=90)
 p.add_argument('--max-spread',type=float,default=2.0);p.add_argument('--depth',type=float,default=.1)
 p.add_argument('--expiry',type=int,default=6)
 a=p.parse_args()
 if a.symbol!='GOLD':p.error('This package is GOLD-only; must use FxPro symbol GOLD')
 if not(5<=a.poll<=300 and 0<a.risk<=.02 and a.history_bars>=1800 and a.max_spread>0 and a.depth>=0 and a.expiry>0):p.error('Invalid settings')
 return a

def m15_full_bar(m5,boundary):
 # Three M5 candles forming the M15 candle immediately BEFORE this close boundary.
 parts=m5[(m5.index>=boundary-pd.Timedelta(minutes=15))&(m5.index<boundary)]
 if len(parts)!=3 or not all(parts.index==pd.date_range(boundary-pd.Timedelta(minutes=15),periods=3,freq='5min')):return None
 return {'open':float(parts.iloc[0]['open']),'high':float(parts.high.max()),'low':float(parts.low.min()),'close':float(parts.iloc[-1]['close'])}

def current_signals(m5,boundary,depth,expiry):
 # Construct all features causally using only completed M5 candles.
 features=base.build_features(m5)
 output=[];diagnostics=[]
 for mode in MODES:
  episodes,obs=create_episodes(features,mode,depth,expiry)
  signals=make_entry_signals(episodes,obs,'reentry','none')
  fresh=[s for s in signals if pd.Timestamp(s['time'])==boundary]
  output.extend((mode,s) for s in fresh)
  matching=features[features.time==boundary]
  diagnostics.append(f"{mode}: {len(fresh)} reentry signals, {len(matching)} side checks")
 return output,'; '.join(diagnostics)

def run():
 import MetaTrader5 as mt5
 a=parse();db=connect(a.db);seed(db,10000);db.commit()
 print(f'Step 14 GOLD shadow/paper monitor. Database: {a.db}. NO MT5 ORDERS. Ctrl+C stops.',flush=True)
 lastprint=0
 try:
  while True:
   try:
    if not mt5.initialize():raise RuntimeError(f'MT5 initialize failed: {mt5.last_error()}')
    try:
     if not mt5.symbol_select('GOLD',True):raise RuntimeError('FxPro GOLD symbol not available')
     tick=mt5.symbol_info_tick('GOLD');info=mt5.symbol_info('GOLD')
     if tick is None or info is None or tick.time<=0:raise RuntimeError('Invalid quote or specs')
     bid,ask=float(tick.bid),float(tick.ask)
     if bid<=0 or ask<=bid:raise RuntimeError('Invalid bid/ask')
     raw_tick=datetime.fromtimestamp(int(tick.time),timezone.utc)
     delta=(raw_tick-datetime.now(timezone.utc)).total_seconds();hours=round(delta/3600)
     residue=delta-hours*3600
     if abs(hours)>14 or abs(residue)>90:raise RuntimeError('Tick clock convention not verifiable')
     age=(datetime.now(timezone.utc)-(raw_tick-timedelta(hours=hours))).total_seconds()
     if not -10<=age<=90:raise RuntimeError(f'Stale quote {age:.1f}s')
     broker_clock=pd.Timestamp(raw_tick);boundary=broker_clock.floor('15min')
     db.execute('INSERT INTO quotes(at_utc,broker_clock,bid,ask,offset_hours) VALUES(?,?,?,?,?)',(utcnow(),str(broker_clock),bid,ask,hours))
     manage_quotes(db,bid,ask)
     prev=db.execute('SELECT last_boundary FROM checkpoints WHERE symbol=?',('GOLD',)).fetchone()
     if prev is None:
      db.execute('INSERT INTO checkpoints(symbol,last_boundary) VALUES(?,?)',('GOLD',str(boundary)))
      log(db,'STARTUP',f'No backfilled signals; next boundary after {boundary}')
     elif pd.Timestamp(prev['last_boundary'])<boundary:
      lag=(broker_clock-boundary).total_seconds();spread=ask-bid
      missed=max(0,int((boundary-pd.Timestamp(prev['last_boundary'])).total_seconds()//900)-1)
      if missed:log(db,'MISSED',f'{missed} missed M15 boundaries; no retroactive trades')
      rates=mt5.copy_rates_from_pos('GOLD',mt5.TIMEFRAME_M5,0,a.history_bars)
      if rates is None or len(rates)<1800:raise RuntimeError(f'Insufficient M5 data: {mt5.last_error()}')
      m5=pd.DataFrame(rates).drop_duplicates('time').sort_values('time')
      m5.index=pd.to_datetime(m5.time,unit='s',utc=True)
      m5=m5[(m5.index+pd.Timedelta(minutes=5))<=broker_clock]
      if len(m5)<1800:raise RuntimeError('Insufficient complete GOLD history')
      bar=m15_full_bar(m5,boundary)
      if bar is not None:assess_first_full_bar(db,boundary,bar)
      # Always record evaluation, even if too late to create a paper position.
      signals,details=current_signals(m5,boundary,a.depth,a.expiry)
      can_enter=lag<=a.max_signal_lag and spread<=a.max_spread and missed==0 and bar is not None
      status='ELIGIBLE' if can_enter else 'DIAGNOSTIC_ONLY'
      db.execute('INSERT OR IGNORE INTO evaluations(symbol,boundary,observed_utc,timing,spread,signals,notes) VALUES(?,?,?,?,?,?,?)',('GOLD',str(boundary),utcnow(),status,spread,len(signals),details))
      for mode,s in signals:
       db.execute('INSERT OR IGNORE INTO signals(symbol,mode,side,signal_clock,episode,status,reason) VALUES(?,?,?,?,?,?,?)',
                  ('GOLD',mode,s['side'],str(boundary),str(s['episode']),'PENDING' if can_enter else 'DIAGNOSTIC_ONLY',status))
       if can_enter:
        for policy in POLICIES:
         ok,reason=paper_open(db,s,mode,policy,bid,ask,broker_clock,info,risk=a.risk)
         if not ok:log(db,'ENTRY_SKIPPED',f'{mode}/{policy}: {reason}')
       db.execute("UPDATE signals SET status=?,reason=? WHERE symbol=? AND mode=? AND side=? AND signal_clock=?",
           ('PAPER_PROCESSED' if can_enter else 'DIAGNOSTIC_ONLY',status,'GOLD',mode,s['side'],str(boundary)))
      db.execute('UPDATE checkpoints SET last_boundary=? WHERE symbol=?',(str(boundary),'GOLD'))
      log(db,'EVALUATED',f'M15 {boundary} {status}, {len(signals)} setups, {details}')
     if time.monotonic()-lastprint>=60:
      open_count=db.execute("SELECT COUNT(*) FROM positions WHERE status='OPEN'").fetchone()[0]
      print(f'{utcnow()} GOLD {bid:.2f}/{ask:.2f} offset={hours:+}h open_paper={open_count}',flush=True)
      lastprint=time.monotonic()
     db.commit()
    finally:mt5.shutdown()
   except Exception as exc:
    db.rollback();print('Monitor error:',exc,flush=True)
    with db:log(db,'ERROR',str(exc))
   time.sleep(a.poll)
 except KeyboardInterrupt:print('Stopped. NO MT5 ORDERS.')
 finally:db.close()

if __name__=='__main__':run()
