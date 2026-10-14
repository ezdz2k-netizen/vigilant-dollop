"""FxPro GOLD Triple-BB forward PAPER monitor. NO order_send, read market only."""
import argparse, time, json
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
import MetaTrader5 as mt5
import pandas as pd
import gold_bb_reversal as base
import gold_entry_comparison as entry
from paper_core import connect,seed,audit,open_paper,update_positions,STYLES,now
from signal_diagnostics import initialize, evaluate, store, record_health

ROOT=Path(__file__).resolve().parent

def get_args():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--symbol',default='GOLD');p.add_argument('--db',default=str(ROOT/'paper_trades.db'))
 p.add_argument('--poll',type=int,default=10);p.add_argument('--risk',type=float,default=.005)
 p.add_argument('--initial-equity',type=float,default=10000)
 p.add_argument('--slippage',type=float,default=.10,help='Adverse GOLD price units per side')
 p.add_argument('--commission',type=float,default=7.,help='Account currency per standard lot roundtrip')
 p.add_argument('--atr-buffer',type=float,default=.25);p.add_argument('--max-hours',type=int,default=48)
 p.add_argument('--max-signal-lag',type=int,default=90,help='Maximum seconds since confirmed M15 close to accept virtual entry')
 p.add_argument('--max-spread',type=float,default=2.0,help='Maximum observed ask-bid in GOLD price units')
 p.add_argument('--history-bars',type=int,default=4800)
 a=p.parse_args()
 if not (5<=a.poll<=300 and 0<a.risk<=.02 and a.initial_equity>0 and a.slippage>=0 and a.commission>=0 and a.atr_buffer>=0 and a.max_hours>0 and 0<a.max_signal_lag<=300 and a.max_spread>0 and a.history_bars>=1800):p.error('Invalid settings')
 return a

def market(symbol,count):
 rates=mt5.copy_rates_from_pos(symbol,mt5.TIMEFRAME_M5,0,count)
 if rates is None or len(rates)<1800:raise RuntimeError(f'Insufficient GOLD M5 data: {mt5.last_error()}')
 frame=pd.DataFrame(rates).drop_duplicates('time').sort_values('time')
 frame.index=pd.to_datetime(frame.time,unit='s',utc=True)
 return frame

def build_signals(m5):
 m15=base.aggregate(m5,900);h1=base.aggregate(m5,3600);h4=base.aggregate(m5,14400)
 args=SimpleNamespace(window_hours=12,m15_reentry_bars=4,structure_lookback=3,confirmation_bars=3,momentum_body_min=.55,momentum_volume_mult=1.)
 flexible=base.select_signals(m15,h1,h4,args)['flexible']
 return entry.make_entries(flexible,m15,args),m15

def check():
 a=get_args();params={'symbol':a.symbol,'risk':a.risk,'slippage':a.slippage,'commission':a.commission,'atr_buffer':a.atr_buffer,'max_hours':a.max_hours}
 db=connect(a.db);seed(db,a.initial_equity);initialize(db);db.commit()
 print('PAPER-ONLY GOLD monitor. All four methods. No order placement. Ctrl+C stops.')
 print('Database:',a.db)
 last_message=0
 try:
  while True:
   try:
    if not mt5.initialize():raise RuntimeError(f'MT5 initialize: {mt5.last_error()}')
    try:
     if not mt5.symbol_select(a.symbol,True):raise RuntimeError(f'Symbol missing: {a.symbol}')
     info=mt5.symbol_info(a.symbol);tick=mt5.symbol_info_tick(a.symbol)
     if not info or not tick or not tick.time:raise RuntimeError('No GOLD quote/specification')
     bid=float(tick.bid);ask=float(tick.ask)
     if bid<=0 or ask<=bid:raise RuntimeError('Invalid bid/ask')
     # FxPro server's raw epoch labels may be offset from actual UTC. Determine current
     # integer-hour offset only from a fresh quote, and keep all *bar* comparisons in broker labels.
     raw_tick=datetime.fromtimestamp(int(tick.time),timezone.utc)
     delta=(raw_tick-datetime.now(timezone.utc)).total_seconds()
     hours=round(delta/3600);residual=delta-hours*3600
     if abs(hours)>14 or abs(residual)>90:raise RuntimeError(f'Tick stale / clock convention unclear: {delta:.1f}s')
     from datetime import timedelta
     tick_age=(datetime.now(timezone.utc)-(raw_tick-timedelta(hours=hours))).total_seconds()
     if not -10<=tick_age<=90:raise RuntimeError(f'Stale GOLD quote: {tick_age:.1f}s')
     tick_clock=pd.Timestamp(raw_tick)
     spread=ask-bid
     db.execute('INSERT INTO ticks(at_utc,broker_clock,bid,ask) VALUES(?,?,?,?)',(now(),str(tick_clock),bid,ask))
     update_positions(db,bid,ask,info,params,str(tick_clock))
     raw=market(a.symbol,a.history_bars)
     # Strictly exclude forming M5 candles relative to the fresh broker-clock tick.
     raw=raw[(raw.index+pd.Timedelta(minutes=5))<=tick_clock]
     if len(raw)<1800:raise RuntimeError('Not enough fully closed M5 candles')
     m15_clock=tick_clock.floor('15min')
     previous=db.execute('SELECT last_m15_close FROM checkpoints WHERE symbol=?',(a.symbol,)).fetchone()
     record_health(db,a.symbol,tick_clock,bid,ask,hours,tick_age,m15_clock)
     if previous is None:
      db.execute('INSERT INTO checkpoints(symbol,last_m15_close) VALUES(?,?)',(a.symbol,str(m15_clock)))
      audit(db,'STARTUP',f'Skipped historical signals; first eligible close is after {m15_clock}')
     elif pd.Timestamp(previous[0])<m15_clock:
      # If monitor was offline: no fabricated retroactive fills.
      lag=(tick_clock-m15_clock).total_seconds()
      if lag>a.max_signal_lag or spread>a.max_spread:
       audit(db,'SKIP',f'New M15 window: lag={lag:.1f}s, spread={spread:.3f}; no entries; no backfilled diagnostics')
      else:
       diagnostics, signals = evaluate(raw,m15_clock,a.symbol,hours)
       store(db,diagnostics)
       audit(db,'EVALUATED',f'M15={m15_clock}; diagnostic rows={len(diagnostics)}')
       for style in STYLES:
        # Only newly completed M15 signal matching latest M15 boundary.
        fresh=[s for s in signals[style] if pd.Timestamp(s['time'])==m15_clock]
        for s in fresh:
         clock=str(m15_clock);side=s['side']
         row=db.execute('SELECT id FROM signals WHERE symbol=? AND style=? AND side=? AND signal_clock=?',(a.symbol,style,side,clock)).fetchone()
         if row:continue
         db.execute('INSERT INTO signals(symbol,style,side,signal_clock,recorded_at_utc,status,detail) VALUES(?,?,?,?,?,?,?)',
          (a.symbol,style,side,clock,now(),'PENDING',json.dumps({'sweep_low':s['sweep_low'],'sweep_high':s['sweep_high'],'atr':s['atr'],'opposite':s['opposite']},default=str)))
         sid=db.execute('SELECT last_insert_rowid()').fetchone()[0]
         ok,reason=open_paper(db,sid,style,side,s,clock,bid,ask,info,params)
         db.execute('UPDATE signals SET status=? WHERE id=?',('OPENED' if ok else 'SKIPPED',sid))
         if not ok:audit(db,'REJECT',reason,style)
      db.execute('UPDATE checkpoints SET last_m15_close=? WHERE symbol=?',(str(m15_clock),a.symbol))
     if time.monotonic()-last_message>60:
      open_count=db.execute("SELECT COUNT(*) FROM positions WHERE status='OPEN'").fetchone()[0]
      print(f'{now()} {a.symbol} bid={bid:.3f} ask={ask:.3f} broker_offset={hours:+d}h; open_paper={open_count}')
      last_message=time.monotonic()
     db.commit()
    finally:mt5.shutdown()
   except Exception as e:
    db.rollback();print('Monitor error (retry):',e)
    with db:
     audit(db,'ERROR',str(e))
   time.sleep(a.poll)
 except KeyboardInterrupt:print('Paper monitor stopped. NO ORDERS EXECUTED.')
 finally:db.close()

if __name__=='__main__':check()
