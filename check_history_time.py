
from datetime import datetime, timedelta, timezone
from collections import Counter

import MetaTrader5 as mt5
import pandas as pd

SYMBOL = "EURUSD"

if not mt5.initialize():
    raise RuntimeError(
        f"MT5 connection failed: {mt5.last_error()}"
    )

try:
    if not mt5.symbol_select(SYMBOL, True):
        raise RuntimeError("Cannot select EURUSD")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365)

    for label, timeframe, seconds in [
        ("M5", mt5.TIMEFRAME_M5, 300),
        ("M15", mt5.TIMEFRAME_M15, 900),
        ("H1", mt5.TIMEFRAME_H1, 3600),
    ]:
        rates = mt5.copy_rates_range(
            SYMBOL, timeframe, start, end
        )

        if rates is None or len(rates) == 0:
            print(label, "No data:", mt5.last_error())
            continue

        df = pd.DataFrame(rates)

        # Inspect raw epoch timestamps without
        # applying the current broker offset.
        timestamps = pd.to_datetime(
            df["time"], unit="s", utc=True
        )

        residues = df["time"] % seconds
        counts = Counter(residues.tolist())

        print(f"\n===== {label} HISTORY =====")
        print("Bars:", len(df))
        print("First:", timestamps.iloc[0])
        print("Last:", timestamps.iloc[-1])
        print("Alignment residues:", counts.most_common(10))

        bad = df.loc[residues != 0].head(10)

        print("Misaligned bars:", int((residues != 0).sum()))

        if not bad.empty:
            print("\nFirst misaligned timestamps:")
            print(
                pd.to_datetime(
                    bad["time"], unit="s", utc=True
                ).to_string(index=False)
            )

finally:
    mt5.shutdown()

print("\nDiagnostic complete. No trades executed.")
