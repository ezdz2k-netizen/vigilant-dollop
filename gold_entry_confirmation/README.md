# GOLD Triple Bollinger Band Reversal — Entry Confirmation Comparison

Research only. **No MT5 order calls**. Keep your live monitor and existing files unchanged.

## Files
- `gold_entry_comparison.py`: downloads seven-day chunks of GOLD M5 data and evaluates four entry styles on identical historical windows.
- `gold_bb_reversal.py`: included copy of the previous Triple BB baseline module, to avoid overwriting your installed version.
- `gold_entry_dashboard.py`: Streamlit comparison dashboard.
- `requirements.txt`: Python dependencies.

## Install (PowerShell)
Extract the ZIP under `C:\MT5_AI_Trader` so it creates the `gold_entry_confirmation` folder.

```powershell
cd C:\MT5_AI_Trader\gold_entry_confirmation
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe gold_entry_comparison.py --symbol GOLD --months 12 --risk 0.02 --train-months 6 --test-months 2 --holdout-months 2
..\.venv\Scripts\python.exe -m streamlit run gold_entry_dashboard.py --server.port 8510
```
Open http://localhost:8510

## Four entry definitions
1. `reentry`: original M15 close back inside BB. Earliest possible simulated entry is following M5 bar.
2. `confirmation`: within next 3 **consecutive** completed M15 candles, a directional candle closes above the previous close (BUY) or below the previous close (SELL), remains inside BB, and does not breach the sweep extreme.
3. `structure`: within next 3 consecutive completed M15 candles, closing price breaks the preceding 3 M15 highs (BUY) or lows (SELL).
4. `momentum`: within next 3 consecutive completed M15 candles, directional body >=55% of range, price on appropriate side of rising/falling EMA20, and tick volume >= the *prior* 20-bar average.

All styles use *Flexible* H4/H1/M15 sweep alignment within 12 hours, 20/2 Bollinger Bands and an opposite-band target frozen at confirmation time. H4/H1 sweeps are only treated as known at their respective bar closes.

## Research protocol
- 12 months, 6-month training plus rolling 2-month tests, and final 2-month holdout.
- Fixed predeclared four-way candidates; scores are based on training R-multiples and a small-sample penalty. No tuning from final holdout.
- The final report explicitly compares all four styles on the *same holdout dates*. The selected style is chosen only on the development period.
- Backtester retains original GOLD tick size/value-based lot sizing, modeled spread, slippage (default 0.10 gold price each side), roundtrip commission (default $7 per lot), 0.25× M15 ATR stop buffer, 48-hour holding limit, 2% simulated risk.
- M5 historical OHLC cannot resolve actual intrabar stop/target ordering. Script assumes stop first if both touched. All prices and session times are raw MT5 broker-clock labels, not verified historical UTC. Account margin, swaps and exact fills aren't simulated.
- For credible results, validate using more history, multiple rolling folds, alternative realistic costs, and out-of-sample data not used for the design. The old holdout has been viewed already, so this is exploratory research, not an untouched prospective experiment.

Outputs go to `gold_entry_results` by default:
`entry_report.json`, `holdout_comparison.csv`, `fold_comparison.csv`, `holdout_trades.csv`, `rolling_oos_trades.csv`.

No automatic trading or OpenAI calls are implemented.
