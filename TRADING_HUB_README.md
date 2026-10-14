# MT5 Trading Hub — FxPro symbols

## What this adds

A single Streamlit dashboard (`trading_hub.py`) with navigation between Live Market and Backtesting, and a dropdown for `BITCOIN`, `GOLD`, `#US30` (exact FxPro Market Watch names). Live charts show completed candles with BB(20,2), EMA50, ADX/+DI/-DI and tick volume, plus multi-timeframe H1/M15/M5 research analysis. Historical results are loaded **only if the saved report's symbol matches the selected symbol**.

This is a read-only view. It contains no trade-order submission function. Changing the dropdown DOES NOT change your existing monitor's `SYMBOL` configuration.

## Installation

1. Copy `trading_hub.py` to `C:\MT5_AI_Trader` alongside `indicators.py` and `multi_timeframe.py`.
2. In VS Code PowerShell:

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install streamlit plotly pandas numpy MetaTrader5
.\.venv\Scripts\python.exe -m streamlit run trading_hub.py --server.port 8507
```

3. Open `http://localhost:8507`. Switch pages using **Dashboard** in the sidebar, or the deep links:

- http://localhost:8507/?view=live&symbol=GOLD
- http://localhost:8507/?view=backtest&symbol=GOLD
- http://localhost:8507/?view=live&symbol=BITCOIN
- http://localhost:8507/?view=live&symbol=%23US30

## Backtest data

- The existing Step 8 `backtest_results/summary.json` and `trades.csv` are shown only if `summary.json` explicitly identifies the selected symbol.
- Per-asset backtest folders can be named `backtest_results_bitcoin`, `backtest_results_gold`, or `backtest_results_us30` (with corresponding `summary.json`/`trades.csv`).
- FxPro GOLD optimizer reports can be read at `xau_optimization_results/optimization_report.json` or `xauusd_optimization/xau_optimization_results/optimization_report.json`. The optimizer writes them after you run it. The report must identify symbol `GOLD`.
- BITCOIN and #US30 require broker-aware backtest calculations; do not repurpose EURUSD's fixed pip value, FX spread or lot assumptions.

## Limitations

- The current timestamp adjustment compares the latest tick with your PC UTC clock. It refuses stale/uncertain quotes. Historical DST/time correctness remains to be verified.
- MTF default indicator thresholds are not optimized for Bitcoin or US30.
- The chart page polls every 10 seconds and your existing M15 monitor operates separately. Changing symbols in the hub does not activate symbol monitoring or trading.
- If the broker symbols use suffixes or unusual names, verify them in Market Watch. This package intentionally uses the three exact names requested.
