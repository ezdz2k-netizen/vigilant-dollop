
import MetaTrader5 as mt5
from datetime import datetime, timezone

SYMBOL = "EURUSD"

if not mt5.initialize():
    print("MT5 connection failed:", mt5.last_error())
    raise SystemExit(1)

try:
    tick = mt5.symbol_info_tick(SYMBOL)

    if tick is None:
        print("No tick available:", mt5.last_error())
        raise SystemExit(1)

    computer_utc = datetime.now(timezone.utc)

    tick_utc = datetime.fromtimestamp(
        tick.time, timezone.utc
    )

    difference = (
        tick_utc - computer_utc
    ).total_seconds()

    print("\n===== MT5 TIME COMPARISON =====")
    print("Computer UTC:", computer_utc)
    print("MT5 tick timestamp:", tick_utc)
    print("Difference (hours):", round(difference / 3600, 3))
    print("Difference (seconds):", round(difference))
    print("==============================")

finally:
    mt5.shutdown()
