"""MT5 M15 monitor with Step 7 multi-timeframe confirmation (research only).

Requires sibling modules: dual_ai_engine.py, indicators.py, multi_timeframe.py.
Uses existing simulated/real AI settings in dual_ai_engine.py.
Never sends trading orders. One journaled outcome per completed candle per mode.
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

import dual_ai_engine as engine
from multi_timeframe import analyze as analyze_mtf, save_latest as save_mtf_latest

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "monitor_m15_mtf_state.json"  # fresh state namespace
MTF_JOURNAL = BASE_DIR / "mtf_monitor_journal.csv"
POLL_SECONDS = 10
HEARTBEAT_SECONDS = 300
DIAGNOSTIC_SECONDS = 60
ERROR_RETRY_SECONDS = 60
BILLING_RETRY_SECONDS = 1800
CANDLE_SECONDS = 900
CLOSE_BUFFER_SECONDS = 3
MAX_CLOSE_AGE_SECONDS = 3600
MAX_TICK_AGE_SECONDS = 90

# SAFETY: no forced candidates; no trading implementation exists.
ANALYSIS_ONLY = True


def now_utc():
    return datetime.now(timezone.utc)


def parse_utc(value):
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        raise ValueError("Timestamp has no timezone")
    return ts.to_pydatetime().astimezone(timezone.utc)


def load_state():
    if not STATE_FILE.exists():
        return {}
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid monitor state")
    return data


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def mode_key():
    mode = "simulation" if engine.SIMULATION_MODE else "real_ai"
    return f"{engine.SYMBOL}:M15:MTF:{mode}"


def detect_offset():
    """Estimate whole-hour offset from a *fresh* quote, or fail closed."""
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
    try:
        if not mt5.symbol_select(engine.SYMBOL, True):
            raise RuntimeError(f"Symbol not found: {engine.SYMBOL}")
        tick = mt5.symbol_info_tick(engine.SYMBOL)
        if tick is None or not tick.time:
            raise RuntimeError("Missing MT5 tick")
        utc = now_utc()
        raw = datetime.fromtimestamp(int(tick.time), timezone.utc)
        delta = (raw - utc).total_seconds()
        hours = round(delta / 3600)
        residual = delta - hours * 3600
        age = (utc - (raw - timedelta(hours=hours))).total_seconds()
        if abs(hours) > 14 or abs(residual) > 90 or not -10 <= age <= MAX_TICK_AGE_SECONDS:
            raise RuntimeError(f"Cannot verify MT5 clock offset; residual={residual:.0f}s age={age:.0f}s")
        return hours, age
    finally:
        mt5.shutdown()


def normalize_snapshot(snapshot, offset_hours):
    """Normalize only time fields; preserve rule candidate and raw timestamp."""
    snapshot = dict(snapshot)
    raw_time = parse_utc(snapshot["last_closed_candle"])
    corrected_time = raw_time - timedelta(hours=offset_hours)
    snapshot["broker_candle_time"] = raw_time.isoformat()
    snapshot["last_closed_candle"] = corrected_time.isoformat()
    snapshot["detected_offset_hours"] = offset_hours
    snapshot["rule_action"] = snapshot["actual_rule_action"]
    snapshot["is_forced_test"] = False
    snapshot["candles"] = [
        {**c, "time": (parse_utc(c["time"]) - timedelta(hours=offset_hours)).isoformat()}
        for c in snapshot["candles"]
    ]
    return snapshot, corrected_time


def candle_eligible(candle_open):
    if candle_open.second != 0 or candle_open.minute % 15 != 0:
        raise RuntimeError("M15 candle not aligned to 15-minute boundary")
    age = (now_utc() - (candle_open + timedelta(seconds=CANDLE_SECONDS))).total_seconds()
    return CLOSE_BUFFER_SECONDS <= age <= MAX_CLOSE_AGE_SECONDS, age


def selected_journal():
    return engine.SIMULATION_JOURNAL if engine.SIMULATION_MODE else engine.LIVE_JOURNAL


def already_journaled(snapshot):
    path = selected_journal()
    if not path.exists():
        return False
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if (row.get("symbol") == snapshot["symbol"]
                and row.get("timeframe") == "M15"
                and row.get("forced_test", "").lower() != "true"
                and row.get("candle_time")
                and parse_utc(row["candle_time"]) == parse_utc(snapshot["last_closed_candle"])):
                return True
    return False


def append_mtf_journal(snapshot, mtf, final):
    """Separate MTF record; never alter existing AI journal schema."""
    record = {
        "logged_at_utc": now_utc().isoformat(),
        "symbol": snapshot["symbol"],
        "candle_time": snapshot["last_closed_candle"],
        "mode": "SIMULATION" if engine.SIMULATION_MODE else "REAL_AI",
        "m15_rule": snapshot["actual_rule_action"],
        "h1_trend": mtf["h1_trend"],
        "m5_confirmed": mtf["m5_confirmed"],
        "mtf_signal": mtf["mtf_signal"],
        "final_signal": final["action"],
        "reason": final["reason"],
    }
    exists = MTF_JOURNAL.exists() and MTF_JOURNAL.stat().st_size > 0
    with MTF_JOURNAL.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(record))
        if not exists:
            writer.writeheader()
        writer.writerow(record)


def ai_review(snapshot):
    """Luna then Sol; AI never gets to reverse the selected candidate."""
    candidate = snapshot["actual_rule_action"]
    fast = engine.ask_model(engine.FAST_MODEL, engine.FAST_PROMPT, snapshot, "low")
    fast = engine.enforce_gate(fast, candidate)
    if not fast["approved"]:
        return fast, None, fast
    deep = engine.ask_model(
        engine.DEEP_MODEL, engine.DEEP_PROMPT,
        {"market": snapshot, "stage_one": fast}, "medium"
    )
    deep = engine.enforce_gate(deep, candidate)
    return fast, deep, deep


def process(snapshot, state):
    candle = snapshot["last_closed_candle"]
    print(f"\nNEW CLOSED M15 CANDLE: {candle} | {snapshot['symbol']} | close={snapshot['close']}")
    if already_journaled(snapshot):
        print("Existing non-forced AI journal row detected; skipping to avoid duplicate.")
        state[mode_key()] = candle
        save_state(state)
        return

    # A decision at the exact M15 close + 3 seconds. This avoids look-ahead.
    decision_time = parse_utc(candle) + timedelta(seconds=CANDLE_SECONDS + CLOSE_BUFFER_SECONDS)
    mtf = analyze_mtf(symbol=snapshot["symbol"], asof_utc=decision_time)
    save_mtf_latest(mtf)
    print(f"H1={mtf['h1_trend']} | M15={mtf['m15_rule']} | M5={mtf['m5_confirmed']} | MTF={mtf['mtf_signal']}")

    # Defensive consistency: the MTF analysis must refer to the same M15 rule.
    if mtf["m15_rule"] != snapshot["actual_rule_action"]:
        raise RuntimeError("MTF and monitor M15 rule disagree; no AI assessment saved")

    fast = deep = None
    if mtf["mtf_signal"] != snapshot["actual_rule_action"] or mtf["mtf_signal"] == "HOLD":
        final = engine.reject_result("MTF gate HOLD: " + "; ".join(mtf["explanations"]))
        print("MTF rejected or no candidate; both AI stages skipped.")
    else:
        print("[1/2] Fast AI screening")
        fast, deep, final = ai_review(snapshot)
        print("[2/2] Deep AI review:", "completed" if deep else "skipped")

    print("FINAL:", final["action"], "| approved:", final["approved"])
    engine.save_journal(snapshot, fast, deep, final)
    append_mtf_journal(snapshot, mtf, final)
    state[mode_key()] = candle
    save_state(state)
    if engine.SIMULATION_MODE:
        print("SIMULATION: model results are mocked, not real market analysis")
    print("NO TRADE EXECUTED")


def run():
    if not ANALYSIS_ONLY:
        raise RuntimeError("Execution mode not supported")
    if not engine.ANALYSIS_ONLY:
        raise RuntimeError("AI engine must remain analysis-only")
    if engine.FORCE_TEST_SIGNAL:
        raise RuntimeError("Set FORCE_TEST_SIGNAL = False in dual_ai_engine.py before monitoring")

    state = load_state()
    print("MT5 M15 + H1/M5 INTELLIGENCE MONITOR | READ ONLY")
    print("Symbol:", engine.SYMBOL, "| interval:", POLL_SECONDS, "seconds")
    print("AI:", "SIMULATION" if engine.SIMULATION_MODE else "REAL API")
    print("State:", STATE_FILE.name, "| previous:", state.get(mode_key(), "none"))
    print("Press Ctrl+C to stop")
    last_diag = last_heartbeat = last_offset = 0
    last_offset_hours = None
    backoff_until = 0

    try:
        while True:
            try:
                monotonic = time.monotonic()
                if monotonic < backoff_until:
                    time.sleep(min(POLL_SECONDS, max(1, backoff_until - monotonic)))
                    continue
                if monotonic - last_heartbeat >= HEARTBEAT_SECONDS:
                    print("Heartbeat:", now_utc().isoformat())
                    last_heartbeat = monotonic

                snapshot = engine.get_market_snapshot()
                offset_hours, tick_age = detect_offset()
                if last_offset_hours is not None and offset_hours != last_offset_hours:
                    print("Offset changed; waiting one poll to verify:", last_offset_hours, "->", offset_hours)
                    last_offset_hours = offset_hours
                    time.sleep(POLL_SECONDS)
                    continue
                last_offset_hours = offset_hours
                snapshot, candle_open = normalize_snapshot(snapshot, offset_hours)
                eligible, close_age = candle_eligible(candle_open)

                if monotonic - last_diag >= DIAGNOSTIC_SECONDS:
                    print(f"UTC {now_utc().isoformat()} | raw candle {snapshot['broker_candle_time']} | "
                          f"offset {offset_hours:+d}h | normalized {candle_open.isoformat()} | "
                          f"close age {close_age:.0f}s | tick age {tick_age:.1f}s")
                    last_diag = monotonic

                last = state.get(mode_key())
                newer = last is None or candle_open > parse_utc(last)
                if eligible and newer:
                    process(snapshot, state)
                time.sleep(POLL_SECONDS)
            except Exception as exc:
                message = str(exc)
                billing = any(s in message.lower() for s in
                              ("insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached"))
                cooldown = BILLING_RETRY_SECONDS if billing else ERROR_RETRY_SECONDS
                print(f"Monitor error: {message}\nRetry in {cooldown}s. No trade executed.")
                backoff_until = time.monotonic() + cooldown
    except KeyboardInterrupt:
        print("\nMonitor stopped. NO TRADE EXECUTED")


if __name__ == "__main__":
    run()
