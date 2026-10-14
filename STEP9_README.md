# Step 9 — Strategy Diagnostics (read-only)

This package uses your **existing** `trades.csv` and `summary.json` only. It never connects to MetaTrader 5 or OpenAI, never edits your existing MT5 scripts, and never places orders.

## Setup (Windows PowerShell, in `C:\MT5_AI_Trader`)

1. Copy `strategy_diagnostics.py`, `step9_dashboard.py`, and `requirements_step9.txt` into your project directory.
2. Copy the original `trades.csv` and `summary.json` into `backtest_results/` if not already there.
3. Install: ` .\.venv\Scripts\python.exe -m pip install -r requirements_step9.txt `
4. Run: ` .\.venv\Scripts\python.exe strategy_diagnostics.py --trades backtest_results\trades.csv --summary backtest_results\summary.json --output step9_results `
5. Dashboard: ` .\.venv\Scripts\python.exe -m streamlit run step9_dashboard.py --server.port 8505 `
6. Open http://localhost:8505

If running with the included sample source files, point `--trades` and `--summary` to the package's `sample_input` directory instead.

## Output

- `step9_results/diagnostic_report.json`: metrics, grouped analyses, warnings and flags.
- `step9_results/enriched_trades.csv`: trade-level supplementary diagnostics.
- `step9_results/execution_flags.csv`: flagged transactions requiring manual inspection.

## Methodology limits

- **Regimes**: no OHLC/VIX/economic data was provided, so volatility is described using **ATR quartiles at entry**. This is not a full historical market-regime classifier.
- **Sessions**: UTC hour and UTC-clock buckets only. The broker's historical +3-hour adjustment has not been validated across DST.
- **Costs**: spread and commission are reported, not re-simulated. No counterfactual cost-free ledger or tick replay is available. Commission sums cannot simply be added back to infer true alternative execution performance.
- **Signal funnel**: candidate and confirmed counts are sourced from summary.json; rejection reasons are unavailable.
- **Sequencing**: identical entry/exit timestamps and overlaps are flagged; without tick data we cannot determine intrabar ordering.
- **Out-of-sample**: subgroup performance is descriptive, not optimized or independently validated.
