from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from paper_core import connect,seed,open_paper,update_positions

def run():
 with TemporaryDirectory() as t:
  db=connect(Path(t)/'paper.db');seed(db,10000)
  broker=SimpleNamespace(trade_tick_size=.01,trade_tick_value=1.,volume_min=.01,volume_max=100.,volume_step=.01)
  cfg=dict(risk=.005,slippage=.10,commission=7.,atr_buffer=.25,symbol='GOLD',max_hours=48)
  db.execute("INSERT INTO signals(symbol,style,side,signal_clock,recorded_at_utc,status) VALUES('GOLD','reentry','BUY','2026-10-13 12:00','2026-10-13T04:00+00:00','PENDING')")
  good,reason=open_paper(db,1,'reentry','BUY',dict(sweep_low=2990,sweep_high=3000,atr=2,opposite=3020),'2026-10-13 12:00',3000,3000.3,broker,cfg)
  assert good,reason
  assert db.execute('SELECT COUNT(*) FROM positions WHERE status="OPEN"').fetchone()[0]==1
  good,_=open_paper(db,1,'reentry','BUY',dict(sweep_low=2990,sweep_high=3000,atr=2,opposite=3020),'2026-10-13 12:00',3000,3000.3,broker,cfg)
  assert not good
  update_positions(db,3021,3021.3,broker,cfg,'2026-10-13 12:10')
  assert db.execute('SELECT COUNT(*) FROM positions WHERE status="CLOSED"').fetchone()[0]==1
  assert float(db.execute('SELECT equity FROM marks WHERE style="reentry"').fetchone()[0])>10000
  db.commit();db.close()
 print('Paper account, duplicate-position, and target-exit tests PASSED. No MT5 connection or orders.')
if __name__=='__main__':run()
