# XAUUSD Gold Research — installation and methodology

This package is **not** an already-optimized profitable gold strategy. It is a read-only research system that retrieves XAUUSD history from your own MT5 broker, searches a limited parameter grid on **8 training months**, then runs the selected parameter set on a **4-month holdout** that was not used for selection.

## Files
- `xau_optimizer.py` — gold-specific backtest and training-only search
- `xau_dashboard.py` — Streamlit results dashboard
- `requirements_xau.txt` — dependencies

## Installation
Copy these three files to `C:\MT5_AI_Trader`. Leave `monitor_m15.py`, `dual_ai_engine.py`, `indicators.py` and the EURUSD backtest unchanged.

In VS Code PowerShell:
```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install -r requirements_xau.txt
.\.venv\Scripts\python.exe xau_optimizer.py --symbol XAUUSD --months 12 --train-months 8 --risk 0.02 --strategy mtf
.\.venv\Scripts\python.exe -m streamlit run xau_dashboard.py --server.port 8506
```
If your broker uses `XAUUSD.a`, `GOLD`, etc., replace `XAUUSD` with the exact name visible in Market Watch.

Open http://localhost:8506. The optimizer output folder defaults to `xau_optimization_results` in the working directory. If the dashboard is placed inside the same project folder, its sidebar defaults to that folder.

## Defaults / parameters
- History: 12 months; training 8 months / untouched holdout 4 months.
- Risk: 2% **hypothetical equity**, compounded per completed trade; zero real MT5 orders.
- Starting equity: USD 10,000. Reject non-USD account currencies.
- Select gold volume using MT5 `trade_contract_size`, `volume_step`, `volume_min`, `volume_max`, `point`, `trade_tick_size` and related values.
- Stop: candidate ATR14 multiple of [1.5, 2.0], target: 2R or 3R; adjustable grid. These are research trials, not recommended execution values.
- Entry: earliest available M5 open at or after the M15 decision close. Uses bid-bar OHLC, adds spread on BUY entry, costs on SELL exits.
- Cost assumptions: maximum allowed spread **$0.70 per oz**, fallback to that if historical spread unavailable; slippage **$0.10 per oz each side**; USD 7 per lot roundtrip commission. **Verify broker terms**; this may not match your real contract / commission model.
- Stop-first if both stop and target are hit in one M5 candle. Intra-bar trajectory and fills unknown; stop prices during gaps may be optimistic.
- No swap, financing, margin rejection or news-event data.
- Indicator formulas use Wilder-style EWM and may differ somewhat from MT5 built-in ADX.
- Historical raw MT5 timestamps are treated as **broker-clock labels** consistently, NOT blindly corrected using today's +3-hour observed offset. Raw timestamps may need further DST investigation before UTC session analysis.
- This is only one train/holdout split. Walk-forward multiple splits, a larger untouched period, parameter stability analysis and tick-based execution testing are needed before claiming an edge.

## Safety
**No** `mt5.order_send`, **no** AI API calls, **no** live trading functionality. Historical-data calls can still require the MT5 desktop terminal and broker connection.

## Outputs
- `xau_optimization_results/training_grid.csv`
- `xau_optimization_results/training_trades.csv`
- `xau_optimization_results/holdout_trades.csv`
- `xau_optimization_results/optimization_report.json`
- `xau_optimization_results/history/m5_history.csv`, plus M15 and H1 histories

These files may be large. Keep them local and do not put account/API credentials inside them.
