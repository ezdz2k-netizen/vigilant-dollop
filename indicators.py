
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

SYMBOL = "EURUSD"
TIMEFRAME = mt5.TIMEFRAME_M15
BARS = 250


def calculate_indicators(df):
    df = df.copy()

    # Bollinger Bands (20, 2)
    df["bb_middle"] = df["close"].rolling(20).mean()
    std = df["close"].rolling(20).std(ddof=0)
    df["bb_upper"] = df["bb_middle"] + 2 * std
    df["bb_lower"] = df["bb_middle"] - 2 * std

    # EMA 50
    df["ema50"] = df["close"].ewm(
        span=50, adjust=False
    ).mean()

    # ADX, +DI, -DI (Wilder smoothing)
    period = 14

    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move, 0.0
        ), index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move, 0.0
        ), index=df.index
    )

    previous_close = df["close"].shift(1)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs()
    ], axis=1).max(axis=1)

    # Wilder-style exponentially smoothed values
    alpha = 1 / period

    atr = tr.ewm(
        alpha=alpha, adjust=False,
        min_periods=period
    ).mean()

    plus_smoothed = plus_dm.ewm(
        alpha=alpha, adjust=False,
        min_periods=period
    ).mean()

    minus_smoothed = minus_dm.ewm(
        alpha=alpha, adjust=False,
        min_periods=period
    ).mean()

    df["plus_di"] = 100 * plus_smoothed / atr
    df["minus_di"] = 100 * minus_smoothed / atr

    di_sum = df["plus_di"] + df["minus_di"]
    dx = 100 * (
        (df["plus_di"] - df["minus_di"]).abs()
        / di_sum.replace(0, np.nan)
    )

    df["adx"] = dx.ewm(
        alpha=alpha, adjust=False,
        min_periods=period
    ).mean()

    # Tick volume confirmation
    df["volume_avg"] = df["tick_volume"].rolling(
        20
    ).mean()

    return df


def generate_signal(df):
    latest = df.iloc[-1]
    previous = df.iloc[-2]

    required = [
        "bb_upper", "bb_lower", "ema50",
        "adx", "plus_di", "minus_di", "volume_avg"
    ]

    if latest[required].isna().any():
        return "HOLD", "Insufficient indicator data"

    # Trend-following Bollinger Band breakout
    bullish_breakout = (
        previous["close"] <= previous["bb_upper"]
        and latest["close"] > latest["bb_upper"]
    )

    bearish_breakout = (
        previous["close"] >= previous["bb_lower"]
        and latest["close"] < latest["bb_lower"]
    )

    strong_trend = latest["adx"] > 25
    volume_confirmed = (
        latest["tick_volume"] > latest["volume_avg"]
    )

    if (
        bullish_breakout
        and latest["close"] > latest["ema50"]
        and latest["plus_di"] > latest["minus_di"]
        and strong_trend
        and volume_confirmed
    ):
        return "BUY", "Bullish breakout with trend confirmation"

    if (
        bearish_breakout
        and latest["close"] < latest["ema50"]
        and latest["minus_di"] > latest["plus_di"]
        and strong_trend
        and volume_confirmed
    ):
        return "SELL", "Bearish breakout with trend confirmation"

    return "HOLD", "Conditions not fully aligned"


def main():
    if not mt5.initialize():
        print("MT5 connection failed:", mt5.last_error())
        return

    try:
        if not mt5.symbol_select(SYMBOL, True):
            print("Symbol unavailable:", SYMBOL)
            return

        # Start at position 1 to exclude open candle
        rates = mt5.copy_rates_from_pos(
            SYMBOL, TIMEFRAME, 1, BARS
        )

        if rates is None or len(rates) < 100:
            print("Insufficient market data:", mt5.last_error())
            return

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(
            df["time"], unit="s", utc=True
        )

        df = calculate_indicators(df)
        latest = df.iloc[-1]

        action, reason = generate_signal(df)

        print("\n===== MT5 AI TRADER =====")
        print("Symbol:", SYMBOL)
        print("Timeframe: M15")
        print("Last closed candle:", latest["time"])
        print(f"Close: {latest['close']:.5f}")
        print(f"BB Upper: {latest['bb_upper']:.5f}")
        print(f"BB Middle: {latest['bb_middle']:.5f}")
        print(f"BB Lower: {latest['bb_lower']:.5f}")
        print(f"EMA 50: {latest['ema50']:.5f}")
        print(f"ADX: {latest['adx']:.2f}")
        print(f"+DI: {latest['plus_di']:.2f}")
        print(f"-DI: {latest['minus_di']:.2f}")
        print(f"Tick Volume: {latest['tick_volume']}")
        print(f"Avg Volume: {latest['volume_avg']:.1f}")

        print("\nSIGNAL:", action)
        print("REASON:", reason)
        print("========================")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
