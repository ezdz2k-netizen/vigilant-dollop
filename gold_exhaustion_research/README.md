# FxPro GOLD — H1/H4 Developing Bollinger Reversal Research

## Safety
Research only. `gold_exhaustion.py` retrieves historical M5 candles and reads GOLD instrument specifications; it NEVER places or modifies MT5 orders. Run separately from your working Step 11.1 paper monitor. It does not overwrite that system.

## What is tested
- H1 live/developing outer-band extension; H4 live/developing extension; BOTH simultaneously.
- Developing H1/H4 BB(20,2) is reconstructed at each completed M15 boundary from 19 earlier full HTF closes **plus the current developing close**. No final value of a not-yet-closed HTF bar is used.
- **Mandatory** M15 completed outside close: depth `(close - upper)/ATR14` for potential SELL or `(lower-close)/ATR14` for BUY. Compare minimum depth 0.10 versus 0.30 ATR. These are experimental, not proven thresholds.
- State machine: arm on a strong M15 outside close plus applicable HTF extension, wait up to 6 M15 candles. If M15 returns inside and BOTH H1/H4 live prices are back inside, invalidate. A fresh strong outside close is required to re-arm.
- Entry confirmation methods: re-entry; causally available RSI14 divergence proxy; falling ADX14 + weakening dominant DI; three-bar market structure break (CHOCH proxy); recent-high/low rejection (liquidity sweep proxy). These are **algorithmic proxies**, not confirmed discretionary chart structures or actual order flow.
- Target middle or opposite M15 BB, fixed at confirmation. Stop past observed sweep high/low plus 0.25 ATR. Next M5 open entry, M5 stop-first conservative tie-break, account specification-based risk sizing.
- Rolling 6-month training and 2-month test windows where history permits, followed by final untouched 60-day holdout; per-mode candidate selection using only development windows. Baseline each mode: reentry, 0.10 depth, opposite target.

## Installation / Windows VS Code
Extract the folder into `C:\MT5_AI_Trader\gold_exhaustion_research`.

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install -r gold_exhaustion_research\requirements.txt
.\.venv\Scripts\python.exe gold_exhaustion_research\test_exhaustion.py
.\.venv\Scripts\python.exe gold_exhaustion_research\gold_exhaustion.py --symbol GOLD --months 12 --output gold_exhaustion_research\gold_exhaustion_results
.\.venv\Scripts\python.exe -m streamlit run gold_exhaustion_research\exhaustion_dashboard.py --server.port 8514
```

Open `http://localhost:8514`.

To use a previously exported MT5 M5 CSV without connecting to MT5:
`python gold_exhaustion.py --cache path\to\m5_history_raw.csv --tick-size <value> --tick-value <value> --point <value>` **with broker-confirmed GOLD instrument specifications**. Default simulated tick/contract values are placeholders for offline testing only, not verified FxPro settings.

## Warnings and limitations
- Broker epoch labels appear +3h from wall-clock UTC in your earlier tests, but **historical DST correction has NOT been verified**. Results retain raw broker clock labels; sessions should not be interpreted as UTC.
- Historical M5 bar data approximates bid/ask fills; actual intra-bar paths, stop gaps, spread spikes, swap, margin and tick execution are not modeled. Historical comparison is provisional.
- **Critical comparison caveat:** Train/test/holdout splits are implemented as signal-time boundaries; positions nearing the end of a training window are excluded so exits cannot leak forward. Results may exclude boundary trades.
- Indicators represent quantitative research proxies; RSI divergence requires no future centered pivots; CHOCH uses a previous three-bar extreme, not a full discretionary swing-state engine.
- H1/H4 *price at the M15 decision point* is their developing partial close. For tick-by-tick H1/H4 outside price before M15 closes, full tick replay would be a distinct experiment.
- No model here forecasts a reliable reversal probability. Depth categories and confirmations must be validated across additional unseen data and forward paper records before decisions.

## Performance fix
The older version repeatedly scanned/resampled M5 history for every M15 bar. This version precomputes complete HTF blocks once and uses index lookups. Test with `test_fast_bands.py`; no changes to trading or risk assumptions.
