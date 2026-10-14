"""Read-only diagnostics for GOLD exhaustion feature generation.
Place beside gold_exhaustion.py, or run from the project folder.
No orders and no output trades.
"""
import argparse
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
ENGINE_DIR = ROOT / 'gold_exhaustion_research'
if ENGINE_DIR.is_dir():
    sys.path.insert(0, str(ENGINE_DIR))
import gold_exhaustion as gx


def inspect(m5, tf):
    sec = {'H1': 3600, 'H4': 14400}[tf]
    bars_per = sec // 300
    rule = '1h' if tf == 'H1' else '4h'
    blocks = m5.resample(rule, closed='left', label='left', origin='epoch').agg(
        bars=('close', 'size'), close=('close', 'last'))
    active = blocks[blocks.bars > 0]
    full = blocks[blocks.bars == bars_per]
    partial = active[active.bars != bars_per]
    print(f'\n{tf} blocks: total calendar blocks={len(blocks)}, active={len(active)}, full={len(full)}, partially populated={len(partial)}, empty={int((blocks.bars==0).sum())}')
    if len(partial):
        print('Examples of partially populated blocks (session breaks may cause these):')
        print(partial[['bars']].tail(8).to_string())
    lookup = gx.DevelopingBandLookup(m5, tf)
    latest_start = m5.index[-1] - pd.Timedelta(days=14)
    trial_times = pd.date_range(latest_start.ceil('15min'), m5.index[-1].floor('15min'), freq='15min', tz='UTC')
    tested = 0
    available = 0
    sample = []
    for t in trial_times:
        result = lookup.at(t)
        tested += 1
        if result is not None:
            available += 1
            if len(sample) < 3:
                sample.append(str(t))
    print(f'Latest 14 calendar days: lookup available at {available}/{tested} M15 boundaries')
    print('First available example timestamps:', sample if sample else 'NONE')
    return available


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--symbol', default='GOLD')
    p.add_argument('--months', type=int, default=12)
    p.add_argument('--cache', default='', help='Optional pre-downloaded raw MT5 M5 CSV')
    args = p.parse_args()
    m5, _ = gx.load_history(args.symbol, args.months, args.cache)
    print(f'M5 loaded: {len(m5):,} bars; first={m5.index[0]}, last={m5.index[-1]}')
    print('Pandas:', pd.__version__, 'Band parity version:', gx.BAND_PARITY_VERSION)
    a = inspect(m5, 'H1')
    b = inspect(m5, 'H4')
    print('\nCONCLUSION:')
    if b == 0:
        print('H4 lookup produced no values over the tested period. Do NOT run or interpret H4-only/H1+H4 backtests yet.')
        print('Check whether GOLD daily trading breaks produce partial H4 candles. The code may need session-aware candle reconstruction.')
    elif a == 0:
        print('H1 lookup returned no values: inspect H1 gaps and timestamp convention before backtesting.')
    else:
        print('At least some higher-timeframe values are available. If build_features still has no records, inspect its additional filters.')
    print('Diagnostic completed. No orders executed.')

if __name__ == '__main__':
    main()
