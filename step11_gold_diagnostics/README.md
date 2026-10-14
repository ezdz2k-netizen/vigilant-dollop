# Step 11 — GOLD Signal Diagnostics & Monitoring Health

This is a **drop-in replacement** for Step 10's `step10_gold_paper` folder. It is research-only: no `order_send`, no MT5 position modification, no real orders, and no OpenAI calls.

## Contents
- `paper_monitor.py`: complete Step 10 paper monitor with non-qualifying-candle diagnostics and heartbeat health.
- `signal_diagnostics.py`: new SQLite diagnostic tables and assessment based on the existing GOLD signal functions.
- `paper_dashboard.py`: updated Streamlit dashboard with monitoring health, reason counts, signal funnel, journals and virtual account information.
- `paper_core.py`, `gold_bb_reversal.py`, `gold_entry_comparison.py`: unchanged dependencies bundled for standalone use.
- `test_paper.py`, `test_step11.py`: offline regression/smoke tests.
- `requirements.txt`.

## Installation (Windows / VS Code)

1. Stop the running Step 10 monitor with **Ctrl+C**. Stop the existing dashboard Streamlit server.
2. Back up `C:\MT5_AI_Trader\step10_gold_paper\paper_trades.db` and the previous Step 10 folder.
3. Extract this ZIP's `step11_gold_diagnostics` folder to `C:\MT5_AI_Trader\`.
4. **To keep your existing virtual positions, copy the old `paper_trades.db` (plus its `-wal` and `-shm` files only if they exist) into the new `step11_gold_diagnostics` folder, with the monitor fully stopped.** Prefer an SQLite backup while DB is available if there were open writers. You can alternatively start a fresh test DB by not copying it. Do not run two monitors on the same DB.
5. In VS Code PowerShell:

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install -r step11_gold_diagnostics\requirements.txt
.\.venv\Scripts\python.exe step11_gold_diagnostics\test_paper.py
.\.venv\Scripts\python.exe step11_gold_diagnostics\test_step11.py
.\.venv\Scripts\python.exe -m py_compile step11_gold_diagnostics\paper_monitor.py
```

Start monitor:

```powershell
.\.venv\Scripts\python.exe step11_gold_diagnostics\paper_monitor.py --symbol GOLD --risk 0.005
```

Start dashboard in another terminal:

```powershell
.\.venv\Scripts\python.exe -m streamlit run step11_gold_diagnostics\paper_dashboard.py --server.port 8512
```

Visit `http://localhost:8512`.

## Interpret diagnostics

Each **newly observed, timely completed M15 candle** creates up to two diagnostic records: BUY and SELL. Checkpoints skip historic signals on startup and while offline. No trades are fabricated for missed candles. `h4_recent`/`h1_recent`/`m15_recent` indicate separately observed same-side sweep events; `aligned` evaluates their 12-hour window. `reentry`/`confirmation`/`structure`/`momentum` indicate whether each original entry engine generated a new signal at that M15 boundary. Counts are per *side-candle*, not trade counts and not a strictly descending funnel.

Historical time labels are broker-clock epoch labels; verified historical UTC/DST conversion is not available. Quote freshness comes from local UTC minus the detected current integer-hour offset, using a current tick. The dashboard's health age is the difference between wall-clock UTC and the last database health write.

## Limits

- No order submission; all positions are virtual, and the four strategies have independent simulated equity.
- Prices are sampled at 10-second intervals (configurable). An intrapoll stop or target touch may go unobserved.
- Positions use observed Bid/Ask, simulated slippage/commission, and account tick-value assumptions. The dashboard shows **realized balance only** until accurate unrealized valuation is separately validated.
- Signal “fail reasons” are preliminary funnel diagnostics: the underlying engine's event deduplication can suppress otherwise aligned events. The true qualification flags use the existing entry algorithm.
- Health database updates occur on successful loops. A terminal error can stall the heartbeat, which the dashboard flags.
- This package is syntax-checked and offline-tested, **not run against your FxPro terminal**. Verify live health and M15 evaluation in your environment.

## Safety review

Neither `paper_monitor.py` nor `paper_core.py` contain `order_send` or `TRADE_ACTION_DEAL`. The system connects to MT5 only to select a symbol, read prices, metadata and historical candles; it does not open, alter or close real positions. Its `open_paper` function only inserts into local SQLite.
