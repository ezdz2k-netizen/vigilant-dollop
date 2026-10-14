# GOLD Flexible Triple Bollinger Band – Walk-Forward Filter Research

This package extends the **existing** `gold_bb_reversal.py` and tests **Flexible + Opposite Bollinger Band**, not strict/sequential. It uses the prior event definition: H4, H1 and M15 wick sweeps in any order within 12 hours, M15 closing back inside its bands, then an opposite-band target.

## Files
- `gold_walkforward_optimizer.py` — read-only MT5 history download, indicators, filter grid, rolling training/test selection, untouched final holdout.
- `gold_bb_reversal.py` — included original research backtest helper, with caching of M5 rows for speed.
- `gold_walkforward_dashboard.py` — Streamlit research dashboard, reads output files only.
- `requirements.txt` — dependencies.

## Install (VS Code / Windows PowerShell)
Unzip these files into `C:\MT5_AI_Trader\gold_walkforward\`.

```powershell
cd C:\MT5_AI_Trader\gold_walkforward
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe gold_walkforward_optimizer.py --symbol GOLD --months 12 --risk 0.02
..\.venv\Scripts\python.exe -m streamlit run gold_walkforward_dashboard.py --server.port 8509
```

Then open `http://localhost:8509`.

## Filters being tested
- **ADX14 regime**: no restriction; ADX below 25; or ADX below 35.
- **M15 rejection wick**: minimum favorable wick-to-range ratio 0% or 30% on the *completed M15 re-entry candle*.
- **Breakout depth**: minimum sweep penetration beyond the **previous completed** outer BB as a fraction of M15 ATR (0 or 0.10).
- **ATR volatility**: unrestricted, or between the 10th and 90th percentiles **estimated from training signals only**.
- **Trading hours**: all hours, broker-clock 06:00–13:59, or broker-clock 14:00–21:59. These are **not verified UTC, London, or New York sessions**.

72 combinations per training window. Set `--max-grid 6` for a **smoke test only** (not valid model selection).

## Validation scheme
- 12-month research history; 6-month rolling training / 2-month rolling testing, stepped every 2 months; final 2 months kept completely outside rolling optimization.
- Score uses trade mean R, bounded profit factor term, and a small-sample penalty. At least 12 training trades required (override `--min-train-trades`).
- Final parameter selection uses the development set ending **before** final holdout. It is then compared against unfiltered Flexible + Opposite BB on the same final holdout.
- Base execution assumptions unchanged: 2% simulated risk, ATR stop buffer, opposite BB target fixed at signal, next M5 entry, broker-specific tick value/lot limits, spread, slippage, commissions, conservative stop-first tie-break.
- The last 48 hours of **each evaluation window** are excluded from entry to prevent the outcome leaking across train/test boundaries. Note: signals overlapping windows may be evaluated independently.

## Output files
`gold_walkforward_results/optimization_report.json`, `fold_selections.csv`, `training_grid.csv`, `rolling_oos_trades.csv`, `selected_holdout_trades.csv`, `unfiltered_holdout_trades.csv`.

## Important limitations
- This is a **filter study**, not live trading. No MT5 order-send calls, no OpenAI API calls.
- The prior project labels timestamps as **raw broker clock**. Historical timezone / DST conversion is not verified. Session filters must not be called UTC sessions.
- Five-minute bid OHLC plus spread approximates execution, but does not reconstruct intrabar fills. Stops, target execution and actual slippage may differ.
- A winning optimization is not proof of forward profitability; trading costs and multiple-testing effects still matter.
- Per-fold equity is restarted; the pooled walk-forward headline is a descriptive aggregation of independently simulated fold trades, **not a continuous tradable equity curve**.
