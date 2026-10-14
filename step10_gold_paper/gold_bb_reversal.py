"""Read-only FxPro GOLD triple-timeframe Bollinger reversal research.

All broker bar timestamps are treated as broker-clock labels, NOT asserted UTC.
Signals are evaluated only after their respective timeframe closes.
No orders, logins or write calls to MT5 trading API.
"""
import argparse
import json
import math
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

TF = {'M15': 900, 'H1': 3600, 'H4': 14400}
MODES = ('strict', 'sequential', 'flexible')
TARGETS = ('middle', 'opposite')


def args_parse():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbol', default='GOLD')
    p.add_argument('--months', type=int, default=12)
    p.add_argument('--risk', type=float, default=0.02)
    p.add_argument('--starting-equity', type=float, default=10000)
    p.add_argument('--slippage-price', type=float, default=0.10, help='Adverse GOLD price movement per side, e.g. 0.10 USD/oz')
    p.add_argument('--fallback-spread-price', type=float, default=0.30)
    p.add_argument('--commission-lot-roundtrip', type=float, default=7.0)
    p.add_argument('--atr-stop-buffer', type=float, default=0.25)
    p.add_argument('--max-hold-hours', type=int, default=48)
    p.add_argument('--window-hours', type=int, default=12)
    p.add_argument('--m15-reentry-bars', type=int, default=4)
    p.add_argument('--output', default='gold_reversal_results')
    a = p.parse_args()
    if not (0 < a.risk <= .05 and 0 < a.months <= 36 and a.starting_equity > 0 and a.slippage_price >= 0 and a.fallback_spread_price >= 0 and a.atr_stop_buffer >= 0 and a.window_hours > 0 and a.m15_reentry_bars >= 1):
        p.error('Invalid risk, date range, cost, or window settings')
    return a


def fetch_5min(symbol, start, end):
    frames = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=7), end)
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, cursor, nxt)
        if rates is None:
            raise RuntimeError(f'MT5 M5 request failed {cursor} to {nxt}: {mt5.last_error()}')
        if len(rates):
            frames.append(pd.DataFrame(rates))
        else:
            print(f'Empty broker history window: {cursor.date()} to {nxt.date()}')
        cursor = nxt
    if not frames:
        raise RuntimeError('No GOLD M5 history available')
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=['time']).sort_values('time').reset_index(drop=True)
    if (df.time.to_numpy(dtype='int64') % 300 != 0).any():
        raise RuntimeError('Raw M5 bar openings are not aligned to 5-minute boundaries')
    df['open_time'] = pd.to_datetime(df.time, unit='s', utc=True)
    df = df.set_index('open_time')
    for c in ['open', 'high', 'low', 'close']:
        df[c] = df[c].astype(float)
    df['tick_volume'] = df['tick_volume'].astype(float)
    return df


def aggregate(m5, seconds):
    rule = {900: '15min', 3600: '1h', 14400: '4h'}[seconds]
    f = m5.resample(rule, label='left', closed='left', origin='epoch').agg(
        open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
        close=('close', 'last'), tick_volume=('tick_volume', 'sum'), bars=('close', 'count')
    )
    # Avoid using partially missing aggregation groups, including weekends.
    expected = seconds // 300
    f = f[(f.bars == expected)].copy()
    # BB band at CURRENT completed candle for re-entry, PREVIOUS completed BB for wick sweep.
    basis = f.close.rolling(20, min_periods=20).mean()
    std = f.close.rolling(20, min_periods=20).std(ddof=0)
    f['middle'] = basis
    f['upper'] = basis + 2 * std
    f['lower'] = basis - 2 * std
    f['upper_before'] = f.upper.shift(1)
    f['lower_before'] = f.lower.shift(1)
    prev = f.close.shift(1)
    tr = pd.concat([(f.high-f.low).abs(), (f.high-prev).abs(), (f.low-prev).abs()], axis=1).max(axis=1)
    f['atr'] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    f['close_time'] = f.index + pd.Timedelta(seconds=seconds)
    f['block'] = f.index.floor('4h')
    f['buy_sweep'] = f.low < f.lower_before
    f['sell_sweep'] = f.high > f.upper_before
    return f.dropna(subset=['middle', 'upper_before', 'lower_before', 'atr'])


def select_signals(m15, h1, h4, cfg):
    # An event becomes observable ONLY at the completed candle's close_time.
    events = {}
    for tf, df in [('M15', m15), ('H1', h1), ('H4', h4)]:
        for side in ('buy', 'sell'):
            flag = f'{side}_sweep'
            events[(tf, side)] = [
                {'time': r.close_time, 'block': r.block, 'low': float(r.low), 'high': float(r.high)}
                for r in df.itertuples() if getattr(r, flag)
            ]
    event_times = {key: [e['time'] for e in values] for key, values in events.items()}

    def recent(tf, side, low, high):
        key = (tf, side)
        times = event_times[key]
        return events[key][bisect_left(times, low):bisect_right(times, high)]

    results = {mode: [] for mode in MODES}
    used = {(m, s): set() for m in MODES for s in ('buy', 'sell')}
    window = pd.Timedelta(hours=cfg.window_hours)
    # At each M15 completed candle, look for a recent M15 wick sweep and re-entry close.
    for row in m15.itertuples():
        t = row.close_time
        for side in ('buy', 'sell'):
            inside = row.close > row.lower and row.close < row.upper
            if not inside:
                continue
            m_ev = recent('M15', side, t - pd.Timedelta(minutes=15*(cfg.m15_reentry_bars-1)), t)
            if not m_ev:
                continue
            m = m_ev[-1]
            h1ev = recent('H1', side, t-window, t)
            h4ev = recent('H4', side, t-window, t)
            if not h1ev or not h4ev:
                continue
            for mode in MODES:
                pairs = []
                if mode == 'strict':
                    # Strict means all 3 *breach candles* opened in the same 4h broker block.
                    # Therefore an H4 breach is known only AFTER that block has completed.
                    pairs = [(a,b) for a in h4ev for b in h1ev if a['block'] == b['block'] == m['block'] and t >= a['time']]
                    # Require entry confirmation within one hour after the H4 event became known.
                    pairs = [(a,b) for a,b in pairs if t-a['time'] <= pd.Timedelta(hours=1)]
                elif mode == 'sequential':
                    pairs = [(a,b) for a in h4ev for b in h1ev if a['time'] <= b['time'] <= m['time'] <= t]
                else:
                    pairs = [(a,b) for a in h4ev for b in h1ev if max(a['time'], b['time'], m['time']) - min(a['time'], b['time'], m['time']) <= window]
                if not pairs:
                    continue
                a,b = max(pairs, key=lambda p: (p[0]['time'],p[1]['time']))
                sigkey = (str(a['time']), str(b['time']), str(m['time']))
                if sigkey in used[(mode,side)]:
                    continue
                used[(mode,side)].add(sigkey)
                results[mode].append({
                    'time': t, 'side': 'BUY' if side=='buy' else 'SELL',
                    'mode': mode, 'sweep_low': min(e['low'] for e in m_ev if e['time'] >= m['time']),
                    'sweep_high': max(e['high'] for e in m_ev if e['time'] >= m['time']),
                    'middle': float(row.middle), 'opposite': float(row.upper if side=='buy' else row.lower),
                    'atr': float(row.atr), 'h4_event': a['time'], 'h1_event': b['time'], 'm15_event': m['time'],
                })
    return results


def quantize_volume(value, info):
    step, minimum, maximum = float(info.volume_step), float(info.volume_min), float(info.volume_max)
    if step <= 0 or minimum <= 0 or maximum < minimum:
        raise RuntimeError('Invalid broker volume specifications')
    if value < minimum:
        return 0.0
    return min(maximum, math.floor((value + 1e-12) / step) * step)


_SIM_CACHE = {}

def simulate(m5, signals, info, cfg, target_name, split_time):
    ts = m5.index
    key = id(m5)
    if key not in _SIM_CACHE:
        _SIM_CACHE.clear()
        _SIM_CACHE[key] = list(m5.itertuples())
    rows = _SIM_CACHE[key]
    last_exit = pd.Timestamp.min.tz_localize('UTC')
    equity = cfg.starting_equity
    trades = []
    tick_size = float(info.trade_tick_size)
    tick_value = float(info.trade_tick_value)
    point = float(info.point)
    if min(tick_size, tick_value, point) <= 0:
        raise RuntimeError('GOLD broker tick size/value/point unavailable; cannot risk-size')
    for s in sorted(signals, key=lambda x:x['time']):
        if s['time'] < last_exit:
            continue
        start_i = ts.searchsorted(s['time'], side='left')
        if start_i >= len(rows):
            continue
        entryrow = rows[start_i]
        if entryrow.Index > s['time'] + pd.Timedelta(minutes=15):
            continue  # no next M5 bar: don't enter after a gap
        buy = s['side'] == 'BUY'
        spread = float(entryrow.spread) * point if hasattr(entryrow,'spread') and float(entryrow.spread) > 0 else cfg.fallback_spread_price
        # MT5 bar OHLC assumed bid. BUY enters ask; SELL enters bid.
        entry = float(entryrow.open) + (spread if buy else 0) + (cfg.slippage_price if buy else -cfg.slippage_price)
        stop = (s['sweep_low'] - cfg.atr_stop_buffer*s['atr']) if buy else (s['sweep_high'] + cfg.atr_stop_buffer*s['atr'] + spread)
        level = s['middle'] if target_name=='middle' else s['opposite']
        target = level if buy else level + spread  # sell closes by buying ask
        risk_price = (entry-stop) if buy else (stop-entry)
        reward_price = (target-entry) if buy else (entry-target)
        if risk_price <= 0 or reward_price <= 0:
            continue
        value_per_lot = risk_price / tick_size * tick_value
        lot = quantize_volume(equity * cfg.risk / (value_per_lot + cfg.commission_lot_roundtrip), info)
        if not lot:
            continue
        exit_at = None
        exit_price = None
        exit_reason = 'TIME'
        max_end = entryrow.Index + pd.Timedelta(hours=cfg.max_hold_hours)
        for r in rows[start_i:]:
            if r.Index > max_end:
                break
            curr_spread = float(r.spread)*point if hasattr(r,'spread') and float(r.spread)>0 else cfg.fallback_spread_price
            if buy:
                hit_stop = float(r.low) <= stop
                hit_tp = float(r.high) >= target
            else:
                hit_stop = float(r.high) + curr_spread >= stop
                hit_tp = float(r.low) + curr_spread <= target
            if hit_stop or hit_tp:
                # Conservative when stop and target both appear in one 5m bar.
                exit_reason = 'STOP' if hit_stop else 'TARGET'
                exit_price = (stop if hit_stop else target) + (-cfg.slippage_price if buy else cfg.slippage_price)
                exit_at = r.Index + pd.Timedelta(minutes=5)
                break
            exit_price = float(r.close) + (0 if buy else curr_spread) + (-cfg.slippage_price if buy else cfg.slippage_price)
            exit_at = r.Index + pd.Timedelta(minutes=5)
        if exit_at is None:
            continue
        pnl_price = (exit_price-entry) if buy else (entry-exit_price)
        gross = pnl_price/tick_size*tick_value*lot
        commission = lot*cfg.commission_lot_roundtrip
        net = gross-commission
        before = equity
        equity += net
        last_exit = exit_at
        trades.append({
            'variant': s['mode'], 'target': target_name, 'side': s['side'],
            'entry_broker_clock': str(entryrow.Index), 'exit_broker_clock': str(exit_at),
            'signal_broker_clock': str(s['time']), 'period': 'holdout' if s['time'] >= split_time else 'training',
            'entry_price': round(entry,5),'exit_price': round(exit_price,5), 'stop_price': round(stop,5),
            'target_price': round(target,5),'lots': round(lot,4), 'spread_price_at_entry': round(spread,5),
            'atr_m15':round(s['atr'],5), 'gross_pnl':round(gross,2),'commission':round(commission,2),
            'net_pnl':round(net,2), 'r_multiple':round(net/(value_per_lot*lot+commission),4),
            'equity_before':round(before,2), 'equity_after':round(equity,2), 'exit_reason':exit_reason,
            'h4_event_broker_clock':str(s['h4_event']),'h1_event_broker_clock':str(s['h1_event'])
        })
    return trades


def summarize(trades, initial_equity):
    if not trades:
        return {'trades':0, 'return_pct':0.0, 'profit_factor':None, 'max_drawdown_pct':0.0, 'win_rate_pct':None, 'net_pnl':0.0, 'avg_r':None}
    pnl = np.array([r['net_pnl'] for r in trades],dtype=float)
    gross_profit = pnl[pnl>0].sum()
    gross_loss = -pnl[pnl<0].sum()
    equity = initial_equity + np.cumsum(pnl)
    peaks = np.maximum.accumulate(np.r_[initial_equity,equity])[1:]
    dd = 100*np.max((peaks-equity)/peaks)
    return {'trades':len(trades),'return_pct':round(100*pnl.sum()/initial_equity,2),
            'profit_factor':round(float(gross_profit/gross_loss),3) if gross_loss>0 else None,
            'max_drawdown_pct':round(float(dd),2),'win_rate_pct':round(float(100*np.mean(pnl>0)),2),
            'net_pnl':round(float(pnl.sum()),2), 'avg_r':round(float(np.mean([x['r_multiple'] for x in trades])),3)}


def main():
    a = args_parse()
    path = Path(a.output)
    path.mkdir(parents=True,exist_ok=True)
    if not mt5.initialize():
        raise RuntimeError(f'MT5 initialize failed: {mt5.last_error()}')
    try:
        if not mt5.symbol_select(a.symbol,True):
            raise RuntimeError(f'Symbol {a.symbol!r} not found: {mt5.last_error()}')
        info = mt5.symbol_info(a.symbol)
        account = mt5.account_info()
        if info is None or account is None:
            raise RuntimeError('Missing symbol or account specifications')
        print('MT5 account:', 'DEMO' if account.trade_mode==mt5.ACCOUNT_TRADE_MODE_DEMO else 'LIVE', '(READ-ONLY)')
        print('GOLD specs:', {k:getattr(info,k,None) for k in ['point','trade_tick_size','trade_tick_value','trade_contract_size','volume_min','volume_step','volume_max']})
        end = datetime.now(timezone.utc).replace(second=0,microsecond=0)
        start = end - pd.DateOffset(months=a.months)
        warmup = start - timedelta(days=45)
        print('Fetching broker-clock M5 candles in seven-day chunks...')
        m5 = fetch_5min(a.symbol,warmup,end+timedelta(hours=6))
    finally:
        mt5.shutdown()
    m15=aggregate(m5,900)
    h1=aggregate(m5,3600)
    h4=aggregate(m5,14400)
    if len(h4)<100:
        raise RuntimeError('Insufficient H4 bars for 20-period Bollinger Bands')
    # Raw broker-clock index is used consistently. Avoid pretending it is historical UTC.
    begin_label = pd.Timestamp(start).tz_convert('UTC')
    end_label = pd.Timestamp(end).tz_convert('UTC')
    signals = select_signals(m15,h1,h4,a)
    split = begin_label + (end_label-begin_label)*2/3
    all_trades=[]
    funnel=[]
    stats=[]
    for mode in MODES:
        sigs=[s for s in signals[mode] if begin_label <= s['time'] <= end_label]
        for target in TARGETS:
            tx=simulate(m5,sigs,info,a,target,split)
            all_trades.extend(tx)
            funnel.append({'variant':mode,'target':target,'candidate_signals':len(sigs),'executed_trades':len(tx)})
            for period in ('training','holdout'):
                subset=[x for x in tx if x['period']==period]
                # This is a fixed configuration test, not an optimization.
                stat=summarize(subset,a.starting_equity)
                stats.append({'variant':mode,'target':target,'period':period, **stat})
    trade_columns = ['variant','target','side','entry_broker_clock','exit_broker_clock','signal_broker_clock','period', 'entry_price','exit_price','stop_price','target_price','lots','spread_price_at_entry','atr_m15','gross_pnl','commission','net_pnl','r_multiple','equity_before','equity_after','exit_reason','h4_event_broker_clock','h1_event_broker_clock']
    pd.DataFrame(all_trades, columns=trade_columns).to_csv(path/'trades.csv',index=False)
    pd.DataFrame(stats).to_csv(path/'comparison.csv',index=False)
    pd.DataFrame(funnel).to_csv(path/'signal_counts.csv',index=False)
    report = {
        'symbol':a.symbol,'generated_utc':datetime.now(timezone.utc).isoformat(),
        'time_basis':'RAW BROKER CLOCK EPOCH LABELS; NOT VERIFIED HISTORICAL UTC',
        'observed_data_first':str(m5.index.min()),'observed_data_last':str(m5.index.max()),
        'm5_bars':len(m5),'m15_bars':len(m15),'h1_bars':len(h1),'h4_bars':len(h4),
        'config':vars(a),'split_broker_clock':str(split), 'results':stats,
        'warnings':[
            'H4/H1 breaches only become known at candle close; no developing H4/H1 candle inference.',
            'Strict is same completed H4 block plus reversal within 1h after H4 close.',
            'Historical timestamp offset and DST not normalized; all time labels are broker-clock.',
            'Backtest is based on M5 OHLC bid approximations, not true tick execution.',
            'Exit if both stop and target touch in same bar assumes stop first.',
            'Entry/exit slippage and commission are assumptions; no swap, funding, margin call simulation.',
            'Training/holdout slices are descriptive: no parameter search was conducted.',
            'Risk percentage applied to independent simulated equity path for each mode/target.',
            'No trading orders sent. Not evidence of forward profitability.'
        ]
    }
    (path/'report.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print('\nDONE: Results written to',path.resolve())
    print(pd.DataFrame(stats).to_string(index=False))
    print('NO TRADES EXECUTED')

if __name__=='__main__':
    main()
