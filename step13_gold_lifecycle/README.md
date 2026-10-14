# Step 13 — FxPro GOLD Reversal Lifecycle & Entry Timing Research

This is a **separate, historical, read-only research application**. It does not modify your `step11_1_gold` paper monitor, the virtual accounts, or any live account. It does not call `order_send`, `order_check`, or the OpenAI API.

## Included

- `gold_exhaustion.py`: copied session-aware V5 higher-timeframe Bollinger Band and M15 feature engine from the existing project.
- `step13_engine.py`: lifecycle, four entry-timing methods, MFE/MAE, risk-aware virtual fills, walk-forward evaluation.
- `step13_dashboard.py`: Streamlit historical research and trade comparison.
- `test_step13.py`: offline regression tests.
- `requirements.txt`: package dependencies.

## Installation — VS Code Windows

Extract the ZIP into `C:\MT5_AI_Trader`, preserving the `step13_gold_lifecycle` directory. From a VS Code PowerShell terminal:

```powershell
cd C:\MT5_AI_Trader
.\.venv\Scripts\python.exe -m pip install -r step13_gold_lifecycle\requirements.txt
.\.venv\Scripts\python.exe step13_gold_lifecycle\test_step13.py
.\.venv\Scripts\python.exe step13_gold_lifecycle\step13_engine.py --symbol GOLD --months 12 --risk 0.005 --output step13_gold_lifecycle\results
.\.venv\Scripts\python.exe -m streamlit run step13_gold_lifecycle\step13_dashboard.py --server.port 8516
```

Visit `http://localhost:8516`.

To use offline cached M5 data exported from MT5, specify `--cache C:\path\to\m5_history_raw.csv` and **verify** `--tick-size`, `--tick-value`, `--point`, `--min-lot`, `--max-lot`, and `--lot-step` against your FxPro GOLD symbol. Offline defaults are examples, not verified broker contract values.

## Exact research definitions

- **H1**, **H4**, **BOTH**: current *developing* H1/H4 Bollinger Band(20,2) at each completed M15 boundary; current price is M5 close at that boundary. The prior 19 observed completed higher-timeframe trading blocks plus developing current price are used. H1-only means H1 sufficient, whether or not H4 also qualifies. BOTH requires same-side extension of both.
- **Mandatory arm**: completed M15 close is outside its own BB(20,2) by >=0.10 ATR14. `--depth` adjusts this. The outside close *arms* a reversal hypothesis; it is **not** an immediate order.
- **Conditional invalidation**: after arming, if M15 closes inside its own BB and both developing H1 and H4 prices are simultaneously **inside both upper and lower band boundaries**, invalidate. A new strong outside M15 close is required to create a new episode. Expire after six M15 observations (`--expiry`), by default.
- **Entry A reentry**: first later M15 close inside the bands *when not invalidated*.
- **Entry B confirmation**: subsequent M15 close in intended reversal direction beyond first re-entry's close.
- **Entry C structure**: causal three-M15-bar structure-break proxy (not a complete discretionary CHOCH model).
- **Entry D failed retest**: first re-entry, then price retests near the sweep extreme, then closes back inside moving away from the first re-entry close. This is a conservative algorithmic proxy, not order-book liquidity validation.
- **Exhaustion research features**: RSI divergence and ADX fade are **optional filters** compared individually or in OR mode. Both come from completed M15 bars and use causal indicator series; neither is a proof of a reversal.
- **Virtual stop**: the most extreme observed M15 sweep plus 0.25 ATR buffer; **targets**: fixed middle or opposite M15 BB at confirmation; entry at next available M5 open with spread and adverse slippage. One virtual trade open per independent combination; two-sided 7 currency units per standard lot round-trip commission default. Historical M5 OHLC chooses STOP when both STOP/TARGET touched in one bar.
- **Lifecycle**: first re-entry timing, further extreme beyond arm, and MFE/MAE at 4/8/16/32 M15 horizons after arming; lifecycle measurement is *not* a guaranteed trade and can extend beyond invalidation.

## Data, validation, and interpretation

- Historical price data fetched in **7-day MT5 M5 chunks** with 100-day indicator warm-up, when no `--cache` is specified.
- Time labels are **raw broker-clock epoch labels**, not verified true UTC. FxPro's broker timezone/DST requires comparison to native MT5 charts.
- Train 180 days, test 60 days, roll 60 days, reserve the last 60 days as a final *research holdout*; a trade must exit within its evaluation segment. **Holdout dates from earlier investigations have already been inspected**, so these results are not pristine unseen validation.
- Within each training fold, variants (entry method, RSI/ADX filter, fixed target) are ranked by return minus 0.5 times drawdown. Only fold-selected configurations are used in that fold's rolling test. A development-only selection is evaluated on the final holdout; a fixed close-back-inside baseline is evaluated over the same holdout dates.
- A separate `holdout_method_comparison.csv` is purely exploratory and MUST NOT be used to call one method validated. A new prospective paper-trading period is required.
- Confidence in historical figures is limited by intrabar ambiguity, spread/slippage approximation, swaps/funding not simulated, and differences between reconstructed candle boundaries and the native broker terminal.
- The dashboard is read-only and loads previously generated CSV/JSON files; it never accesses MT5.

## Output files

| File | Description |
|---|---|
| `feature_snapshots.csv` | Historical M15 observations with developing H1/H4 indicator values |
| `episodes.csv` | Armed lifecycle episodes, expiry/invalidation status |
| `reentry_timing.csv` | Additional extension and first re-entry delay |
| `lifecycle_paths.csv` | MFE/MAE horizons after arm |
| `walkforward_folds.csv` | Fold-selected candidate rolling test metrics |
| `final_holdout.csv` | Development-selected candidate versus fixed baseline |
| `holdout_method_comparison.csv` | Descriptive all-four-entry/target comparisons |
| `holdout_trades.csv` | Simulated trade ledger with entry/exit and MFE/MAE |
| `report.json` | Configuration, counts, warnings |

## Safety

All files are historical research tools. No real MT5 orders, no position modification, no OpenAI API calls. Keep the Step 11.1 forward monitor as an unchanged benchmark.

## Step 13.1 data integrity patch (2026-10-13)

Fixes `lifecycle_paths.csv` serialization. The earlier `lifecycle.extend(DataFrame)`
iterated **column names**, generating a single-column CSV instead of observations.
Now the engine appends `DataFrame.to_dict(orient='records')` and writes with a fixed
schema (`episode,mode,side,arm_time,depth,horizon_m15,mfe_atr,mae_atr,net_excursion_atr`).
The dashboard checks for missing columns or no rows and shows an explanation instead
of crashing.

Trade ledger now records `signal_known_at`, `entry_bar_open_time`,
`entry_delay_seconds`, `entry_bid_open`, `entry_spread_price`,
`entry_slippage_price`, and `entry_fill_basis`. A signal timestamp equal to its
entry-bar opening timestamp is possible at an M15/M5 boundary; this remains an
**OHLC model assumption, not a verified executable tick fill**. No order execution.

Run before backtesting:

```powershell
.\.venv\Scripts\python.exe step13_gold_lifecycle\test_step13.py
.\.venv\Scripts\python.exe step13_gold_lifecycle\test_step13_integrity.py
```

Rerun the entire historical engine to regenerate `lifecycle_paths.csv` and
`holdout_trades.csv`; replacing the dashboard alone will not repair old outputs.
Previously inspected holdout data remains research-only, not fresh validation.
