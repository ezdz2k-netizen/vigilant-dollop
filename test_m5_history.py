
import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone

SYMBOL = "EURUSD"

if not mt5.initialize():
    raise RuntimeError(mt5.last_error())

try:
    mt5.symbol_select(SYMBOL, True)

    print("M5 timeframe constant:", mt5.TIMEFRAME_M5)

    # Test 1: Most recent 100 completed candles
    print("\nTEST 1: Last 100 M5 candles")

    rates = mt5.copy_rates_from_pos(
        SYMBOL,
        mt5.TIMEFRAME_M5,
        1,
        100
    )

    if rates is None:
        print("FAILED:", mt5.last_error())
    else:
        print("SUCCESS:", len(rates), "candles")

    # Test 2: Last 7 days
    print("\nTEST 2: Seven days of M5 history")

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=7)

    rates = mt5.copy_rates_range(
        SYMBOL,
        mt5.TIMEFRAME_M5,
        start,
        now
    )

    if rates is None:
        print("FAILED:", mt5.last_error())
    else:
        print("SUCCESS:", len(rates), "candles")

        if len(rates):
            print(
                "First raw timestamp:",
                datetime.fromtimestamp(
                    int(rates[0]["time"]),
                    timezone.utc
                )
            )

            print(
                "Last raw timestamp:",
                datetime.fromtimestamp(
                    int(rates[-1]["time"]),
                    timezone.utc
                )
            )

            print(
                "Misaligned M5 bars:",
                sum(int(r["time"]) % 300 != 0 for r in rates)
            )

finally:
    mt5.shutdown()

print("\nNo trades executed.")
