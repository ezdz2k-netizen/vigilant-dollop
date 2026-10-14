"""Step 11: observation-only GOLD M15 diagnostic persistence, never trades."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
import pandas as pd
import gold_bb_reversal as base
import gold_entry_comparison as entry
from paper_core import STYLES

SCHEMA = '''
CREATE TABLE IF NOT EXISTS candle_diagnostics (
 id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, candle_close TEXT NOT NULL,
 evaluated_utc TEXT NOT NULL, side TEXT NOT NULL,
 h4_recent INTEGER NOT NULL, h1_recent INTEGER NOT NULL,
 m15_recent INTEGER NOT NULL, m15_inside INTEGER NOT NULL,
 aligned INTEGER NOT NULL, reentry INTEGER NOT NULL,
 confirmation INTEGER NOT NULL, structure INTEGER NOT NULL, momentum INTEGER NOT NULL,
 reason TEXT NOT NULL, broker_offset_hours INTEGER,
 UNIQUE(symbol,candle_close,side)
);
CREATE INDEX IF NOT EXISTS idx_diag_candle ON candle_diagnostics(candle_close);
CREATE TABLE IF NOT EXISTS monitor_health (
 symbol TEXT PRIMARY KEY, observed_utc TEXT NOT NULL, tick_broker_clock TEXT NOT NULL,
 bid REAL NOT NULL, ask REAL NOT NULL, offset_hours INTEGER NOT NULL,
 tick_age_seconds REAL NOT NULL, spread REAL NOT NULL,
 last_evaluated_m15 TEXT, status TEXT NOT NULL
);
'''

def initialize(db):
 db.executescript(SCHEMA)

def record_health(db,symbol,clock,bid,ask,offset,tick_age,last_m15,status='OK'):
 db.execute('''INSERT INTO monitor_health(symbol,observed_utc,tick_broker_clock,bid,ask,
 offset_hours,tick_age_seconds,spread,last_evaluated_m15,status)
 VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET
 observed_utc=excluded.observed_utc,tick_broker_clock=excluded.tick_broker_clock,
 bid=excluded.bid,ask=excluded.ask,offset_hours=excluded.offset_hours,
 tick_age_seconds=excluded.tick_age_seconds,spread=excluded.spread,
 last_evaluated_m15=excluded.last_evaluated_m15,status=excluded.status''',
 (symbol,datetime.now(timezone.utc).isoformat(),str(clock),float(bid),float(ask),int(offset),float(tick_age),float(ask-bid),str(last_m15) if last_m15 is not None else None,status))

def evaluate(m5, boundary, symbol, offset_hours):
 """Evaluate only closed bars on a single broker-clock M15 boundary."""
 boundary=pd.Timestamp(boundary)
 m15=base.aggregate(m5,900)
 h1=base.aggregate(m5,3600)
 h4=base.aggregate(m5,14400)
 cfg=SimpleNamespace(window_hours=12,m15_reentry_bars=4,
                      structure_lookback=3,confirmation_bars=3,
                      momentum_body_min=.55,momentum_volume_mult=1.)
 relevant=m15[m15.close_time == boundary]
 if relevant.empty:
  return [],{k:[] for k in STYLES}
 event_args={}
 # Use identical definitions as the underlying signal generator.
 for side in ('BUY','SELL'):
  flag='buy_sweep' if side=='BUY' else 'sell_sweep'
  low=boundary-pd.Timedelta(hours=12)
  within={name: df[(df.close_time>=low)&(df.close_time<=boundary)&(df[flag])]
          for name,df in [('H4',h4),('H1',h1)]}
  lookback_start=boundary-pd.Timedelta(minutes=45)
  ms=m15[(m15.close_time>=lookback_start)&(m15.close_time<=boundary)&(m15[flag])]
  row=relevant.iloc[-1]
  inside=bool(row.lower<row.close<row.upper)
  aligned=False
  if len(ms) and len(within['H1']) and len(within['H4']):
   last_m=ms.iloc[-1].close_time
   for h4row in within['H4'].itertuples():
    for h1row in within['H1'].itertuples():
     if max(h4row.close_time,h1row.close_time,last_m)-min(h4row.close_time,h1row.close_time,last_m)<=pd.Timedelta(hours=12):
      aligned=True;break
    if aligned:break
  event_args[side]={'h4_recent':bool(len(within['H4'])),'h1_recent':bool(len(within['H1'])),
         'm15_recent':bool(len(ms)),'m15_inside':inside,'aligned':aligned}
 # All candidate styles evaluated from same library as monitor.
 flexible=base.select_signals(m15,h1,h4,cfg)['flexible']
 signals=entry.make_entries(flexible,m15,cfg)
 entries={s:{str(x['time']):[x for x in signals[s] if pd.Timestamp(x['time'])==boundary] for x in signals[s] if pd.Timestamp(x['time'])==boundary} for s in STYLES}
 result=[]
 for side in ('BUY','SELL'):
  flags=event_args[side]
  pass_styles={style:any(x['side']==side and pd.Timestamp(x['time'])==boundary for x in signals[style]) for style in STYLES}
  reason=next((label for cond,label in [
    (not flags['h4_recent'],'No recent H4 outer-band sweep'),
    (not flags['h1_recent'],'No recent H1 outer-band sweep'),
    (not flags['m15_recent'],'No recent M15 outer-band sweep'),
    (not flags['m15_inside'],'M15 close outside BB'),
    (not flags['aligned'],'Breaches not aligned within 12h'),
    (not pass_styles['reentry'],'No new qualifying reentry event'),
   ] if cond),'Reentry qualified; awaiting or checking confirmations')
  result.append(dict(symbol=symbol,candle_close=str(boundary),side=side,**{k:int(v) for k,v in flags.items()},
      **{k:int(v) for k,v in pass_styles.items()},reason=reason,broker_offset_hours=int(offset_hours)))
 return result,signals

def store(db,records):
 for r in records:
  db.execute('''INSERT OR IGNORE INTO candle_diagnostics
  (symbol,candle_close,evaluated_utc,side,h4_recent,h1_recent,m15_recent,m15_inside,aligned,
   reentry,confirmation,structure,momentum,reason,broker_offset_hours)
  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
  (r['symbol'],r['candle_close'],datetime.now(timezone.utc).isoformat(),r['side'],
   r['h4_recent'],r['h1_recent'],r['m15_recent'],r['m15_inside'],r['aligned'],
   r['reentry'],r['confirmation'],r['structure'],r['momentum'],r['reason'],r['broker_offset_hours']))
