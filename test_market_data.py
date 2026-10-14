
import MetaTrader5 as mt5
import pandas as pd

print("Starting MT5 Market Data Test...")

if not mt5.initialize():
    print("MT5 connection failed:", mt5.last_error())
    raise SystemExit(1)

try:
    symbol = "EURUSD"

    print(f"Connecting to {symbol}...")

    if not mt5.symbol_select(symbol, True):
        print("Symbol selection failed:", mt5.last_error())
        raise SystemExit(1)

    rates = mt5.copy_rates_from_pos(
        symbol,
        mt5.TIMEFRAME_M15,
        1,
        50
    )

    if rates is None or len(rates) == 0:
        print("No market data received:", mt5.last_error())
        raise SystemExit(1)

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(
        df["time"], unit="s", utc=True
    )

    print("\nMT5 MARKET DATA CONNECTED")
    print(f"Symbol: {symbol}")
    print(f"Candles retrieved: {len(df)}")

    print("\nLast 10 completed M15 candles:")
    print(df[[
        "time", "open", "high",
        "low", "close", "tick_volume"
    ]].tail(10).to_string(index=False))

finally:
    mt5.shutdown()
    print("\nMT5 connection closed.")
