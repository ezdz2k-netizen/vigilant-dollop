# Step 11.1 — GOLD paper trading diagnostics

**Read-only FxPro GOLD research. No MT5 order submission.** Four independent paper portfolios: reentry, confirmation, structure, momentum.

## Installation (Windows / VS Code)

1. Stop **both** the previous GOLD monitor and paper dashboard. Do not run two monitors writing the same database.
2. Back up `C:\MT5_AI_Trader\step11_gold_diagnostics\paper_trades.db` and your old Step 11 folder. If you copy the database, stop all writers first; preferably use SQLite's backup API to make a consistent copy.
3. Extract `step11_1_gold` into `C:\MT5_AI_Trader\step11_1_gold`.
4. To retain balances and open virtual positions, use a consistent backup of the old `paper_trades.db` as the new folder's `paper_trades.db`. If no database is copied, the script starts four **new** $10,000 virtual portfolios.
5. Install and test:

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install -r step11_1_gold\requirements.txt
.\.venv\Scripts\python.exe step11_1_gold\test_paper.py
.\.venv\Scripts\python.exe step11_1_gold\test_step11.py
.\.venv\Scripts\python.exe step11_1_gold\test_step11_1.py
.\.venv\Scripts\python.exe -m py_compile step11_1_gold\paper_monitor.py
```

Start monitor (terminal 1):

```powershell
.\.venv\Scripts\python.exe step11_1_gold\paper_monitor.py --symbol GOLD --risk 0.005
```

Start dashboard (terminal 2):

```powershell
.\.venv\Scripts\python.exe -m streamlit run step11_1_gold\paper_dashboard.py --server.port 8513
```

Open `http://localhost:8513`.

## What changed

- Each **new observed** M15 boundary is evaluated and logged even if signal lateness or spread makes a virtual entry ineligible.
- The SQLite `candle_observations` table logs `ELIGIBLE`, `DIAGNOSTIC_ONLY_LATE`, `DIAGNOSTIC_ONLY_SPREAD`, `NO_DATA`, or `INVALID_TIME` for the latest observed boundary. `candle_diagnostics` retains BUY and SELL conditions when sufficient history is available.
- Missed older M15 boundaries are **counted**, not replayed. No fictitious fills are created for missing market observations. Startup initializes a checkpoint without retroactive signals.
- Only `ELIGIBLE` boundaries may create virtual signals/positions; existing paper trade pricing, stops, targets and fees are unchanged.
- Journal observations and checkpoints commit together; repeating a boundary does not create duplicate observation or diagnostic records.
- Dashboard includes recent boundary counts, skip categories, missed-boundary counts and exports. Counters are based on loaded records (dashboard limits its query to 1,000 rows).

## Limitations

- Offline regression tests passed, but live FxPro runtime has **not** been tested here.
- Tick prices are sampled periodically; fills are not a broker-quality simulation and stops may be skipped between polls.
- All historical candles are treated as FxPro broker-clock labels, not verified DST-adjusted UTC.
- Current `monitor_health.last_evaluated_m15` represents the most recently **observed** M15 boundary and can advance before an actual diagnostic evaluation. Use `candle_observations` for actual recorded evaluations.
- `NO_DATA` means no diagnostic rows were produced; investigate market history alignment if persistent.
- No order_send, no actual order changes, no OpenAI API requests.
