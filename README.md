# GOLD (FxPro) — Three-way Bollinger Band Reversal Research

**Read-only.** Standalone Python script using the local MetaTrader 5 terminal, no OpenAI calls, and **no `mt5.order_send`**.

## What is being compared?

All three patterns require highs/lows (wicks) to breach the *prior completed* Bollinger Band, not a candle close outside the band. Each checks for an **M15 close back inside** the current Bollinger Bands after a sweep within the previous four M15 bars.

1. **Strict:** H4, H1, and M15 breach candles must belong to the same broker-clock 4-hour block. The H4 breach only becomes known *after the H4 candle closes*. An M15 return inside the band must then be observed within one hour of that H4 close. This rule will naturally be rare.
2. **Sequential:** H4 breach is confirmed first, then H1 breach, then M15 breach, and then M15 re-entry. Confirmed event timestamps must be no more than 12 hours old.
3. **Flexible:** all three breaches may arrive in any order, no more than 12 hours apart; then confirm M15 re-entry.

For each variant, test a **fixed-at-signal middle BB target** and a **fixed-at-signal opposite BB target**, separately. Exit rules: stop beyond M15 sweep extremum by 0.25 ATR14, or target, or 48-hour maximum. Trade entered at next available M5 bar *after* the M15 close, if within 15 minutes. Any candle with both SL/TP is counted as SL first.

**No intra-H4 'forming candle' claims.** This is a conservative, strictly completed-candle research interpretation. A different tick-level replay is required to test entries that use intrabar developing H4/H1 bands.

## Setup

Extract `gold_bb_reversal.py` and `gold_reversal_dashboard.py` into `C:\MT5_AI_Trader`.

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install MetaTrader5 pandas numpy streamlit plotly
.\.venv\Scripts\python.exe gold_bb_reversal.py --symbol GOLD --months 12 --risk 0.02
.\.venv\Scripts\python.exe -m streamlit run gold_reversal_dashboard.py --server.port 8508
```

Browse http://localhost:8508.

Files generated under `gold_reversal_results/`: `comparison.csv`, `trades.csv`, `signal_counts.csv`, `report.json`.

## Caveats

- Broker-clock epoch labels are used consistently across aggregated M5/M15/H1/H4 bars. They are **not verified historical UTC**; DST and partial-session clock changes may affect a few H4 blocks. Historical session-hour labels should not be interpreted as UTC.
- M15/H1/H4 are reconstructed from complete M5 groups: missing M5 bars cause incomplete aggregated bars to be dropped rather than filled.
- GOLD tick value, tick size, point and volume limits are loaded from *your FxPro MT5 symbol*. If these are unavailable, the engine refuses to risk-size.
- Broker bar spreads approximate ask, and slippage is a configurable adverse GOLD price move (default 0.10) each side. This is not tick-accurate or a guarantee of executable fill.
- Does not include swaps, financing, full margin/margin-call modeling, gap-specific fill modeling, or symbol-specific hours beyond the available M5 data.
- Strategy configuration uses a descriptive ~8-month/4-month split with **no parameter fitting**: don't interpret the holdout as statistically independent after choosing ideas by reviewing its results.
- Historic GOLD instruments may have changed tick valuation / contract details. Current broker specification is applied as an approximation.
- The backtest intentionally does not generate or call your existing AI models. It is a pure strategy experiment.
- Multiple separate combinations may take trades at the same time. Their outcomes are independent research portfolios, not an aggregate multi-strategy live portfolio.
