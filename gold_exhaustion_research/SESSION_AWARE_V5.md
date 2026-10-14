# GOLD Exhaustion V5 — Session-aware H1/H4 Band Reconstruction

Your diagnostic found that many FxPro GOLD H4 bars have 36 rather than 48 constituent M5 candles because the instrument trades with daily breaks. The previous H4 lookup required 19 consecutive 48-bar H4 blocks and therefore returned no values for the tested 14 days.

V5 defines a higher timeframe trading bar as any nonempty observed broker-clock H1/H4 calendar block. At each completed M15 decision boundary it combines the 19 most recent *previous* observed trading-block closes with the last available M5 close from the developing H1/H4 bar. It requires the latest M5 bar to close exactly at the decision boundary and does not fill prices across trading gaps.

This is a **semantic change**, not merely a performance repair. The two lookup implementations have matching calculations, but whether these reconstructed bars exactly match broker native GOLD H4 chart candles has not been verified. Validate broker H1/H4 bar alignment and chart-session anchoring before interpreting performance.

V5 preserves the backtesting risk and read-only behavior. Keep the earlier script as a backup.

Install the whole `gold_exhaustion_research` folder into `C:\MT5_AI_Trader`. From there run:

```powershell
.\.venv\Scripts\python.exe gold_exhaustion_research\test_exhaustion.py
.\.venv\Scripts\python.exe gold_exhaustion_research\test_fast_bands.py
.\.venv\Scripts\python.exe gold_exhaustion_research\test_session_bands.py
.\.venv\Scripts\python.exe gold_exhaustion_research\gold_exhaustion.py --symbol GOLD --months 12 --output gold_exhaustion_research\gold_exhaustion_results
```

The `build_features` function now raises a descriptive error when there are zero records instead of a pandas `KeyError: time`.
