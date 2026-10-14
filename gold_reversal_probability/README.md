# GOLD Reversal Probability Research — Step 12A

## Purpose

Use completed M15 outside-Bollinger-Band candidates from your V5 `candidate_features.csv`, and historical GOLD M5 prices from FxPro, to **label actual subsequent paths**. Compare H1, H4 and H1+H4 extension states and M15 penetration depth. Optionally fit chronological, isotonic-calibrated logistic regressions. **No MT5 orders and no OpenAI API calls.**

## Install in Windows VS Code

Extract `gold_reversal_probability` into `C:\MT5_AI_Trader`. Keep your existing `gold_exhaustion_research` files unchanged.

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install -r gold_reversal_probability\requirements.txt
.\.venv\Scripts\python.exe gold_reversal_probability\test_probability.py
.\.venv\Scripts\python.exe gold_reversal_probability\probability_engine.py --symbol GOLD --months 12 --candidates gold_exhaustion_research\gold_exhaustion_results\candidate_features.csv --output gold_reversal_probability\results
.\.venv\Scripts\python.exe -m streamlit run gold_reversal_probability\probability_dashboard.py --server.port 8515
```

Then open `http://localhost:8515`.

If you previously exported raw GOLD M5 candles with MT5 `time` epoch seconds, add `--m5-csv path\to\gold_m5.csv` to avoid downloading them again. The engine uses seven-day download chunks otherwise.

## Label meaning

For each eligible outside-M15-close event, consider H1, H4, or BOTH based on the **developing H1/H4 state stored at that M15 boundary**. The H1 and H4 groups overlap with BOTH by design.

Paper entry is approximated from the next M5 bid open with spread for BUY, and slippage. Stop uses the M15 sweep candle high/low with 0.25× ATR buffer; targets are fixed M15 middle or opposite band levels at the event timestamp. Both stop and target are evaluated over 4, 8, 16, or 32 M15 candles; same-bar stop/target ties count as STOP. Some candidate/target combinations are omitted when entry/stop/target ordering is invalid (and are **not** counted as failures). Missing M5 data or insufficient forward horizon are excluded.

This is an **event-level target-before-stop outcome**, not a guaranteed profitable order, a calibrated live reversal prediction, or the same state-machine trading strategy as Step 11.1.

## Files

- `labeled_events.csv`: outcomes, features, MFE/MAE, time to event, and targets
- `holdout_rates.csv`: empirical per-mode, per-target, per-horizon rates
- `depth_outcomes.csv`: depth-binned rates, Wilson intervals, MFE/MAE and event timing
- `probability_metrics.csv`: Brier scores comparing calibrated, raw, and constant-base-rate forecasts
- `holdout_predictions.csv`: out-of-sample probabilities where enough data is available
- `report.json`: experiment settings and limitations

## Critical caveats

* Historical candles retain raw broker-clock epoch **labels**, not verified UTC; DST assumptions remain unverified.
* M5 bid OHLC cannot determine true tick sequencing; stop-first is assumed if both SL and TP are touched in a bar. Sell-side ask candles are approximated using recorded M5 spread or fallback spread.
* The labeling engine requires the immediate next M5 bar and sufficient forward bars; calendar gaps may still be included inside the window. Results may differ under an explicit market-session time horizon.
* Multiple overlapping outside-close candidates can belong to the same directional move. Consequently calibration samples are correlated, Wilson intervals are optimistic, and simple chronological splits with an 8-hour purge are **not** proof of independent validation.
* Historical holdout periods have already been inspected in this project; consider genuinely new forward data necessary for final validation.
* Calibrated probabilities are not live signals. Do not authorize automatic orders using these outputs.
* Compare against the constant training-base-rate Brier score; **lower is better**. A fitted model that does not beat it shows no demonstrated predictive value.
