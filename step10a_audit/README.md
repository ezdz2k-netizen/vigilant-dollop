# MT5 Step 10A — GOLD Triple Bollinger execution audit

## What is included

- `step10a_audit.py`: offline trade-ledger and source-level audit, with flagged rows and post-hoc execution-cost sensitivity (does not make trades or query MT5).
- `test_audit.py`: regression test for entry chronology and ledger reconciliation.
- `step10a_results/audit_report.md`: findings from the user's uploaded holdout and rolling-OOS trade records and supplied backtester source.
- `step10a_results/audit_report.json`, `audit_flags.csv`, `trade_style_summary.csv`, `cost_sensitivity.csv`: supporting machine-readable reports.
- `source_reference/`: copies of the exact existing GOLD research scripts and inputs used for this inspection. These references are not modified or executed.

## Installation and running in VS Code

Unzip into `C:\MT5_AI_Trader\step10a_audit`. It will not overwrite existing production scripts. From `C:\MT5_AI_Trader`:

```powershell
.\.venv\Scripts\python.exe -m pip install pandas numpy
.\.venv\Scripts\python.exe step10a_audit\step10a_audit.py --holdout step10a_audit\source_reference\holdout_trades.csv --rolling step10a_audit\source_reference\rolling_oos_trades.csv --entry-report step10a_audit\source_reference\entry_report.json --entry-source step10a_audit\source_reference\gold_entry_comparison.py --base-source step10a_audit\source_reference\gold_bb_reversal.py --output step10a_audit\step10a_results
.\.venv\Scripts\python.exe step10a_audit\test_audit.py
```

Open `step10a_audit\step10a_results\audit_report.md` in VS Code or `audit_report.json` for automated checks.

## Audit boundaries

This performs **offline proof of basic chronology and arithmetic** and **source-code pattern inspection**, not historical tick replay. It cannot prove actual bid/ask fills, absent look-ahead in every dependency, broker session time/DST, margin or exact GOLD contract specifications. All trade executions remain simulated. No MT5 order API calls, credentials or OpenAI calls.

The holdout CSV contains four *alternative* backtested methods. Do not add their dollars together as though they were one portfolio. The bundled post-hoc cost shock does not rerun entries, exits, compounding or margin.

## Next engineering work

1. Correct `window_trades()` period labels by setting each output row's `period` from the caller's actual holdout/fold boundary.
2. Record `signal_observed_at`, selected M5 entry-bar OHLC and spread, actual quote used, stop/target hit flags, and stop/target same-bar conflicts per trade.
3. Re-run a bounded sample with real MT5 tick history and compare bid/ask fills, sequence of stop/target hits and gap opening prices.
4. Compare reconstructed H1/H4 bars with native broker bars at daylight-saving transitions.
5. Only after the audit passes, recalculate the out-of-sample and forward paper performance; do not enable live trading.
