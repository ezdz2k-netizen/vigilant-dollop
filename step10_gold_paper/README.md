# Step 10 — GOLD Triple Bollinger Band forward paper-trading monitor

**Research-only. No MT5 trading requests or orders.** The monitor reads FxPro `GOLD` quotes and historical M5 candles; it tracks four independent virtual accounts in SQLite. It never modifies your live MT5 monitor, API settings, or original entry backtester.

## Install (Windows PowerShell)

Extract the `step10_gold_paper` folder next to your existing `.venv` inside `C:\MT5_AI_Trader`.

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install -r step10_gold_paper\requirements.txt
.\.venv\Scripts\python.exe step10_gold_paper\test_paper.py
.\.venv\Scripts\python.exe step10_gold_paper\paper_monitor.py --symbol GOLD --risk 0.005
```

In a **second terminal**:

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m streamlit run step10_gold_paper\paper_dashboard.py --server.port 8511
```

Open http://localhost:8511 in your browser. Press Ctrl+C in the monitor terminal to stop. Restarting resumes recorded state; it **does not backfill or invent entries** missed while offline.

## Strategies

A. `reentry`: M15 closes back inside its band after a flexible H4/H1/M15 outer-band sweep.
B. `confirmation`: a later completed M15 reversal-direction confirmation candle.
C. `structure`: subsequent completed M15 close breaks recent M15 structure.
D. `momentum`: later completed M15 candle has directional body, EMA20 alignment, and volume confirmation.

Signal definitions are imported from the bundled **original** `gold_bb_reversal.py` and `gold_entry_comparison.py` for reproducibility. All H1/H4 events are observed only after their corresponding candle is complete. All M5 input bars are completed relative to a fresh MT5 tick. Signals are recognized once per current M15 boundary and virtual positions entered at the *observed current* bid/ask within 90 seconds of that boundary. No retroactive fills. One paper position per method at once.

**Note:** This is a forward monitoring approximation, not a recreation of historical backtest execution. Targets are frozen at confirmation, stops use M15 sweep extreme plus 0.25 ATR, and paper entries/exits include adverse 0.10 GOLD price-unit slippage per side and a configurable USD 7 per standard lot roundtrip cost. **Actual observed ask−bid spread** is included. Each method begins with its own simulated 10,000 account-currency balance, risking **0.5% of current virtual equity** per new position.

The virtual fills occur on the first observed quote of a new signal rather than at the M5 bar open. Since quote monitoring is every 10 seconds by default, stops/targets may be skipped during gaps or move through price levels between polls. Exits are recorded at the next *observed* market quote (plus adverse simulated slippage), even if worse than the stop. Historical DST is not inferred: broker epoch labels are used consistently to align candles, and a fresh tick validates the present local clock offset. The monitor does not require `OpenAI` nor submit trade orders.

## Known limitations and safeguards

- No live trading: neither `mt5.order_send` nor any other trading function is called.
- No look-ahead from current forming M5/H1/H4 bars; aggregation uses only completed five-minute candles.
- Starting from an empty database skips all pre-start signals. If the process was offline, old signals are not filled at hindsight prices.
- One running monitor per DB. Running concurrent monitors against the same DB could duplicate paper signals; avoid this.
- Quote feed is sampled every 10 s by default, **not tick-by-tick**, and may miss intrapoll stop/target touches.
- `trade_tick_value` can vary by broker, symbol, and conversion currency. Verify specification and quote currency when assessing virtual P&L.
- No swap/financing, margin, market impact, broker rejects, or exact order book execution. No real-money performance claims.
- Original strategy is intentionally unchanged; previously identified backtest holdout-label errors and equal signal/entry timestamps are *not corrected by this forward research module*. The forward journal has its own independent UTC timestamps and broker clock labels.
- The dashboard reports **realized** virtual equity and closed trades. Unrealized mark-to-market P&L is not included in headline equity.
- For paper portfolio sizing, if broker lot minimum prevents 0.5% risk, signal is logged as SKIPPED.

## Output

`step10_gold_paper/paper_trades.db` (SQLite). The database contains checkpoints, signals, four virtual accounts, positions, quote samples and event logs. Dashboard can export the trade ledger. Back up this DB before upgrading code. Add `step10_gold_paper/*.db*` to `.gitignore`.
