"""Read-only paper trading primitives: no MT5 trade imports or order submission."""
from __future__ import annotations
import json, math, sqlite3
from datetime import datetime, timezone
from pathlib import Path

STYLES=('reentry','confirmation','structure','momentum')
SCHEMA='''
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at_utc TEXT NOT NULL, kind TEXT NOT NULL, style TEXT, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints(symbol TEXT PRIMARY KEY, last_m15_close TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, style TEXT NOT NULL, side TEXT NOT NULL, signal_clock TEXT NOT NULL, recorded_at_utc TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, UNIQUE(symbol,style,side,signal_clock));
CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY, signal_id INTEGER NOT NULL UNIQUE, symbol TEXT NOT NULL, style TEXT NOT NULL, side TEXT NOT NULL, opened_utc TEXT NOT NULL, opened_clock TEXT NOT NULL, entry REAL NOT NULL, stop REAL NOT NULL, target REAL NOT NULL, lots REAL NOT NULL, risk_cash REAL NOT NULL, spread_entry REAL NOT NULL, entry_tick_bid REAL NOT NULL, entry_tick_ask REAL NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN', closed_utc TEXT, close_price REAL, exit_reason TEXT, gross_pnl REAL, commission REAL, net_pnl REAL, equity_after REAL);
CREATE TABLE IF NOT EXISTS marks(style TEXT PRIMARY KEY, equity REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ticks(id INTEGER PRIMARY KEY, at_utc TEXT NOT NULL, broker_clock TEXT NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_signal_clock ON signals(signal_clock);
'''

def now(): return datetime.now(timezone.utc).isoformat()
def connect(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(path),timeout=15)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(SCHEMA)
    return db

def audit(db,kind,msg,style=None):
    db.execute('INSERT INTO events(at_utc,kind,style,message) VALUES(?,?,?,?)',(now(),kind,style,str(msg)))

def seed(db,initial):
    for style in STYLES: db.execute('INSERT OR IGNORE INTO marks(style,equity) VALUES(?,?)',(style,float(initial)))

def volume_for_risk(risk_cash,entry,stop,info,commission_lot):
    size=float(info.trade_tick_size);value=float(info.trade_tick_value)
    mn=float(info.volume_min);mx=float(info.volume_max);step=float(info.volume_step)
    if min(size,value,mn,mx,step)<=0:raise ValueError('Unusable broker GOLD contract/tick/volume settings')
    perlot=abs(entry-stop)/size*value+commission_lot
    if perlot<=0:raise ValueError('Invalid risk per lot')
    raw=min(mx,risk_cash/perlot)
    lots=math.floor((raw+1e-9)/step)*step
    return round(lots,8) if lots>=mn else 0.

def open_paper(db,sid,style,side,signal,clock,bid,ask,info,params):
    if db.execute('SELECT 1 FROM positions WHERE status="OPEN" AND style=?',(style,)).fetchone():return False,'Existing virtual position'
    eq=float(db.execute('SELECT equity FROM marks WHERE style=?',(style,)).fetchone()[0]);risk_cash=eq*params['risk']
    slip=params['slippage'];entry=(ask+slip) if side=='BUY' else (bid-slip)
    sweep=float(signal['sweep_low'] if side=='BUY' else signal['sweep_high']);atr=float(signal['atr'])
    spread=ask-bid
    stop=(sweep-params['atr_buffer']*atr) if side=='BUY' else (sweep+params['atr_buffer']*atr+spread)
    target=float(signal['opposite'])+(spread if side=='SELL' else 0)
    if not math.isfinite(entry+stop+target) or (side=='BUY' and not stop<entry<target) or (side=='SELL' and not target<entry<stop):return False,'Invalid stop/target geometry'
    lots=volume_for_risk(risk_cash,entry,stop,info,params['commission'])
    if not lots:return False,'Volume below minimum / insufficient virtual equity'
    db.execute('INSERT INTO positions(signal_id,symbol,style,side,opened_utc,opened_clock,entry,stop,target,lots,risk_cash,spread_entry,entry_tick_bid,entry_tick_ask) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (sid,params['symbol'],style,side,now(),clock,entry,stop,target,lots,risk_cash,spread,bid,ask))
    audit(db,'OPEN',f'{side} {lots} lots @ {entry:.3f}; stop={stop:.3f}, target={target:.3f}',style)
    return True,'Opened virtual position'

def close_paper(db,p,bid,ask,info,params,reason,broker_clock):
    # BUY exits by selling at bid; SELL exits by buying at ask.
    exitprice=(bid-params['slippage']) if p['side']=='BUY' else (ask+params['slippage'])
    move=(exitprice-p['entry']) if p['side']=='BUY' else (p['entry']-exitprice)
    gross=move/float(info.trade_tick_size)*float(info.trade_tick_value)*p['lots']
    commission=p['lots']*params['commission'];net=gross-commission
    before=float(db.execute('SELECT equity FROM marks WHERE style=?',(p['style'],)).fetchone()[0]);after=before+net
    db.execute('UPDATE positions SET status="CLOSED",closed_utc=?,close_price=?,exit_reason=?,gross_pnl=?,commission=?,net_pnl=?,equity_after=? WHERE id=?',
        (now(),exitprice,reason,gross,commission,net,after,p['id']))
    db.execute('UPDATE marks SET equity=? WHERE style=?',(after,p['style']))
    audit(db,'CLOSE',f'{reason} exit @ {exitprice:.3f}; net={net:.2f}; equity={after:.2f}; tick={broker_clock}',p['style'])

def update_positions(db,bid,ask,info,params,clock):
    from datetime import timedelta
    t=datetime.fromisoformat(now())
    for p in db.execute('SELECT * FROM positions WHERE status="OPEN"').fetchall():
        hit_stop=(bid<=p['stop']) if p['side']=='BUY' else (ask>=p['stop'])
        hit_target=(bid>=p['target']) if p['side']=='BUY' else (ask<=p['target'])
        expired=t-datetime.fromisoformat(p['opened_utc'])>=timedelta(hours=params['max_hours'])
        if hit_stop or hit_target or expired:
            close_paper(db,p,bid,ask,info,params,'STOP' if hit_stop else ('TARGET' if hit_target else 'TIME'),clock)
