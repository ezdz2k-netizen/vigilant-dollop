# Prioritized development roadmap

This roadmap is deliberately staged. No strategy should be promoted based on existing reports alone; every performance claim needs a fresh, prospective, out-of-sample validation with costs and coverage reported.

## P0 — protect evidence and prevent accidental effects

1. Freeze the current workspace: retain the audit source manifest and make a separately stored, verified backup of SQLite databases using SQLite backup while monitors are stopped. Never delete/overwrite existing CSV, JSON, DB, WAL, or SHM files.
2. Add a repository-wide safety check that fails if new code contains MT5 order APIs, account/position mutation APIs, or imports that execute external calls at import time.
3. Move all live MT5 probes and monitors behind explicit commands; make imports side-effect free. `ai_engine.py` must not call OpenAI at import.
4. Add secret hygiene checks for `.env`, API-key patterns, and generated logs. Keep `.env` local and untracked.

## P1 — establish one maintainable baseline

1. Choose a canonical research version (recommended starting point: Step 14 plus its Step 13 dependencies) and label all other folders as historical/experimental.
2. Extract shared packages for: MT5 read-only data access, broker-clock normalization, indicators/developing bands, signal generation, simulation/fill model, and SQLite paper ledger. Preserve old folders unchanged while migrating tests.
3. Replace 15 requirements files with a root environment specification plus per-feature extras, lock tested versions for Python 3.11, and add a clean-environment install check.
4. Define clear interfaces and schemas for signals, trades, evaluations, reports, and database migrations. Add schema versioning and read-only dashboard connections.

## P2 — test the system, not just functions

1. Convert script-style tests into `pytest`/`unittest` discovery with deterministic synthetic bars and fixed seeds.
2. Add import tests that stub MT5/OpenAI and prove no network, terminal, order, or database writes occur during import.
3. Add safety tests scanning all Python source for forbidden order APIs and checking monitors require paper mode.
4. Add Streamlit `AppTest` smoke tests for each dashboard’s no-data, stale-data, and read-only database paths.
5. Add property tests for timezone normalization, incomplete bars, missing M5 intervals, stop/target priority, volume quantization, and BE never widening a stop.

## P3 — research validity and reproducibility

1. Build a single data contract: symbol, broker clock, timezone evidence, bar completeness, spread source, slippage, commission, tick specifications, and missing-data policy.
2. Separate development, validation, and untouched prospective periods. Record selection decisions before opening holdout results; report sample size, uncertainty, and sensitivity.
3. Validate M1 execution research against available tick samples; quantify unresolved ordering and gap cases rather than presenting M1 outcomes as fills.
4. Re-run each candidate strategy from a clean environment and archive immutable manifests, configuration, code hash, and report hash.

## P4 — paper operations

1. Run exactly one monitor per database with documented ownership, backup, restart, and outage procedures.
2. Add health alerts for stale quotes, missed boundaries, WAL growth, schema mismatch, clock-offset changes, and duplicate writers.
3. Compare baseline and BE portfolios only on identical signal cohorts and publish coverage/missed-observation statistics.
4. Keep AI post-mortems manual, closed-trade-only, non-controlling, and separately budgeted.

## P5 — only after review: any execution work

Execution functions remain disabled. A future execution proposal must be a separate reviewed change with explicit demo-account constraints, dry-run tests, order API allow-listing, kill switch, audit logging, and user approval. No profitability inference authorizes execution.

## Recommended next review questions

- Which package is the intended canonical research baseline: Step 14, Step 13.2, or another branch?
- Should old step folders remain immutable evidence or be moved to an archive after backups?
- Is the goal a single read-only research app, or continued independent experiment packages?
