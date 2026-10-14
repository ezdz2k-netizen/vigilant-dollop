"""Step 11.1 persisted M15 observation statuses. No MT5 access or trading."""
from datetime import datetime, timezone
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS candle_observations (
 id INTEGER PRIMARY KEY,
 symbol TEXT NOT NULL,
 candle_close TEXT NOT NULL,
 recorded_utc TEXT NOT NULL,
 status TEXT NOT NULL,
 lag_seconds REAL NOT NULL,
 spread_price REAL NOT NULL,
 diagnostic_rows INTEGER NOT NULL,
 missed_boundaries INTEGER NOT NULL DEFAULT 0,
 note TEXT,
 UNIQUE(symbol,candle_close)
);
CREATE INDEX IF NOT EXISTS idx_observation_close ON candle_observations(symbol,candle_close);
"""

def initialize_observations(db):
 db.executescript(SCHEMA)

def elapsed_boundaries(previous, latest):
 previous=pd.Timestamp(previous); latest=pd.Timestamp(latest)
 gap=(latest-previous).total_seconds()
 if gap<0:raise ValueError('M15 timestamp went backwards; refusing checkpoint advancement')
 if gap % 900:raise ValueError('M15 boundaries misaligned; refusing checkpoint advancement')
 return max(0,int(gap // 900)-1)

def build_observation_status(has_diagnostics,lag,spread,max_lag,max_spread):
 if not has_diagnostics:return 'NO_DATA'
 if lag<0:return 'INVALID_TIME'
 if lag>max_lag:return 'DIAGNOSTIC_ONLY_LATE'
 if spread>max_spread:return 'DIAGNOSTIC_ONLY_SPREAD'
 return 'ELIGIBLE'

def record_observation(db,symbol,candle,status,lag,spread,row_count,missed,note=''):
 db.execute("""INSERT OR IGNORE INTO candle_observations
 (symbol,candle_close,recorded_utc,status,lag_seconds,spread_price,diagnostic_rows,missed_boundaries,note)
 VALUES(?,?,?,?,?,?,?,?,?)""",
 (symbol,candle,datetime.now(timezone.utc).isoformat(),status,float(lag),float(spread),int(row_count),int(missed),note))
