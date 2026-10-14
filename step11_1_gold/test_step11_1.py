"""Offline Step 11.1 regression tests. No broker connection, API, or orders."""
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
from monitor_observations import (
    initialize_observations, record_observation, elapsed_boundaries,
    build_observation_status,
)
from paper_core import connect, seed
import sys, types
try:
 import MetaTrader5
except ImportError:
 sys.modules["MetaTrader5"] = types.ModuleType("MetaTrader5")
from signal_diagnostics import initialize,store


def test():
    assert elapsed_boundaries('2026-10-13 11:30:00+00:00', '2026-10-13 11:45:00+00:00') == 0
    assert elapsed_boundaries('2026-10-13 11:30:00+00:00', '2026-10-13 12:15:00+00:00') == 2
    for status,has_data,lag,spread in [
        ('ELIGIBLE',True,15,0.2),
        ('DIAGNOSTIC_ONLY_LATE',True,91,0.2),
        ('DIAGNOSTIC_ONLY_SPREAD',True,10,3.0),
        ('NO_DATA',False,10,0.2),
        ('INVALID_TIME',True,-2,0.2),
    ]:
        assert build_observation_status(has_data,lag,spread,90,2.0) == status
    with tempfile.TemporaryDirectory() as tmp:
        db = connect(Path(tmp)/'paper.db')
        seed(db,10000)
        initialize(db)
        initialize_observations(db)
        candle='2026-10-13 11:45:00+00:00'
        row={'symbol':'GOLD','candle_close':candle,'side':'BUY','h4_recent':0,'h1_recent':1,
             'm15_recent':0,'m15_inside':1,'aligned':0,'reentry':0,
             'confirmation':0,'structure':0,'momentum':0,
             'reason':'No recent H4 outer-band sweep','broker_offset_hours':3}
        store(db,[row])
        record_observation(db,'GOLD',candle,'DIAGNOSTIC_ONLY_LATE',120,.15,1,0,'no entry')
        store(db,[row])
        record_observation(db,'GOLD',candle,'DIAGNOSTIC_ONLY_LATE',120,.15,1,0,'duplicate')
        db.commit()
        assert db.execute('SELECT COUNT(*) FROM candle_diagnostics').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM candle_observations').fetchone()[0] == 1
        assert db.execute('SELECT status FROM candle_observations').fetchone()[0] == 'DIAGNOSTIC_ONLY_LATE'
        assert db.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 0
        db.close()
    print('Step 11.1 status, missed-boundary, SQLite migration and dedup tests PASSED.')
    print('No MT5 connection or orders.')

if __name__=='__main__': test()
