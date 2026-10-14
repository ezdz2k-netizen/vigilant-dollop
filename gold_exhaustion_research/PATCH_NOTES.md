# Fast H1/H4 Bollinger Lookup: Parity Patch

Your original three unit tests passed, but the additional weekend-gap parity test reported an H1 mismatch. The backtest should not be relied on until this test passes on your Windows/Python environment.

This replacement `gold_exhaustion.py` replaces cached rolling sum-of-squares lookup with direct access to the previous 19 complete and contiguous H1/H4 bars. It calculates bands using the same `numpy.mean` and `numpy.std(ddof=0)` as the slow reference, avoiding the former rolling-cache equivalence issue.

## Replace and test

Copy the replacement `gold_exhaustion.py` to `C:\MT5_AI_Trader\gold_exhaustion_research\gold_exhaustion.py`, retaining your previous file as a backup.

```powershell
.\.venv\Scripts\python.exe gold_exhaustion_research\test_exhaustion.py
.\.venv\Scripts\python.exe gold_exhaustion_research\test_fast_bands.py
```

Run the full backtest **only after both tests pass**:

```powershell
.\.venv\Scripts\python.exe gold_exhaustion_research\gold_exhaustion.py --symbol GOLD --months 12 --output gold_exhaustion_research\gold_exhaustion_results
```

This code does not place MT5 orders. The full-year run still requires live-terminal verification and is not evidence of profitable trading.
