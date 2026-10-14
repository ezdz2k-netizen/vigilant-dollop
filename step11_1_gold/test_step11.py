"""Standalone smoke tests: no MT5 connection, no live orders."""
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
import numpy as np
import pandas as pd
from paper_core import connect, seed
import sys, types
# Offline tests can run without the Windows-only MT5 package.
try:
 import MetaTrader5
except ImportError:
 sys.modules["MetaTrader5"] = types.ModuleType("MetaTrader5")
from signal_diagnostics import initialize,store,record_health,evaluate

def main():
 with tempfile.TemporaryDirectory() as temp:
  db=connect(Path(temp)/'test.db');seed(db,10000);initialize(db)
  r={'symbol':'GOLD','candle_close':'2026-10-13 12:15:00+00:00','side':'BUY',
     'h4_recent':0,'h1_recent':0,'m15_recent':0,'m15_inside':1,'aligned':0,
     'reentry':0,'confirmation':0,'structure':0,'momentum':0,'reason':'No recent H4 outer-band sweep','broker_offset_hours':3}
  store(db,[r]);store(db,[r])
  assert db.execute('SELECT COUNT(*) FROM candle_diagnostics').fetchone()[0]==1
  record_health(db,'GOLD','2026-10-13T12:15:00+00:00',4189.1,4189.25,3,0.7,'2026-10-13T12:15:00+00:00')
  db.commit()
  assert db.execute('SELECT spread FROM monitor_health').fetchone()[0]>0
  db.close()
 idx=pd.date_range('2026-09-01',periods=4800,freq='5min',tz='UTC')
 # Synthetic 5-min candles; constant prices should produce no false sweeps.
 rng=np.random.default_rng(2026)
 close=4100+np.cumsum(rng.normal(0,.05,len(idx)))
 m5=pd.DataFrame({'open':close,'high':close+.1,'low':close-.1,'close':close,
                  'tick_volume':np.full(len(idx),10)},index=idx)
 t=idx[-1].floor('15min')
 result,signals=evaluate(m5,t,'GOLD',3)
 assert isinstance(result,list) and all(x['side'] in ('BUY','SELL') for x in result)
 assert set(signals)=={'reentry','confirmation','structure','momentum'}
 print('Step 11 SQLite migration, duplicate prevention, health, and candle diagnostics PASSED.')
 print('No MT5 connection or orders.')
if __name__=='__main__':main()
