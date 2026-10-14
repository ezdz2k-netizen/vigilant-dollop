"""Read-only MT5 multi-timeframe research: H1 trend / M15 setup / M5 timing.

Uses existing indicators.calculate_indicators and indicators.generate_signal.
No orders, no OpenAI calls, no account changes. UTC normalization is checked
against a fresh broker tick; a timestamp convention change fails closed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from indicators import calculate_indicators, generate_signal

BASE_DIR = Path(__file__).resolve().parent
FRAMES = {"M5": (mt5.TIMEFRAME_M5, 300), "M15": (mt5.TIMEFRAME_M15, 900), "H1": (mt5.TIMEFRAME_H1, 3600)}
BARS = 500
MAX_TICK_AGE = 90


def _utcnow():
    return datetime.now(timezone.utc)


def _as_str(t):
    return pd.Timestamp(t).isoformat()


def _atr(df: pd.DataFrame, period=14):
    prev = df["close"].shift(1)
    tr = pd.concat((df.high-df.low, (df.high-prev).abs(), (df.low-prev).abs()), axis=1).max(axis=1)
    # Same exponential Wilder-style smoothing approach as existing ADX module.
    return tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()


def _load_frame(symbol: str, name: str, offset_hours: int, asof: datetime):
    frame, duration = FRAMES[name]
    rates = mt5.copy_rates_from_pos(symbol, frame, 0, BARS + 10)
    if rates is None or len(rates) < 110:
        raise RuntimeError(f"Insufficient {name} data: {mt5.last_error()}")
    df = pd.DataFrame(rates).sort_values("time").drop_duplicates("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=offset_hours)
    # Excludes current/incomplete candles AND candles after the M15 decision instant.
    cutoff = pd.Timestamp(asof) - pd.Timedelta(seconds=duration + 3)
    df = df.loc[df.time <= cutoff].copy().reset_index(drop=True)
    if len(df) < 100:
        raise RuntimeError(f"Insufficient completed {name} candles before {asof.isoformat()}")
    if (df.time.dt.minute % (duration // 60 if duration < 3600 else 60)).ne(0).any():
        raise RuntimeError(f"Misaligned {name} candle timestamps")
    df = calculate_indicators(df)
    df["atr14"] = _atr(df)
    needed = ["adx", "plus_di", "minus_di", "ema50", "bb_upper", "bb_lower", "volume_avg", "atr14"]
    if df[needed].iloc[-2:].isna().any().any():
        raise RuntimeError(f"{name} indicators not ready")
    return df


def _h1_trend(df):
    last = df.iloc[-1]
    slope = float(df.ema50.iloc[-1] - df.ema50.iloc[-4])
    if last.close > last.ema50 and last.plus_di > last.minus_di and slope > 0 and last.adx >= 20:
        direction = "BULLISH"
    elif last.close < last.ema50 and last.minus_di > last.plus_di and slope < 0 and last.adx >= 20:
        direction = "BEARISH"
    else:
        direction = "MIXED"
    return direction, slope


def _m5_timing(df, direction):
    """Basic testable momentum confirmation; not a profitable-entry claim."""
    last = df.iloc[-1]
    recent = df.iloc[-4:-1]
    atr = float(last.atr14)
    if direction == "BUY":
        valid = (last.close > last.open and last.close > recent.high.max() and
                 last.close > last.ema50 and last.plus_di > last.minus_di)
    else:
        valid = (last.close < last.open and last.close < recent.low.min() and
                 last.close < last.ema50 and last.minus_di > last.plus_di)
    active_volume = bool(last.tick_volume >= last.volume_avg)
    candle_range = float(last.high - last.low)
    # Prevent a giant M5 candle triggering naive momentum chasing.
    not_overextended = bool(atr > 0 and candle_range <= 2 * atr)
    return bool(valid and active_volume and not_overextended), {
        "momentum": bool(valid), "volume_above_average": active_volume,
        "range_within_2atr": not_overextended,
    }


def analyze(symbol="EURUSD", asof_utc=None):
    """Return JSON-serializable MTF assessment; no orders or API calls.

    asof_utc: UTC decision instant. For the monitor use the M15 candle
    closing instant plus a few seconds, not the current time, avoiding look-ahead.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("Symbol cannot be empty")
    if not mt5.initialize():
        raise RuntimeError(f"MT5 connection failed: {mt5.last_error()}")
    try:
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("MT5 account unavailable")
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Symbol unavailable: {symbol}")
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or not tick.time:
            raise RuntimeError("Missing MT5 quote timestamp")
        now = _utcnow()
        raw_tick = datetime.fromtimestamp(int(tick.time), timezone.utc)
        difference = (raw_tick - now).total_seconds()
        hours = round(difference / 3600)
        residual = difference - hours * 3600
        age = now - (raw_tick - timedelta(hours=hours))
        if abs(hours) > 14 or abs(residual) > 90 or not -10 <= age.total_seconds() <= MAX_TICK_AGE:
            raise RuntimeError("Broker timestamp correction cannot be verified against a fresh tick")

        if asof_utc is None:
            asof = now
        else:
            asof = pd.Timestamp(asof_utc).to_pydatetime()
            if asof.tzinfo is None:
                raise ValueError("asof_utc requires timezone")
            asof = asof.astimezone(timezone.utc)
            if asof > now + timedelta(seconds=5):
                raise ValueError("Cannot analyze future candles")

        data = {name: _load_frame(symbol, name, hours, asof) for name in FRAMES}
        h1, m15, m5 = (data[k] for k in ("H1", "M15", "M5"))
        trend, slope = _h1_trend(h1)
        rule, rule_reason = generate_signal(m15)
        reasons = []
        if rule == "HOLD":
            reasons.append("M15 breakout rules did not trigger")
        aligned = (rule == "BUY" and trend == "BULLISH") or (rule == "SELL" and trend == "BEARISH")
        if rule != "HOLD" and not aligned:
            reasons.append("H1 trend does not confirm M15 direction")
        timing = False
        timing_parts = {"momentum": False, "volume_above_average": False, "range_within_2atr": False}
        if rule in ("BUY", "SELL"):
            timing, timing_parts = _m5_timing(m5, rule)
            if not timing:
                reasons.append("M5 entry timing/volume/extension filter not confirmed")
        final = rule if (aligned and timing) else "HOLD"
        if not reasons:
            reasons.append("H1 trend, M15 breakout and M5 timing aligned")

        def summarize(df):
            x = df.iloc[-1]
            return {
                "candle_open_utc": _as_str(x.time), "close": round(float(x.close), 6),
                "ema50": round(float(x.ema50), 6), "adx": round(float(x.adx), 2),
                "plus_di": round(float(x.plus_di), 2), "minus_di": round(float(x.minus_di), 2),
                "atr14": round(float(x.atr14), 6),
                "tick_volume": int(x.tick_volume), "avg_tick_volume20": round(float(x.volume_avg), 1),
            }

        return {
            "symbol": symbol, "asof_utc": asof.isoformat(),
            "evaluated_at_utc": now.isoformat(), "detected_offset_hours": hours,
            "quote_age_seconds": round(age.total_seconds(), 1),
            "account_type": "DEMO" if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "LIVE",
            "timeframes": {name: summarize(df) for name, df in data.items()},
            "h1_trend": trend, "h1_ema_slope_3bars": round(slope, 7),
            "m15_rule": rule, "m15_reason": rule_reason,
            "m5_confirmed": timing, "m5_checks": timing_parts,
            "mtf_signal": final, "explanations": reasons,
            "analysis_only": True, "model_analysis": "NOT RUN",
        }
    finally:
        mt5.shutdown()


def save_latest(result):
    """Write a separate status file for dashboard/monitor inspection."""
    path = BASE_DIR / "mtf_latest.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


if __name__ == "__main__":
    try:
        result = analyze()
        print(json.dumps(result, indent=2))
        print("Saved:", save_latest(result))
        print("ANALYSIS ONLY — NO ORDERS")
    except Exception as exc:
        print("MTF analysis unavailable:", exc)
        raise SystemExit(1)
