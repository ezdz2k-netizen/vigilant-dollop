"""Read-only XAUUSD walk-forward strategy research. No MT5 order functions.

Runs separate training/test parameter evaluations. Never treats broker-shifted epoch
seconds as verified UTC; timestamps are printed as RAW MT5 chart-clock labels.
"""
from __future__ import annotations
import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

TF_SECONDS = {"M5": 300, "M15": 900, "H1": 3600}
BASE = Path(__file__).resolve().parent

@dataclass(frozen=True)
class Params:
    adx_min: float
    atr_mult: float
    rr: float
    volume_mult: float
    breakout_atr: float


def wilder(series, n=14):
    # Wilder-style smoothing, approximate warm-up initialization.
    return series.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def features(frame):
    d = frame.copy().sort_values("time").reset_index(drop=True)
    c, h, l = d.close, d.high, d.low
    d["ema50"] = c.ewm(span=50, adjust=False).mean()
    mid = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)
    d["upper"] = mid + 2 * std
    d["lower"] = mid - 2 * std
    prev = c.shift()
    tr = pd.concat([h-l, (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    d["atr"] = wilder(tr)
    up, down = h.diff(), -l.diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0))
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0))
    d["pdi"] = 100 * wilder(plus) / d.atr.replace(0, np.nan)
    d["mdi"] = 100 * wilder(minus) / d.atr.replace(0, np.nan)
    dx = 100 * (d.pdi-d.mdi).abs()/(d.pdi+d.mdi).replace(0, np.nan)
    d["adx"] = wilder(dx)
    d["vol20"] = d.tick_volume.rolling(20).mean()
    d["close_time"] = d.time + pd.to_timedelta(d["bar_seconds"], unit="s")
    return d


def fetch_mt5(symbol, months, warmup_days, chunk_days, cache_dir):
    if mt5 is None:
        raise RuntimeError("Install MetaTrader5 into the Windows Python virtual environment")
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        if account is None:
            raise RuntimeError("Cannot read MT5 account")
        if account.currency != "USD":
            raise RuntimeError(f"Account currency {account.currency} is not USD; this USD-denominated P&L engine refuses to guess conversion")
        candidates = [symbol] if symbol else ["XAUUSD", "XAUUSD.", "GOLD"]
        info = next((mt5.symbol_info(s) for s in candidates if mt5.symbol_info(s) is not None), None)
        if info is None:
            raise RuntimeError("XAUUSD symbol not found. Specify broker symbol with --symbol")
        if not mt5.symbol_select(info.name, True):
            raise RuntimeError(f"Unable to select {info.name}: {mt5.last_error()}")
        if "USD" not in (info.currency_profit or "USD").upper():
            raise RuntimeError("This version supports USD-quoted gold only")
        props = {"symbol": info.name, "point": float(info.point),
                 "tick_size": float(info.trade_tick_size), "tick_value": float(info.trade_tick_value),
                 "contract_size": float(info.trade_contract_size),
                 "volume_min": float(info.volume_min), "volume_max": float(info.volume_max),
                 "volume_step": float(info.volume_step), "account_currency": account.currency,
                 "digits": int(info.digits), "currency_profit": info.currency_profit}
        if min(props["point"], props["tick_size"], props["contract_size"], props["volume_step"]) <= 0:
            raise RuntimeError("Invalid broker symbol properties")
        # Request broker-labelled timestamps directly; do not apply today's tick offset
        # across last year's DST boundaries.
        end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start = (pd.Timestamp(end) - pd.DateOffset(months=months)).to_pydatetime()
        warm = start-timedelta(days=warmup_days)
        frames = {}
        cache_dir.mkdir(parents=True, exist_ok=True)
        for label, seconds in TF_SECONDS.items():
            tf = getattr(mt5, "TIMEFRAME_" + label)
            parts, cursor, empty = [], warm, []
            while cursor < end:
                nxt = min(cursor+timedelta(days=chunk_days), end)
                rates = mt5.copy_rates_range(info.name, tf, cursor, nxt)
                if rates is None:
                    raise RuntimeError(f"{label} failed {cursor.date()}..{nxt.date()}: {mt5.last_error()}")
                if len(rates):
                    parts.append(pd.DataFrame(rates))
                else:
                    empty.append((cursor.isoformat(), nxt.isoformat()))
                cursor = nxt
            if not parts:
                raise RuntimeError(f"No {label} bars downloaded")
            df = pd.concat(parts, ignore_index=True).drop_duplicates("time").sort_values("time")
            if (df.time.astype("int64") % seconds != 0).any():
                raise RuntimeError(f"Raw {label} bars not aligned to interval")
            df["time"] = pd.to_datetime(df.time, unit="s", utc=True)
            df["bar_seconds"] = seconds
            df = features(df)
            df.to_csv(cache_dir / f"{label.lower()}_history.csv", index=False)
            frames[label] = df
            print(f"{label}: {len(df):,} bars, {len(empty)} empty chunks; raw first {df.time.iloc[0]}, last {df.time.iloc[-1]}")
        return frames, props, start, end
    finally:
        mt5.shutdown()


def setup_arrays(frames):
    """Precompute historical index lookups and signal-independent conditions.
    Each lookup uses only candles whose CLOSE <= M15 decision time.
    """
    a, b, h = frames["M5"], frames["M15"], frames["H1"]
    decisions = b.close_time.to_numpy(dtype="datetime64[ns]")
    hm = h.close_time.to_numpy(dtype="datetime64[ns]")
    mm = a.close_time.to_numpy(dtype="datetime64[ns]")
    ao = a.time.to_numpy(dtype="datetime64[ns]")
    hi = np.searchsorted(hm, decisions, side="right")-1
    mi = np.searchsorted(mm, decisions, side="right")-1
    entry = np.searchsorted(ao, decisions, side="left")
    # M5 prior 3 highs/lows excluded from current closed bar
    prior_hi = a.high.shift(1).rolling(3).max().to_numpy()
    prior_lo = a.low.shift(1).rolling(3).min().to_numpy()
    hc = h.close.to_numpy(); he = h.ema50.to_numpy()
    hp, hn, hadx = (h[z].to_numpy() for z in ["pdi", "mdi", "adx"])
    hdir = np.zeros(len(h), dtype="int8")
    rise = he > np.roll(he, 3)
    fall = he < np.roll(he, 3)
    hdir[(hc > he) & (hp > hn) & rise & (hadx >= 20)] = 1
    hdir[(hc < he) & (hn > hp) & fall & (hadx >= 20)] = -1
    hdir[:3] = 0
    # Store everything in numpy arrays for fast grid evaluation.
    return {"M5": a, "M15": b, "H1": h, "hi": hi, "mi": mi, "entry": entry,
            "hdir": hdir, "prior_hi": prior_hi, "prior_lo": prior_lo,
            "decisions": decisions, "m5times": ao,
            "m5closes": mm}


def evaluate(arr, props, p, *, start, end, initial_equity=10000., risk=.02,
             slippage_usd=.10, commission_per_lot=7., max_holding_hours=24,
             max_spread_usd=.7, strategy="mtf", collect=False):
    a, b = arr["M5"], arr["M15"]
    # Symbol quote USD and account USD; broker volume is lots of contract_size ounces.
    contract = props["contract_size"]
    step = props["volume_step"]
    risk = float(risk)
    times = b.close_time
    eq = initial_equity
    peak = eq
    maxdd = 0.
    pnl_list, recs = [], []
    candidates = confirmed = rejected_spread = 0
    occupied = pd.Timestamp("1970-01-01", tz="UTC")
    bh, bl, bc, bo = (b[z].to_numpy() for z in ["high","low","close","open"])
    bu, bd, be, ba, bv = (b[z].to_numpy() for z in ["upper","lower","ema50","adx","vol20"])
    bp, bn, batr, bvol = (b[z].to_numpy() for z in ["pdi","mdi","atr","tick_volume"])
    ah, al, ac, ao = (a[z].to_numpy() for z in ["high","low","close","open"])
    ae, ap, an, av, avol, aatr = (a[z].to_numpy() for z in ["ema50","pdi","mdi","vol20","tick_volume","atr"])
    for k in range(100, len(b)):
        when = times.iloc[k]
        if when < start or when >= end or when <= occupied:
            continue
        if not np.isfinite([bu[k], bd[k], batr[k], ba[k], bv[k]]).all() or batr[k] <= 0:
            continue
        side = 0
        if bc[k-1] <= bu[k-1] and bc[k] > bu[k] and bc[k] > be[k] and bp[k] > bn[k]:
            side = 1
            excursion = (bc[k]-bu[k])/batr[k]
        elif bc[k-1] >= bd[k-1] and bc[k] < bd[k] and bc[k] < be[k] and bn[k] > bp[k]:
            side = -1
            excursion = (bd[k]-bc[k])/batr[k]
        if not side or ba[k] < p.adx_min or bvol[k] < p.volume_mult*bv[k] or excursion < p.breakout_atr:
            continue
        candidates += 1
        hi, mi, ei = int(arr["hi"][k]), int(arr["mi"][k]), int(arr["entry"][k])
        if hi < 60 or mi < 60 or ei >= len(a):
            continue
        if when - b.time.iloc[k] != pd.Timedelta(minutes=15):
            raise RuntimeError("Invalid M15 bar time")
        if when - arr["H1"].close_time.iloc[hi] > pd.Timedelta(hours=2):
            continue
        if when - a.close_time.iloc[mi] > pd.Timedelta(minutes=10):
            continue
        if a.time.iloc[ei] - when > pd.Timedelta(minutes=10):
            continue
        if strategy == "mtf":
            if arr["hdir"][hi] != side:
                continue
            if not np.isfinite([ae[mi], ap[mi], an[mi], av[mi], aatr[mi]]).all() or aatr[mi] <= 0:
                continue
            if side == 1:
                match = ac[mi] > ao[mi] and ac[mi] > arr["prior_hi"][mi] and ac[mi] > ae[mi] and ap[mi] > an[mi]
            else:
                match = ac[mi] < ao[mi] and ac[mi] < arr["prior_lo"][mi] and ac[mi] < ae[mi] and an[mi] > ap[mi]
            if not match or avol[mi] < av[mi] or ah[mi]-al[mi] > 2*aatr[mi]:
                continue
        confirmed += 1
        entry_bar = a.iloc[ei]
        point = props["point"]
        sp = float(entry_bar.spread)*point if float(entry_bar.spread) > 0 else max_spread_usd
        if sp > max_spread_usd:
            rejected_spread += 1
            continue
        # Bid OHLC. Long opens at ask; short opens at bid.
        entry_price = float(entry_bar.open) + sp + slippage_usd if side == 1 else float(entry_bar.open)-slippage_usd
        distance = p.atr_mult*float(batr[k])
        if distance <= 0 or not np.isfinite(distance):
            continue
        stop = entry_price-side*distance
        target = entry_price+side*p.rr*distance
        # Size conservatively, including exit slippage/spread and commissions.
        loss_per_lot = (distance+slippage_usd+(max_spread_usd if side == -1 else 0))*contract+commission_per_lot
        volume = math.floor((risk*eq/loss_per_lot)/step)*step
        volume = round(min(volume, props["volume_max"]), 8)
        if volume < props["volume_min"]:
            continue
        exit_px = None; reason = "END"; exit_at = None
        for j in range(ei, len(a)):
            bar = a.iloc[j]
            ts = bar.time
            if ts >= a.time.iloc[ei]+pd.Timedelta(hours=max_holding_hours):
                break
            bs = float(bar.spread)*point if float(bar.spread) > 0 else max_spread_usd
            # Conservative stop-first ordering when both boundaries occur in same bar.
            if side == 1:
                hit_stop = float(bar.low) <= stop
                hit_target = float(bar.high) >= target
            else:
                hit_stop = float(bar.high)+bs >= stop
                hit_target = float(bar.low)+bs <= target
            if hit_stop:
                exit_px = stop-side*slippage_usd; reason="STOP"; exit_at=ts
                break
            if hit_target:
                exit_px = target-side*slippage_usd; reason="TARGET"; exit_at=ts
                break
        if exit_px is None:
            jj = min(len(a)-1, max(ei, int(np.searchsorted(arr["m5times"],
                          np.datetime64((a.time.iloc[ei]+pd.Timedelta(hours=max_holding_hours)).to_datetime64()), side="left"))-1))
            bar = a.iloc[jj]
            bs = float(bar.spread)*point if float(bar.spread) > 0 else max_spread_usd
            exit_px = float(bar.close)-slippage_usd if side == 1 else float(bar.close)+bs+slippage_usd
            reason="TIME"; exit_at=bar.time
        pnl = side*(exit_px-entry_price)*contract*volume - commission_per_lot*volume
        if not np.isfinite(pnl):
            raise RuntimeError("Non-finite PnL")
        eq += pnl
        peak = max(peak, eq)
        maxdd = max(maxdd, 100*(peak-eq)/peak)
        pnl_list.append(pnl)
        if collect:
            recs.append({"signal_time":when.isoformat(),"entry_time":entry_bar.time.isoformat(),
                         "exit_time":exit_at.isoformat(),"side":"BUY" if side==1 else "SELL",
                         "entry":round(entry_price,4),"stop":round(stop,4),"target":round(target,4),
                         "exit":round(exit_px,4),"lots":volume,"spread_usd":round(sp,4),
                         "pnl_usd":round(pnl,2),"equity_after":round(eq,2),"reason":reason,
                         "atr":round(float(batr[k]),4)})
        occupied = exit_at
        if eq <= 0:
            break
    wins = sum(1 for x in pnl_list if x > 0)
    gains = sum(x for x in pnl_list if x > 0)
    losses = -sum(x for x in pnl_list if x < 0)
    out={"trades":len(pnl_list),"win_rate_pct":round(100*wins/len(pnl_list),2) if pnl_list else None,
         "return_pct":round(100*(eq/initial_equity-1),2),"ending_equity":round(eq,2),
         "profit_factor":round(gains/losses,3) if losses else None,
         "max_drawdown_pct":round(maxdd,2),
         "avg_pnl_usd":round(sum(pnl_list)/len(pnl_list),2) if pnl_list else None,
         "candidates":candidates,"confirmed":confirmed,"rejected_spread":rejected_spread}
    return out, recs


def sort_key(result):
    # Penalize drawdown and small sample count; no optimistic extrapolation.
    n = result["trades"]
    return (result["return_pct"]-0.6*result["max_drawdown_pct"]-max(0, 15-n)*2)


def main():
    ap=argparse.ArgumentParser(description="Research-only XAUUSD parameter optimizer")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--train-months", type=int, default=8)
    ap.add_argument("--initial-equity", type=float, default=10000)
    ap.add_argument("--risk", type=float, default=.02)
    ap.add_argument("--slippage-usd", type=float, default=.10, help="USD per ounce per side")
    ap.add_argument("--max-spread-usd", type=float, default=.70, help="USD per ounce")
    ap.add_argument("--commission", type=float, default=7., help="USD per lot roundtrip")
    ap.add_argument("--grid", choices=["small","medium"], default="small")
    ap.add_argument("--strategy", choices=["baseline","mtf"], default="mtf")
    ap.add_argument("--output", default="xau_optimization_results")
    args=ap.parse_args()
    if not (3 <= args.months <= 24 and 1 <= args.train_months < args.months and 0 < args.risk <= .02):
        raise ValueError("Require 3-24 months, train-months < months, risk <= 0.02")
    if min(args.initial_equity,args.max_spread_usd,args.slippage_usd+0.00001) <= 0 or args.commission < 0:
        raise ValueError("Invalid cost/equity inputs")
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    frames,props,start,end=fetch_mt5(args.symbol,args.months,100,7,out/"history")
    arr=setup_arrays(frames)
    # Anchor split at broker-labelled historical date; not verified UTC.
    train_end=(pd.Timestamp(start)+pd.DateOffset(months=args.train_months))
    train_start=pd.Timestamp(start)
    test_end=pd.Timestamp(end)
    print("BROKER RAW CLOCK LABELS: training",train_start,"to",train_end,"; holdout",train_end,"to",test_end)
    values = ([20,25,30],[1.5,2.0],[2.,3.],[1.,1.2],[0.,.10]) if args.grid=="small" else ([18,22,26,30],[1.2,1.5,2.0],[1.5,2.,3.],[.9,1.0,1.2],[0.,.05,.15])
    settings=[Params(*x) for x in product(*values)]
    print(f"Evaluating {len(settings)} training configurations...")
    results=[]
    for index,p in enumerate(settings,1):
        metrics,_=evaluate(arr,props,p,start=train_start,end=train_end,initial_equity=args.initial_equity,
                           risk=args.risk,slippage_usd=args.slippage_usd,
                           commission_per_lot=args.commission,max_spread_usd=args.max_spread_usd,
                           strategy=args.strategy)
        results.append({**asdict(p),**metrics,"score":round(sort_key(metrics),3)})
        if index%20==0:print(f"  {index}/{len(settings)} done")
    ranking=pd.DataFrame(results).sort_values("score",ascending=False)
    ranking.to_csv(out/"training_grid.csv",index=False)
    top=ranking.iloc[0]
    chosen=Params(*(float(top[k]) for k in ["adx_min","atr_mult","rr","volume_mult","breakout_atr"]))
    train_stat,train_trades=evaluate(arr,props,chosen,start=train_start,end=train_end,initial_equity=args.initial_equity,
                                     risk=args.risk,slippage_usd=args.slippage_usd,commission_per_lot=args.commission,
                                     max_spread_usd=args.max_spread_usd,strategy=args.strategy,collect=True)
    test_stat,test_trades=evaluate(arr,props,chosen,start=train_end,end=test_end,initial_equity=args.initial_equity,
                                   risk=args.risk,slippage_usd=args.slippage_usd,commission_per_lot=args.commission,
                                   max_spread_usd=args.max_spread_usd,strategy=args.strategy,collect=True)
    pd.DataFrame(train_trades).to_csv(out/"training_trades.csv",index=False)
    pd.DataFrame(test_trades).to_csv(out/"holdout_trades.csv",index=False)
    report={"symbol_properties":props,"research_params":{"months":args.months,"train_months":args.train_months,
            "risk_fraction":args.risk,"initial_equity":args.initial_equity,"slippage_usd_per_ounce_each_side":args.slippage_usd,
            "max_spread_usd_per_ounce":args.max_spread_usd,"commission_usd_per_lot_roundtrip":args.commission,
            "strategy":args.strategy,"grid":args.grid},"training_start_raw":str(train_start),
            "training_end_raw":str(train_end),"holdout_end_raw":str(test_end),
            "selected_training_parameters":asdict(chosen),"training":train_stat,"holdout":test_stat,
            "cautions":["No live orders; MT5 OHLC data only; no OpenAI API calls.",
              "Raw MT5 timestamp labels are NOT independently proven to represent UTC across DST changes.",
              "Gold spread, slippage and commission models require broker-specific verification.",
              "USD profit assumes USD-quoted symbol, USD account and contract_size * price movement.",
              "M5 intrabar ordering unknown; stop-first, gaps and fills are approximated.",
              "No broker margin checks, swaps, overnight financing, partial fills or news-event handling.",
              "Train/holdout split avoids tuning on holdout, but one split does not establish robustness."]}
    (out/"optimization_report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print("TRAIN:",train_stat,"\nHOLDOUT:",test_stat,"\nPARAMS:",asdict(chosen))
    print("Saved:",out)
    print("READ ONLY: no orders sent")

if __name__=="__main__":main()
