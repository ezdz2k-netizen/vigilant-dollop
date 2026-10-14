"""Step 14: persistent, MT5-read-only paper portfolio; no order API."""
import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

MODES=('H1','H4','BOTH')
POLICIES=('baseline','weak_be')

def utcnow():return datetime.now(timezone.utc).isoformat()
def connect(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(path),timeout=15)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=15000')
    db.executescript('''
CREATE TABLE IF NOT EXISTS portfolios(mode TEXT,policy TEXT,equity REAL,initial_equity REAL,PRIMARY KEY(mode,policy));
CREATE TABLE IF NOT EXISTS checkpoints(symbol TEXT PRIMARY KEY,last_boundary TEXT);
CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY,symbol TEXT,mode TEXT,policy TEXT,side TEXT,episode TEXT,
 signal_at TEXT,opened_at TEXT,entry_clock TEXT,entry REAL,initial_stop REAL,stop REAL,target REAL,atr REAL,
 lots REAL,tick_size REAL,tick_value REAL,commission_rt REAL,slippage REAL,be_pending INTEGER DEFAULT 0,
 momentum_checked INTEGER DEFAULT 0,first_full_boundary TEXT,status TEXT DEFAULT 'OPEN',
 closed_at TEXT,exit REAL,exit_reason TEXT,pnl REAL);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_per_portfolio ON positions(mode,policy) WHERE status='OPEN';
CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY,symbol TEXT,mode TEXT,side TEXT,signal_clock TEXT,
 episode TEXT,status TEXT,reason TEXT,UNIQUE(symbol,mode,side,signal_clock));
CREATE TABLE IF NOT EXISTS evaluations(id INTEGER PRIMARY KEY,symbol TEXT,boundary TEXT,observed_utc TEXT,
 timing TEXT,spread REAL,signals INTEGER,notes TEXT,UNIQUE(symbol,boundary));
CREATE TABLE IF NOT EXISTS quotes(id INTEGER PRIMARY KEY,at_utc TEXT,broker_clock TEXT,bid REAL,ask REAL,offset_hours INTEGER);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at_utc TEXT,kind TEXT,message TEXT);
CREATE TABLE IF NOT EXISTS ai_reviews(id INTEGER PRIMARY KEY,position_id INTEGER UNIQUE,created_at TEXT,model TEXT,
 review_json TEXT,error TEXT);
''')
    return db

def seed(db,initial):
    for m in MODES:
        for p in POLICIES:
            db.execute('INSERT OR IGNORE INTO portfolios VALUES(?,?,?,?)',(m,p,initial,initial))

def log(db,kind,message):db.execute('INSERT INTO events(at_utc,kind,message) VALUES(?,?,?)',(utcnow(),kind,str(message)[:1200]))

def specs(info):
    vals={k:float(getattr(info,k,0)) for k in ('trade_tick_size','trade_tick_value','volume_min','volume_max','volume_step')}
    if any(x<=0 for x in vals.values()):raise ValueError('Broker GOLD tick value, tick size or volume specifications invalid')
    return vals

def paper_open(db,s,mode,policy,bid,ask,broker_clock,info,risk=.005,slippage=.10,commission=7.,stop_buffer=.25):
    if db.execute("SELECT 1 FROM positions WHERE mode=? AND policy=? AND status='OPEN'",(mode,policy)).fetchone():return False,'already_open'
    v=specs(info);side=s['side'];buy=side=='BUY';atr=float(s['atr']);spr=ask-bid
    entry=(ask+slippage if buy else bid-slippage)
    stop=(float(s['sweep_low'])-stop_buffer*atr if buy else float(s['sweep_high'])+stop_buffer*atr+spr)
    target=float(s['middle']) if buy else float(s['middle'])+spr
    distance=(entry-stop if buy else stop-entry);reward=(target-entry if buy else entry-target)
    if distance<=0 or reward<=0:return False,'invalid_stop_target'
    equity=db.execute('SELECT equity FROM portfolios WHERE mode=? AND policy=?',(mode,policy)).fetchone()['equity']
    per_lot=distance/v['trade_tick_size']*v['trade_tick_value']+commission
    import math
    lots=math.floor((equity*risk/per_lot)/v['volume_step']+1e-8)*v['volume_step']
    lots=min(lots,v['volume_max'])
    if lots<v['volume_min']:return False,'volume_below_minimum'
    # First full completed M15 after entry; if partial at entry, skip that candle.
    import pandas as pd
    entry_time=pd.Timestamp(broker_clock)
    first_full=entry_time.ceil('15min')+pd.Timedelta(minutes=15)
    if entry_time==entry_time.floor('15min'):first_full=entry_time+pd.Timedelta(minutes=15)
    db.execute('''INSERT INTO positions(symbol,mode,policy,side,episode,signal_at,opened_at,entry_clock,entry,
      initial_stop,stop,target,atr,lots,tick_size,tick_value,commission_rt,slippage,first_full_boundary)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
      ('GOLD',mode,policy,side,str(s['episode']),str(s['time']),utcnow(),str(entry_time),entry,stop,stop,target,
       atr,lots,v['trade_tick_size'],v['trade_tick_value'],commission,slippage,str(first_full)))
    log(db,'PAPER_OPEN',f'{mode}/{policy} {side} entry={entry:.3f} stop={stop:.3f} target={target:.3f} lots={lots}')
    return True,'opened'

def close_position(db,p,bid,ask,reason):
    buy=p['side']=='BUY';exit_px=(bid-p['slippage'] if buy else ask+p['slippage'])
    pnl=(((exit_px-p['entry']) if buy else (p['entry']-exit_px))/p['tick_size']*p['tick_value']*p['lots']-p['commission_rt']*p['lots'])
    db.execute('UPDATE positions SET status=?,closed_at=?,exit=?,exit_reason=?,pnl=? WHERE id=?',('CLOSED',utcnow(),exit_px,reason,pnl,p['id']))
    db.execute('UPDATE portfolios SET equity=equity+? WHERE mode=? AND policy=?',(pnl,p['mode'],p['policy']))
    log(db,'PAPER_CLOSE',f"position {p['id']} {reason}: {pnl:.2f}")
    return pnl

def manage_quotes(db,bid,ask,max_hours=48):
    n=0
    for p in db.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall():
        buy=p['side']=='BUY'; px=bid if buy else ask
        stop_hit=px<=p['stop'] if buy else px>=p['stop'];target_hit=px>=p['target'] if buy else px<=p['target']
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(p['opened_at'])).total_seconds()
        if stop_hit or target_hit or age>max_hours*3600:
            close_position(db,p,bid,ask,'STOP' if stop_hit else ('TARGET' if target_hit else 'TIME'))
            n+=1
        elif p['policy']=='weak_be' and p['be_pending']:
            # Cost-adjusted BE price for observed quote, with roundtrip commission and exit slippage.
            extra=p['commission_rt']*p['tick_size']/p['tick_value']
            be=p['entry']+(extra+2*p['slippage'] if buy else -(extra+2*p['slippage']))
            if (buy and bid>be) or (not buy and ask<be):
                new_stop=max(p['stop'],be) if buy else min(p['stop'],be)
                db.execute('UPDATE positions SET stop=?,be_pending=0 WHERE id=?',(new_stop,p['id']))
                log(db,'BE_ARMED',f"position {p['id']} cost-adjusted BE stop={new_stop:.3f}")
    return n

def assess_first_full_bar(db,boundary,bar,body_threshold=.5,progress_threshold=.1):
    # bar: open/high/low/close of a complete M15 candle, only after the boundary exists.
    for p in db.execute("SELECT * FROM positions WHERE status='OPEN' AND policy='weak_be' AND momentum_checked=0").fetchall():
        if str(boundary)<p['first_full_boundary']:continue
        size=max(1e-9,float(bar['high'])-float(bar['low']))
        buy=p['side']=='BUY';delta=float(bar['close'])-float(bar['open'])
        directional=delta>0 if buy else delta<0
        progress=(float(bar['close'])-p['entry'] if buy else p['entry']-float(bar['close']))/max(p['atr'],1e-9)
        strong=directional and abs(delta)/size>=body_threshold and progress>=progress_threshold
        db.execute('UPDATE positions SET momentum_checked=1,be_pending=? WHERE id=?',(0 if strong else 1,p['id']))
        log(db,'MOMENTUM',f"position {p['id']} {'STRONG' if strong else 'WEAK_BE_PENDING'} progress_ATR={progress:.3f}")
