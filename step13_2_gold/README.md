# Step 13.2 — FxPro GOLD reversal execution research

**Read-only research package.** Contains no `MetaTrader5.order_send`, trade modification, account actions, or AI API calls. Keep Step 11.1 forward paper monitor unchanged.

## Files

- `step13_2_engine.py`: M1 replay, execution variants, independent fold comparisons, trade audit output.
- `step13_2_dashboard.py`: Streamlit comparison, fold/holdout, trade ledger and limitations.
- `step13_engine.py`, `gold_exhaustion.py`: included Step 13.1 signal-generation dependencies (copies from the supplied project), no changes to live scripts.
- `test_step13_2.py`: six offline tests.
- `requirements.txt`: Python dependencies.

## Windows installation

Extract `step13_2_gold` folder to `C:\MT5_AI_Trader\`. In PowerShell:

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install -r step13_2_gold\requirements.txt
.\.venv\Scripts\python.exe step13_2_gold\test_step13_2.py
.\.venv\Scripts\python.exe step13_2_gold\step13_2_engine.py --symbol GOLD --months 12 --output step13_2_gold\results
.\.venv\Scripts\python.exe -m streamlit run step13_2_gold\step13_2_dashboard.py --server.port 8517
```

**M1 data**: Requests in 3-day chunks. A full 12-month history can be large and take time. If you already have a trusted M1 CSV, pass `--m1-csv C:\path\m1_gold.csv` (time, open, high, low, close, optional spread). For M5, use `--m5-cache ...` if available. The engine fails if M1 coverage is insufficient near the end or individual executions. It does not silently approximate 1-minute triggers with M5 data.

**Optional tick audit**: `--tick-csv C:\path\gold_ticks.csv` (time, bid, ask) checks schema and clock overlap; it DOES NOT execute using historical ticks or certify fills. If tick-perfect trailing/fills are required, this simulator is not sufficient. Document that limitation rather than presenting results as tick-validated.

## Research variants (14)

Baseline immediate M1 open, favorable-movement thresholds 0.05/0.10/0.20 ATR, directional next M15 close, weak-first-full-M15 immediate exit, weak-first-full-M15 breakeven protection, ATR 1.5 and Chandelier 2 ATR trailing, breakeven + ATR trailing, opposite BB target, fixed 2R target, 1.5x spread and +0.25 GOLD price-unit slippage.

The original stop is sweep extreme +0.25 ATR. Middle/opposite bands are **M15 levels fixed at signal time**. Virtual risk defaults to 0.5% of $10,000 equity. Uses MT5 symbol point/tick size/tick value and lot limits. Historical bar spreads are used when available, otherwise a configurable fallback, plus modeled commission and adverse slippage. No position/netting/margin/swap modeling.

An entry threshold hit within an M1 bar is filled at the **next observed M1 opening**, not at the favorable threshold. For all methods, original SL/TP are active immediately. Strong-momentum criterion: first fully completed M15 after entry moves in the trade direction, body >= 50% of range and favorable close progress >=0.1 ATR. Weak exits at the first eligible next M1 open. Stops trail only after the corresponding minute ends; stop/TP priority uses stop-first on same M1 bar. Time-based stops are capped to 48 hours.

## Validation and cautions

- **Not tick-perfect**. M1 bid OHLC cannot establish intra-minute high/low ordering, gap execution, historical spread path, or whether slippage is realistic.
- Detected gaps during a simulated position lead to excluded trades; check coverage before drawing conclusions. Weekend gaps are not gap-filled artificially.
- Historical FxPro broker-clock and DST conventions remain unverified; ensure M1/M5 data share the same time basis. Sample tick CSV check only tests overlap.
- All parameter styles are evaluated on final holdout for *description only*. Development selection comes from preceding out-of-sample folds, but prior GOLD research has already inspected these dates. **Fresh prospective validation is required.**
- Results are independent simulated portfolios for each mode/method/config, not a combined strategy; do not sum returns across variants.
- The 12 months of data include available M5 signal warmup. Trade windows crossing fold boundaries are excluded rather than allowing future exits to contaminate training/test results.

## Output

`results/walkforward_folds.csv`, `development_selections.csv`, `holdout_comparison.csv`, `holdout_trades.csv`, `report.json`.

## Scope limitation

This is a functional M1 research implementation and optional tick-history **validator**, not a true tick-replay trading engine. It should not be used to claim observed broker fills or certify positive expected returns. No trading orders are sent.
