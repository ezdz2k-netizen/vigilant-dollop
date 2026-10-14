# Step 14 — FxPro GOLD Forward Paper Validation & AI Review

## Scope and safety

**Paper only. No live MT5 orders.** This is a separate self-contained research project. It copies (but does not modify) the Step 13.1 strategy modules `step13_engine.py` and `gold_exhaustion.py` to use the same H1, H4, or combined developing-BB criteria and strong M15 outside-close arm rule. The comparison uses the **M15 re-entry** entry method from Step 13.1 for both portfolios. Changing the strategy during observation would invalidate an apples-to-apples comparison.

Six independent portfolios: `H1`, `H4`, `BOTH` × `baseline`, `weak_be`. Starting equity: 10,000 each in the broker account's currency as represented by MT5 `trade_tick_value` (not necessarily USD). Default risk: 0.5% of current *realized* virtual equity per entry. One concurrent paper position per portfolio. No `order_send`, `order_check`, real position calls, or MT5 account mutations.

## Install on Windows with VS Code

1. Unzip the `step14_gold` folder under `C:\MT5_AI_Trader`. Leave existing folders unchanged.
2. From `C:\MT5_AI_Trader` run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r step14_gold\requirements.txt
.\.venv\Scripts\python.exe step14_gold\test_step14.py
.\.venv\Scripts\python.exe step14_gold\paper_monitor14.py --symbol GOLD --risk 0.005
```

3. In a **second** terminal:

```powershell
.\.venv\Scripts\python.exe -m streamlit run step14_gold\dashboard14.py --server.port 8518
```

Open `http://localhost:8518` on that PC. Run **one monitor** against `step14_paper.db`. Don't share one DB between Step 11.1 and Step 14.

## Optional ChatGPT trade post-mortem (manual, API-billed)

Set `OPENAI_API_KEY` in the **project root** `.env` (never commit this file). The API account must have credits and access to the selected model. Default `OPENAI_REVIEW_MODEL=gpt-4.1-mini` is a configurable example; you can override it with an available Responses API model.

After a paper position closes, find its ID in the dashboard and run:

```powershell
.\.venv\Scripts\python.exe step14_gold\ai_postmortem.py --position-id 1
```

The reviewer only analyzes **a closed virtual trade** and stores structured interpretation in SQLite. The MT5 monitor/dashboard make no API requests and the model never controls entries, exits or risk parameters. API charges are separate from ChatGPT subscriptions. Do not paste secrets into the dashboard or trade notes.

## Defined behavior

- Live MT5 GOLD Bid/Ask and M5 **read-only** history. The latest forming M5 bar is excluded. Higher-timeframe bands use the bundled session-aware Step 13 features reconstructed at completed M15 boundaries. Historical epoch labels are broker clock and may not equal UTC.
- Only processes a completed M15 boundary as a paper-entry opportunity if the system observes it **within 90 seconds**, spread <= 2 GOLD price units, the monitor did not miss any M15 boundaries, and a fully observed M15 candle is available. Late data is logged for diagnosis only; missed signals are never backfilled.
- Strategy parameters: `depth=0.10 ATR`, `expiry=6 M15 candles`, Step 13 `reentry` method, no RSI/ADX discretionary filter. Uses the entry signal's frozen M15 middle-BB target, sweep extreme ± 0.25 ATR initial SL.
- Both baseline and weak-BE virtual positions open on the **same observed quote**. Baseline holds original stop/target. Weak-BE checks the **first FULL M15 candle after actual entry**. Weak means candle not in reversal direction OR body/range < 0.50 OR favorable close progress < 0.10 ATR. If weak, it **marks breakeven adjustment pending**, and tightens its stop only when a *later observed quote* permits a cost-adjusted BE stop without immediate execution; it never widens SL. If the weak first candle is already unprofitable and the market never rallies, the original stop can still be hit. Breakeven is an intended level, not a guaranteed zero-P&L fill.
- Position exits are checked at observed Bid (BUY) / Ask (SELL), with adverse simulated exit slippage, roundtrip commission (default 7 currency units per lot), and broker `trade_tick_size`/`trade_tick_value`. Unrealized P&L is **not** reported as realized equity.
- There is **no adaptive strategy change**, no trailing stop, no AI trading veto in this Step 14 fixed comparison. This is intentional: isolate the impact of weak-momentum BE.

## Important research limitations

- 10-second polling may miss interim stop/target touches or spread spikes and cannot reproduce real fill latency, gaps or true intratick sequencing. If both TP and SL were touched between polls, it cannot reconstruct ordering. Not a reliable basis for real-money performance claims.
- The MT5 terminal clock offset is verified from recent ticks per poll, but the data still uses broker-epoch labels and historical DST is not resolved.
- No swap, margin calls, contract-currency conversion, market depth, funding, realized tax or liquidity impact. The account's tick value is used as a currency proxy; verify broker account currency.
- If the monitor is down, virtual stop/target events are **not reconstructed**. A restarted monitor can close at the first observed price, introducing paper execution discrepancies. Report outages.
- Full end-to-end runtime against your local FxPro terminal is **not verified in this environment**. Offline tests cover simulated positions and state. Run on a demo environment first, though the program itself never sends an order.

## Files

- `paper_monitor14.py`: MT5 quote polling and causal Step 13 M15 signal detection.
- `paper_core14.py`: SQLite portfolios, paper entries, stop/target/time exits, momentum check and BE activation.
- `ai_postmortem.py`: optional manually invoked OpenAI Responses API post-mortem of closed trades.
- `dashboard14.py`: Streamlit read-only comparative dashboard, signals, evaluation coverage, journal and AI reviews.
- `gold_exhaustion.py`, `step13_engine.py`: copied, unchanged Step 13.1 research signal dependencies.
- `test_step14.py`: offline regression tests. `requirements.txt`: dependencies.

## Reproducibility

Keep the strategy code and paper-trading settings fixed for at least several weeks. Compare independent portfolios only on the same underlying signal cohorts; report sample sizes and missed observations. More observations improve confidence, but no sample size guarantees future profitability.
