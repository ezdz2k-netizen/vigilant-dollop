"""Step 8: Read-only EURUSD M5/M15/H1 historical strategy backtest (timestamp fix).

Uses indicators.py from Step 5. NEVER sends trades or invokes OpenAI.
See STEP8_README.md for modelling assumptions and known limitations.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from indicators import calculate_indicators, generate_signal

BASE = Path(__file__).resolve().parent
SECONDS = {"M5": 300, "M15": 900, "H1": 3600}
MT5_TF = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}


@dataclass
class Config:
    symbol: str = "EURUSD"
    months: int = 12
    initial_equity: float = 10000.0
    risk: float = 0.02
    reward_r: float = 3.0
    atr_mult: float = 1.5
    slippage_pips: float = 0.2  # per side; conservative constant assumption
    commission_per_lot_roundtrip: float = 7.0
    fallback_spread_pips: float = 1.0
    strategy: str = "both"  # baseline, mtf, both
    offset_hours: int | None = None  # optional manually verified override
    pip_size: float = 0.0001
    pip_value_per_lot: float = 10.0  # EURUSD quote USD, 100000-unit standard lot
    max_lots: float = 10.0
    min_lots: float = 0.01
    lot_step: float = 0.01
    max_holding_hours: int = 48
    warmup_days: int = 100


def utc(t):
    x = pd.Timestamp(t)
    if x.tz is None:
        raise ValueError("Timestamp must be timezone-aware")
    return x.tz_convert("UTC")


def broker_offset(symbol, override):
    if override is not None:
        if abs(override) > 14:
            raise ValueError("Offset must be between -14 and +14 hours")
        print(f"WARNING: explicit broker offset {override:+d}h; verify against chart")
        return override
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or not tick.time:
        raise RuntimeError("No tick: cannot verify broker timestamp offset. Re-run during active market or pass --offset-hours after verifying.")
    delta = (datetime.fromtimestamp(int(tick.time), timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    hours = round(delta / 3600)
    residual = delta - hours * 3600
    if abs(hours) > 14 or abs(residual) > 90:
        raise RuntimeError(f"Broker timestamp cannot be validated: delta={delta:.1f}s")
    print(f"Broker timestamp offset {hours:+d}h (fresh-tick check, residual {residual:.1f}s)")
    return hours


def fetch_frame(symbol, label, start_utc, end_utc, hours):
    """Download in 7-day chunks; verify raw alignment and coverage.

    CAUTION: An offset observed from today's tick does not prove historical
    server offset stayed unchanged during DST changes. This loader retains
    the v1 constant-offset assumption pending historical broker-time proof.
    """
    offset = timedelta(hours=hours)
    parts = []
    left = start_utc
    chunk_count = 0
    while left < end_utc:
        right = min(left + timedelta(days=7), end_utc)
        # The user's terminal returns broker-shifted epoch timestamps.
        arr = mt5.copy_rates_range(symbol, MT5_TF[label], left + offset, right + offset)
        chunk_count += 1
        if arr is None:
            raise RuntimeError(
                f"MT5 {label} history download failed for {left.isoformat()} "
                f"to {right.isoformat()}: {mt5.last_error()}"
            )
        if len(arr):
            chunk = pd.DataFrame(arr)
            raw_bad = int((chunk["time"].astype("int64") % SECONDS[label] != 0).sum())
            if raw_bad:
                samples = chunk.loc[chunk["time"] % SECONDS[label] != 0, "time"].head(3).tolist()
                raise RuntimeError(f"{label}: {raw_bad} raw unaligned candles in chunk {chunk_count}; samples={samples}")
            parts.append(chunk)
        else:
            print(f"  {label}: EMPTY chunk {left.date()} - {right.date()} (weekend/holiday or missing history)")
        left = right
    if not parts:
        raise RuntimeError(f"No {label} bars in requested window")
    df = pd.concat(parts, ignore_index=True).drop_duplicates("time").sort_values("time")
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=hours)
    df = df.loc[(df.time >= pd.Timestamp(start_utc)) & (df.time <= pd.Timestamp(end_utc))].reset_index(drop=True)
    if len(df) < 200:
        raise RuntimeError(f"Only {len(df)} {label} candles loaded")
    interval = SECONDS[label]
    # Compare timestamps using datetime arithmetic, independent of pandas'
    # internal timestamp resolution (s/ms/us/ns).
    bad = df["time"].ne(df["time"].dt.floor(f"{interval}s"))
    if bad.any():
        examples = df.loc[bad, "time"].head(5).astype(str).tolist()
        raise RuntimeError(f"{label} timestamp misalignment ({int(bad.sum())} bars); examples={examples}")
    gaps = df.time.diff().dt.total_seconds().dropna()
    unexpected = int(((gaps > interval * 1.5) & (gaps < 5 * 3600)).sum())
    print(f"  {label}: {len(df):,} unique bars from {chunk_count} chunks; "
          f"{unexpected} intraday gaps (review before interpreting performance)")
    df = calculate_indicators(df)
    prev = df.close.shift(1)
    true_range = pd.concat([df.high - df.low, (df.high-prev).abs(), (df.low-prev).abs()], axis=1).max(axis=1)
    df["atr14"] = true_range.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    df["close_time"] = df.time + pd.to_timedelta(interval, unit="s")
    return df


def h1_direction(df):
    x = df.iloc[-1]
    slope = float(x.ema50 - df.ema50.iloc[-4])
    if x.close > x.ema50 and x.plus_di > x.minus_di and slope > 0 and x.adx >= 20:
        return "BUY"
    if x.close < x.ema50 and x.minus_di > x.plus_di and slope < 0 and x.adx >= 20:
        return "SELL"
    return "HOLD"


def m5_confirm(df, side):
    x = df.iloc[-1]
    prior = df.iloc[-4:-1]
    vol_ok = x.tick_volume >= x.volume_avg
    vol_ok = bool(vol_ok)
    atr_ok = bool(x.atr14 > 0 and x.high-x.low <= 2*x.atr14)
    if side == "BUY":
        momentum = bool(x.close > x.open and x.close > prior.high.max() and x.close > x.ema50 and x.plus_di > x.minus_di)
    else:
        momentum = bool(x.close < x.open and x.close < prior.low.min() and x.close < x.ema50 and x.minus_di > x.plus_di)
    return momentum and vol_ok and atr_ok


def lookup_completed(df, decision):
    # strict: candle must have actually CLOSED by decision instant
    i = df.close_time.searchsorted(decision, side="right") - 1
    if i < 60:
        return None
    return i


def pip_spread(row, config):
    spread_points = float(row.get("spread", 0))
    if spread_points > 0:
        # EURUSD 5-digit quote: point 0.00001, pip 0.0001.
        return max(spread_points * 0.00001, 0.0)
    return config.fallback_spread_pips * config.pip_size


def simulate_trade(m5, start_idx, signal, decision, atr, equity, cfg):
    """Entry first available M5 OPEN at/after M15 close. Bid OHLC assumption.
    Long buys at ask and exits at bid; short sells at bid and buys back at ask.
    SL-first if both SL/TP reached in same M5 bar (conservative).
    """
    if start_idx >= len(m5):
        return None
    entrybar = m5.iloc[start_idx]
    if utc(entrybar.time) < decision:
        raise RuntimeError("Look-ahead entry check failed")
    if utc(entrybar.time) - decision > pd.Timedelta(minutes=10):
        return None  # missing candles/market closed
    spread = pip_spread(entrybar, cfg)
    slip = cfg.slippage_pips * cfg.pip_size
    bid = float(entrybar.open)
    entry = bid + spread + slip if signal == "BUY" else bid - slip
    sl_distance = float(atr) * cfg.atr_mult
    if not np.isfinite(sl_distance) or sl_distance <= 0:
        return None
    sl = entry - sl_distance if signal == "BUY" else entry + sl_distance
    tp = entry + cfg.reward_r*sl_distance if signal == "BUY" else entry - cfg.reward_r*sl_distance
    # Risk sizing includes anticipated stop-side slippage and exit spread (SELL).
    exit_cost = slip + (spread if signal == "SELL" else 0)
    risk_per_lot = ((sl_distance + exit_cost) / cfg.pip_size) * cfg.pip_value_per_lot + cfg.commission_per_lot_roundtrip
    if risk_per_lot <= 0:
        return None
    lots = math.floor(min(cfg.max_lots, cfg.risk * equity / risk_per_lot) / cfg.lot_step) * cfg.lot_step
    if lots + 1e-9 < cfg.min_lots:
        return None
    lots = round(lots, 2)
    # When signal generated on M15 close, starting with next M5 open; check that bar's range too.
    max_exit = utc(entrybar.time) + pd.Timedelta(hours=cfg.max_holding_hours)
    exit_price = None
    reason = None
    exit_time = None
    exit_spread = spread
    for j in range(start_idx, len(m5)):
        bar = m5.iloc[j]
        t = utc(bar.time)
        if t > max_exit:
            break
        s = pip_spread(bar, cfg)
        lo, hi = float(bar.low), float(bar.high)
        if signal == "BUY":
            stop_hit = lo <= sl
            target_hit = hi >= tp
        else:
            # Ask OHLC approximated by bid OHLC + bar spread
            stop_hit = hi + s >= sl
            target_hit = lo + s <= tp
        if stop_hit:  # conservative tie-break
            exit_price = sl - slip if signal == "BUY" else sl + slip
            reason = "STOP"; exit_time = t; exit_spread = s
            break
        if target_hit:
            exit_price = tp - slip if signal == "BUY" else tp + slip
            reason = "TARGET"; exit_time = t; exit_spread = s
            break
    if exit_price is None:
        j = min(len(m5)-1, max(start_idx, m5.time.searchsorted(max_exit, side="right")-1))
        bar = m5.iloc[j]
        exit_time = utc(bar.time)
        exit_spread = pip_spread(bar, cfg)
        exit_price = float(bar.close) - slip if signal == "BUY" else float(bar.close) + exit_spread + slip
        reason = "TIME" if exit_time >= max_exit - pd.Timedelta(minutes=5) else "END_OF_DATA"
    gross = (exit_price-entry if signal == "BUY" else entry-exit_price) / cfg.pip_size * cfg.pip_value_per_lot * lots
    commission = lots * cfg.commission_per_lot_roundtrip
    pnl = gross - commission
    return {"signal_time_utc": decision.isoformat(), "entry_time_utc": utc(entrybar.time).isoformat(),
            "exit_time_utc": exit_time.isoformat(), "direction": signal, "entry": round(entry, 6),
            "stop": round(sl, 6), "target": round(tp, 6), "exit": round(exit_price, 6),
            "entry_spread_pips": round(spread/cfg.pip_size, 2), "exit_spread_pips": round(exit_spread/cfg.pip_size, 2),
            "atr14": round(atr, 6), "lots": lots, "commission_usd": round(commission, 2),
            "pnl_usd": round(pnl, 2), "r_multiple": round(pnl/(equity*cfg.risk), 4), "exit_reason": reason}


def run_strategy(frames, cfg, variant, test_start, test_end):
    m5, m15, h1 = [frames[x] for x in ("M5", "M15", "H1")]
    trades = []
    equity = cfg.initial_equity
    eligible, passed = 0, 0
    occupied_until = pd.Timestamp.min.tz_localize("UTC")
    for k in range(100, len(m15)):
        candle = m15.iloc[k]
        decision = utc(candle.close_time)
        if decision < test_start or decision >= test_end:
            continue
        if decision <= occupied_until:
            continue
        # Exchange-hours/holiday gaps: do not assume missing recent M5/H1 bars are valid
        hidx = lookup_completed(h1, decision)
        midx = lookup_completed(m5, decision)
        if hidx is None or midx is None:
            continue
        if decision - utc(h1.iloc[hidx].close_time) > pd.Timedelta(hours=2):
            continue
        if decision - utc(m5.iloc[midx].close_time) > pd.Timedelta(minutes=10):
            continue
        # Signal evaluation strictly on data available at M15 close.
        side, _ = generate_signal(m15.iloc[k-1:k+1])
        if side not in ("BUY", "SELL"):
            continue
        eligible += 1
        if variant == "mtf":
            if h1_direction(h1.iloc[:hidx+1]) != side:
                continue
            if not m5_confirm(m5.iloc[:midx+1], side):
                continue
        passed += 1
        entryidx = m5.time.searchsorted(decision, side="left")
        t = simulate_trade(m5, entryidx, side, decision, float(candle.atr14), equity, cfg)
        if t is None:
            continue
        t["strategy"] = variant
        equity = max(0, equity + t["pnl_usd"])
        t["equity_after"] = round(equity, 2)
        trades.append(t)
        occupied_until = utc(t["exit_time_utc"])
        if equity <= 0:
            break
    return trades, {"candidate_signals": eligible, "confirmed_signals": passed}


def statistics(trades, cfg):
    if not trades:
        return {"trades": 0, "ending_equity": cfg.initial_equity, "net_profit_usd": 0, "return_pct": 0,
                "win_rate_pct": None, "profit_factor": None, "max_drawdown_pct": 0, "expectancy_usd": None}
    t = pd.DataFrame(trades)
    pnl = t.pnl_usd.astype(float)
    eq = pd.Series([cfg.initial_equity] + t.equity_after.astype(float).tolist())
    dd = (eq / eq.cummax() - 1) * 100
    gross_profit = pnl[pnl > 0].sum()
    gross_loss = abs(pnl[pnl < 0].sum())
    return {"trades": len(t), "ending_equity": round(float(eq.iloc[-1]), 2),
            "net_profit_usd": round(float(pnl.sum()), 2),
            "return_pct": round(float((eq.iloc[-1]/cfg.initial_equity-1)*100), 2),
            "win_rate_pct": round(float((pnl>0).mean()*100), 2),
            "profit_factor": round(float(gross_profit/gross_loss), 3) if gross_loss else None,
            "max_drawdown_pct": round(float(-dd.min()), 2),
            "expectancy_usd": round(float(pnl.mean()), 2),
            "avg_r_multiple": round(float(t.r_multiple.mean()), 3),
            "target_exits": int((t.exit_reason=="TARGET").sum()),
            "stop_exits": int((t.exit_reason=="STOP").sum()),
            "time_or_end_exits": int(t.exit_reason.isin(["TIME", "END_OF_DATA"]).sum())}


def main():
    p = argparse.ArgumentParser(description="Read-only MTF historical MT5 backtesting")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--risk", type=float, default=0.02)
    p.add_argument("--rr", type=float, default=3.0)
    p.add_argument("--initial-equity", type=float, default=10000)
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument("--offset-hours", type=int)
    p.add_argument("--strategy", choices=["baseline", "mtf", "both"], default="both")
    p.add_argument("--output", default="backtest_results")
    a = p.parse_args()
    cfg = Config(symbol=a.symbol.upper(), months=a.months, risk=a.risk, reward_r=a.rr,
                 initial_equity=a.initial_equity, offset_hours=a.offset_hours, strategy=a.strategy)
    if cfg.symbol != "EURUSD":
        raise ValueError("This v1 simulator only supports EURUSD (USD account, standard pip value assumptions)")
    if not (1 <= cfg.months <= 36 and 0 < cfg.risk <= 0.05 and 0 < cfg.reward_r <= 10 and cfg.initial_equity > 0):
        raise ValueError("Invalid months/risk/reward/equity")
    out = BASE / a.output
    out.mkdir(exist_ok=True)
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = (pd.Timestamp(end) - pd.DateOffset(months=cfg.months)).to_pydatetime()
    warmup = start - timedelta(days=cfg.warmup_days)
    print(f"Read-only {cfg.symbol} backtest: {start.isoformat()} to {end.isoformat()}; initial equity USD {cfg.initial_equity:.2f}")
    if not mt5.initialize():
        raise RuntimeError(f"MT5 unavailable: {mt5.last_error()}")
    try:
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("MT5 account not accessible")
        print(f"Account type: {'DEMO' if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else 'LIVE'} (READ ONLY)")
        if not mt5.symbol_select(cfg.symbol, True):
            raise RuntimeError(f"Symbol {cfg.symbol} unavailable")
        hours = broker_offset(cfg.symbol, cfg.offset_hours)
        print("Fetching historical bars (may take several minutes)...")
        frames = {k: fetch_frame(cfg.symbol, k, warmup, end, hours) for k in SECONDS}
        if utc(frames["M5"].time.min()) > pd.Timestamp(start) - pd.Timedelta(days=20):
            raise RuntimeError("Insufficient M5 history to warm up indicators; increase MT5 'Max bars in chart' or download history")
        # Require near-complete coverage to avoid backtest on an accidentally short period.
        if utc(frames["M5"].time.max()) < pd.Timestamp(end) - pd.Timedelta(days=5):
            raise RuntimeError("Recent M5 history missing")
    finally:
        mt5.shutdown()
    variants = ["baseline", "mtf"] if cfg.strategy == "both" else [cfg.strategy]
    all_trades, result_stats, funnel = [], {}, {}
    for variant in variants:
        print(f"Testing {variant}...")
        trades, counts = run_strategy(frames, cfg, variant, pd.Timestamp(start), pd.Timestamp(end))
        all_trades.extend(trades)
        result_stats[variant] = statistics(trades, cfg)
        funnel[variant] = counts
        print(variant, result_stats[variant])
    pd.DataFrame(all_trades, columns=["strategy", "signal_time_utc", "entry_time_utc", "exit_time_utc", "direction", "entry", "stop", "target", "exit", "entry_spread_pips", "exit_spread_pips", "atr14", "lots", "commission_usd", "pnl_usd", "r_multiple", "exit_reason", "equity_after"]).to_csv(out / "trades.csv", index=False)
    meta = {"created_utc": datetime.now(timezone.utc).isoformat(), "start_utc": start.isoformat(),
            "end_utc": end.isoformat(), "offset_hours": hours, "config": asdict(cfg),
            "stats": result_stats, "signal_funnel": funnel,
            "warnings": ["Historical bid OHLC + bar spread approximates ask; not tick-perfect.",
                         "Constant slippage and fixed commission are modelling assumptions.",
                         "SL-first tie-break for bars hitting both stops and targets.",
                         "M5 bar OHLC cannot reveal intrabar path; gaps and stop fills are approximated.",
                         "No financing/swap, broker-specific margin, or contract variations included.",
                         "No AI model performance is simulated; this is indicator-only.",
                         "Current +3h tick offset is provisionally used for historical bars; DST/timezone changes require validation.",
                         "Optimize and validate out of sample before trusting results."]}
    (out / "summary.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Saved {out / 'trades.csv'} and {out / 'summary.json'}")
    print("READ ONLY: no trade orders, no AI API calls")


if __name__ == "__main__":
    main()
