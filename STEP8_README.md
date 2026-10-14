# MT5 Step 8 — Historical Strategy Backtesting

## What is included
- `backtest_mt5.py`: standalone historical EURUSD backtester; **never submits orders**.
- `backtest_dashboard.py`: offline Streamlit visualization of saved backtest results.
- `requirements_step8.txt`: Python dependencies.

This package is designed to sit alongside your existing `indicators.py`, `dual_ai_engine.py`, `multi_timeframe.py`, and `monitor_m15.py`. It imports **only** `indicators.py`; it does not change or launch any monitor/AI/trade execution code.

## Install in VS Code (PowerShell)
1. Extract files to `C:\MT5_AI_Trader`.
2. Ensure `indicators.py` is present in that folder.
3. Make sure the MetaTrader 5 desktop terminal is open and logged in; a **live account can supply read-only market data**, but no orders are sent.
4. In VS Code terminal:

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install -r requirements_step8.txt
.\.venv\Scripts\python.exe backtest_mt5.py --months 12 --risk 0.02 --rr 3 --initial-equity 10000
```

If your broker timestamps are still known to be 3 hours ahead of UTC and the automatic tick verification cannot run because the market is closed, use **only after checking the server offset**:

```powershell
.\.venv\Scripts\python.exe backtest_mt5.py --months 12 --risk 0.02 --rr 3 --offset-hours 3
```

The script saves `backtest_results/trades.csv` and `backtest_results/summary.json`. The original AI journal is untouched.

To open the dashboard:

```powershell
.\.venv\Scripts\python.exe -m streamlit run backtest_dashboard.py --server.port 8504
```

Visit `http://localhost:8504`.

## Underlying assumptions
- EURUSD only, hypothetical USD account, one standard lot approximately USD 10/pip. This must **not** be reused for other symbols or account currencies.
- Twelve calendar months backwards from execution date, plus 100 days of warmup history.
- Compare **baseline** (M15 Bollinger breakout rules) versus **MTF** (H1 trend and M5 entry timing confirmation) as implemented in the existing Step 7 project.
- Every M15 setup is detected at its **close**. H1 and M5 indicator data are limited to candles closed no later than that M15 close. **Entry is the next M5 bar open**, no earlier.
- Simulated 2% equity risk per accepted trade; stop = 1.5 × M15 ATR14; profit target = 3 × entry-to-stop distance.
- Simulated per-bar MT5 spread from historical `spread` if available; fallback 1.0 pip, configurable in `Config`. Assumes 5-digit EURUSD symbol quote with `point=0.00001`.
- Slippage fixed at 0.2 pip on entry and 0.2 pip on exit; USD 7 round-turn per standard lot commission. These are **estimates**, not verified broker execution costs.
- Long opens at synthetic ask and closes at synthetic bid. Short opens at bid and closes at synthetic ask. M5 bid OHLC plus spread approximates ask (not a tick-perfect reconstruction).
- If stop and target occur within one bar, **stop wins**. Real gap fills/latency may be worse than assumed.
- Only one position at a time **per strategy**. Position size rounded down to 0.01 lot and capped at 10 lots. Max holding time 48 hours.
- Drawdown uses closed-trade equity, not intratrade equity or margin requirements. No financing/swap, corporate-event adjustments, variable pip value, or margin call modelling.
- There is no synthetic AI historical analysis and no API charges. This measures indicator-based logic **only**.

## Important validation steps
1. Verify corrected M5, M15 and H1 timestamps against the actual MT5 chart and check that the offset remains stable across winter/summer DST changes. A single current +3-hour offset may be **wrong for some periods** in the 12-month sample if broker-server time changed seasonally. If so, build a dated offset mapping or read a reliable UTC-normalized data feed before trusting results.
2. Increase MT5 **Tools > Options > Charts > Max bars in chart** and ensure a full 12-month M5 history is downloaded. Limited available bars can invalidate sample coverage. Check any unexpected M5 intraday gaps.
3. Validate the entries/exits against MT5 Strategy Tester or a small hand-checked set, especially bid/ask spread and stop/TP boundaries.
4. Test on an **out-of-sample** date range and vary slippage/spread assumptions. Comparing/filtering strategies on the same period is not proof of an edge.
5. Keep the running monitor and dashboard independent of this module. **There is no order execution code.**

## Configuration
You can edit the `Config` class in `backtest_mt5.py` to change `atr_mult`, default spread, commission, slippage and maximum holding period. Risk and target are command-line options.

## Troubleshooting
- `No module named MetaTrader5`: use your project `.venv` interpreter.
- `No module named indicators`: place `indicators.py` next to `backtest_mt5.py`.
- `Insufficient M5 history`: load more MT5 bars; wait for data synchronization or use a separate verified historical dataset.
- `Cannot verify broker offset`: run with a fresh quote during market hours, or use a manually verified `--offset-hours` override.
- Very few trades: the strict BB-breakout + H1 trend + M5 momentum/volume conditions may be rare. Do not loosen rules just to increase trade counts without out-of-sample testing.
