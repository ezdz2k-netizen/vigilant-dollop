# MT5 AI Trader architecture audit

**Audit date:** 2026-10-14  
**Scope:** `C:\MT5_AI_Trader` source, local manifests, generated research outputs, and installed `.venv` metadata.  
**Change policy:** no existing source, market data, journal, state, or SQLite database was modified. The audit added only `audit_2026-10-14/`.

## Executive assessment

This is a research workspace containing several successive GOLD strategy packages plus an older general-purpose MT5/AI monitor. It is not one cohesive application: each step copied modules and requirements into a new folder. The latest paper-only implementation is `step14_gold`; `step11_1_gold` is the diagnostic paper monitor; `step13_2_gold` is M1 execution research; and the root scripts are the earlier M15/MTF and dashboard layer.

The safety posture is mostly read-only. Static inspection found no `mt5.order_send`, `order_check`, `TRADE_ACTION`, or position mutation call. Paper portfolios write SQLite by design. Real risks are operational: some scripts connect to MT5 at runtime, `ai_engine.py` performs an OpenAI call at import time, monitors can write state/databases, and several test files are actually live MT5 probes. Keep execution disabled and require a separate review before any new execution path.

## Inventory and layers

| Layer | Main locations | Responsibility | Persistence / external effects |
|---|---|---|---|
| Root MT5/AI | `indicators.py`, `multi_timeframe.py`, `dual_ai_engine.py`, `monitor_m15.py` | M15 rule signal, H1/M5 confirmation, optional two-stage OpenAI analysis | MT5 read; CSV/JSON writes; optional API calls |
| Root dashboards | `dashboard.py`, `dashboard_mtf.py`, `trading_hub.py`, `backtest_dashboard.py`, `xau_dashboard.py`, `step9_dashboard.py`, `gold_reversal_dashboard.py` | Streamlit views over MT5 and saved reports | Read MT5/files; dashboard behavior varies |
| Strategy research | `gold_bb_reversal.py`, `gold_walkforward/`, `gold_entry_confirmation/`, `gold_exhaustion_research/`, `gold_reversal_probability/` | Completed-candle BB exhaustion/reversal, walk-forward, entry timing, probability labels, developing H1/H4 bands | MT5 history reads; CSV/JSON outputs |
| Paper diagnostics | `step10_gold_paper/`, `step11_gold_diagnostics/`, `step11_1_gold/` | Virtual portfolios, observation diagnostics, SQLite journals | SQLite writes; MT5 reads; no live orders |
| Step 13 research | `step13_gold_lifecycle/`, `step13_2_gold/` | Causal episodes, M1 execution variants, holdout/fold reports | MT5/CSV reads; CSV/JSON outputs |
| Step 14 | `step14_gold/` | Baseline vs weak-momentum breakeven paper comparison and optional manual post-mortem | SQLite writes; MT5 reads; optional user-triggered OpenAI API |
| Audit/history | `step10a_audit/`, `step9_package/`, result folders | Prior audit reports and copied source references | Historical artifacts |

## Duplication and version lineage

The static inventory reports 87 Python files and 14,756 source lines. Exact duplicate groups include:

- `gold_bb_reversal.py` in six locations.
- `gold_entry_comparison.py` in five locations.
- `gold_exhaustion.py` in four locations.
- `step13_engine.py` in four locations.
- `step13_2_engine.py`, dashboard, and tests duplicated in `step13_2_gold` and `gold_reversal_results/step13_2_gold`.
- `paper_core.py`, paper tests, and diagnostics duplicated across Steps 10/11.

The copies have already diverged. Examples: `step11_1_gold/gold_bb_reversal.py` adds an in-memory simulation cache; Step 14’s `gold_exhaustion.py` adds `h1_inside`/`h4_inside` and changes the `adx_fade` lookup. Treat folders as versioned experiments until a canonical library is deliberately selected.

## Findings

### High priority

1. **Import-time API side effect:** `ai_engine.py` constructs `OpenAI()` and calls `responses.create` at module import. It can unexpectedly bill/fail merely when imported and has no local safety gate. It also relies on `openai`, absent from the root requirements files.
2. **MT5 connectivity is spread everywhere:** many research scripts and root tests call `mt5.initialize()`. They are not unit-test safe and require a running terminal/account context. Root tests (`test_mt5.py`, `test_market_data.py`, `test_m5_history.py`, `test_m5_chunks.py`, `test_mt5_time.py`) are connectivity probes, not deterministic tests.
3. **Database writers are long-running and use WAL:** `step11_1_gold`, `step11_gold_diagnostics`, and `step14_gold` contain SQLite databases/WAL files. Do not run competing monitors against one database or copy/delete files while writers are active. Dashboards generally open databases read-only; monitor cores create/alter schemas.
4. **No single dependency lock/manifest:** there are 15 requirements files with unconstrained or conflicting versions. The active `.venv` is Python 3.11.9 with pandas 3.0.6, numpy 2.4.6, Streamlit 1.65.0, Plotly 7.1.0, MetaTrader5 5.0.6231, OpenAI 3.29.0, and python-dotenv 1.2.4. Per-package requirements omit `openai`/`python-dotenv` where `dual_ai_engine.py` needs them.
5. **Execution research is not tick-validated:** Step 13.2 uses M1 bars and explicitly cannot establish intrabar order, true spread path, gaps, or fills. Step 14 uses 10-second quotes. These are research simulations, not broker-fill evidence.

### Medium priority

- `diagnose_gold_exhaustion.py` dynamically prepends `gold_exhaustion_research` to `sys.path`; it is intentionally path-dependent and the static audit flags the sibling import as unresolved outside that launch convention.
- Historical labels use broker-clock epochs; DST/UTC equivalence is not established.
- Root dashboards and older monitors use separate state/journal files and duplicated indicator logic, increasing inconsistency risk.
- `requirements*.txt` permit major-version drift (`pandas`, `numpy`, `streamlit`, `plotly`) and do not define a tested matrix.
- `__pycache__` artifacts include both CPython 3.11 and 3.13 bytecode, indicating mixed historical environments (ignored by `.gitignore`, but confusing during review).
- There are 80 generated data/report/database artifacts, including large history/features files. They should be catalogued and treated as immutable evidence.

### Testing gaps

Syntax compilation succeeded for all 87 Python files. Deterministic targeted checks passed: Step 14 (5 tests) and Step 13 lifecycle integrity (3 tests) under the project `.venv`. The repository has no unified test runner/configuration, no coverage gate, and many “tests” are scripts with a `run()`/`main()` function rather than discoverable test methods. There are no dashboard `AppTest` tests, no import-safety tests for API modules, no dependency lock verification, and no automated assertion that every monitor remains free of MT5 order APIs.

## Safety boundary confirmed by audit

Static search found no `order_send`, `order_check`, `TRADE_ACTION`, or live position mutation call. This is evidence about the current files only, not a guarantee for future changes. Preserve this boundary with a CI/static guard before any refactor. `.env` exists and is ignored; it was not read or included in the audit output.

## Evidence files

- [static inventory](static_inventory.json) — AST-based module, import, duplicate, dependency, test, artifact, and source-hash inventory.
- Source hashes in that inventory provide a pre-refactor snapshot without copying secrets or data.
