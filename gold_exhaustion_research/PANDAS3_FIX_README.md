# V4 — pandas 3 datetime unit fix

Your Windows test showed pandas 3.0.6 and a mismatch in `DevelopingBandLookup.at()` where the reference returned a band but the accelerated code returned `None`.

Cause: pandas datetime index `.asi8` can return epoch **microseconds** for a `datetime64[us]` index, but the lookup compared those raw integers against `.value` and constants expressed in epoch **nanoseconds**. The mismatch rejects otherwise valid history.

Fix: explicitly convert both `blocks.index` and `m5.index` to nanosecond resolution before `.asi8`:

```python
self.block_times = blocks.index.as_unit("ns").asi8
self.m5_times = m5.index.as_unit("ns").asi8
```

The regression test now explicitly creates a microsecond-resolution index to mimic pandas 3 defaults, with weekend gaps. Other trading and research logic was not changed.

## Install

Stop the old test/backtest. Overwrite both `gold_exhaustion.py` and `test_fast_bands.py` in `C:\MT5_AI_Trader\gold_exhaustion_research\` from this ZIP. 

Run:

```powershell
.\.venv\Scripts\python.exe gold_exhaustion_research\test_fast_bands.py
.\.venv\Scripts\python.exe gold_exhaustion_research\test_exhaustion.py
```

Expected version `2026-10-13-pandas3-units-v4`, and both tests pass. Only then restart the backtest. No live orders are made.
