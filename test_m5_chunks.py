
import MetaTrader5 as mt5
import pandas as pd

from datetime import datetime, timedelta, timezone

SYMBOL = "EURUSD"
DAYS_PER_CHUNK = 7
TOTAL_DAYS = 365

if not mt5.initialize():
    raise RuntimeError(mt5.last_error())

try:
    if not mt5.symbol_select(SYMBOL, True):
        raise RuntimeError(mt5.last_error())

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=TOTAL_DAYS)

    chunks = []
    cursor = start
    failed = []

    while cursor < end:
        chunk_end = min(
            cursor + timedelta(days=DAYS_PER_CHUNK),
            end
        )

        rates = mt5.copy_rates_range(
            SYMBOL,
            mt5.TIMEFRAME_M5,
            cursor,
            chunk_end
        )

        label = (
            f"{cursor.date()} to {chunk_end.date()}"
        )

        if rates is None:
            error = mt5.last_error()
            print(f"FAILED: {label} | {error}")
            failed.append((label, str(error)))

        elif len(rates) == 0:
            print(f"EMPTY: {label}")
            failed.append((label, "No bars"))

        else:
            print(f"OK: {label} | {len(rates)} bars")
            chunks.append(pd.DataFrame(rates))

        cursor = chunk_end

    if chunks:
        df = pd.concat(chunks, ignore_index=True)
        df = df.drop_duplicates(subset=["time"])
        df = df.sort_values("time").reset_index(drop=True)

        misaligned = int(
            (df["time"] % 300 != 0).sum()
        )

        print("\n===== DOWNLOAD SUMMARY =====")
        print("Total unique bars:", len(df))
        print("Misaligned M5 bars:", misaligned)
        print("Failed/empty chunks:", len(failed))
        print(
            "First raw timestamp:",
            pd.to_datetime(
                df["time"].iloc[0],
                unit="s", utc=True
            )
        )
        print(
            "Last raw timestamp:",
            pd.to_datetime(
                df["time"].iloc[-1],
                unit="s", utc=True
            )
        )

        if not failed and misaligned == 0:
            df.to_csv(
                "m5_history_raw.csv",
                index=False
            )
            print("Saved: m5_history_raw.csv")
        else:
            print(
                "Incomplete data: not exporting "
                "as a validated history."
            )

    if failed:
        print("\nFirst failed/empty chunks:")
        for item in failed[:10]:
            print(item)

finally:
    mt5.shutdown()

print("\nNo trades executed.")
