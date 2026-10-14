"""MT5 AI Trader: read-only Streamlit market and signal dashboard.

Run: python -m streamlit run dashboard.py
Dependencies: streamlit plotly pandas numpy MetaTrader5 (and existing indicators.py)
Does NOT import dual_ai_engine or call order_send.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from indicators import calculate_indicators

BASE = Path(__file__).resolve().parent
JOURNALS = {
    "Simulation": BASE / "ai_simulation_journal.csv",
    "Real AI": BASE / "ai_signal_journal.csv",
}
STATE_FILE = BASE / "monitor_m15_state_v3.json"
FRAMES = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}
FRAME_SECONDS = {"M5": 300, "M15": 900, "H1": 3600}
REFRESH_SECONDS = 10

st.set_page_config(page_title="MT5 AI Trader | Live Charts", page_icon="📈", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2.5rem}
[data-testid="stMetric"] {border: 1px solid rgba(128,128,128,.24); border-radius: 12px; padding: 10px 14px}
</style>
""", unsafe_allow_html=True)


def utc_label(value):
    if value is None:
        return "—"
    try:
        parsed = pd.to_datetime(value, utc=True, format="mixed", errors="coerce")
        return parsed.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(parsed) else "—"
    except Exception:
        return "—"


def read_journal(path):
    if not path.exists():
        return pd.DataFrame(), f"No journal yet: {path.name}"
    try:
        df = pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        return pd.DataFrame(), f"Unable to read journal: {exc}"
    if df.empty or not {"candle_time", "final_action"}.issubset(df.columns):
        return pd.DataFrame(), "Journal is empty or missing required columns."
    df["candle_time"] = pd.to_datetime(df["candle_time"], utc=True, format="mixed", errors="coerce")
    df = df.dropna(subset=["candle_time"]).copy()
    if "forced_test" in df:
        df = df.loc[~df["forced_test"].astype(str).str.strip().str.lower().isin(("true", "1"))].copy()
    for col in ("symbol", "timeframe", "original_rule_signal", "fast_action", "deep_action", "reason"):
        if col not in df:
            df[col] = "—"
    for col in ("close", "confidence"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("candle_time").drop_duplicates(["symbol", "timeframe", "candle_time"], keep="last")
    return df, None


def read_state():
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def fetch_mt5_data(symbol, timeframe, timeframe_name, bar_count):
    """Read-only; fails closed if time correction cannot be verified."""
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"MT5 account info unavailable: {mt5.last_error()}")
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 symbol unavailable ({symbol}): {mt5.last_error()}")
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or not tick.time:
            raise RuntimeError(f"No latest tick for {symbol}")
        now = datetime.now(timezone.utc)
        raw_tick_time = datetime.fromtimestamp(int(tick.time), tz=timezone.utc)
        difference = (raw_tick_time - now).total_seconds()
        offset_hours = round(difference / 3600)
        residual = difference - 3600 * offset_hours
        if abs(offset_hours) > 14 or abs(residual) > 90:
            raise RuntimeError("Cannot validate broker timestamp offset from fresh ticks. Check terminal/server clock.")
        adjusted_tick_time = raw_tick_time - timedelta(hours=offset_hours)
        tick_age = (now - adjusted_tick_time).total_seconds()
        if not (-10 <= tick_age <= 90):
            raise RuntimeError(f"Quote is stale or timestamp uncertain ({tick_age:.0f}s). Waiting for fresh ticks.")

        # 1 means exclude current, incomplete candle.
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, bar_count)
        if rates is None or len(rates) < 100:
            raise RuntimeError(f"Insufficient MT5 candles: {mt5.last_error()}")
        df = pd.DataFrame(rates).sort_values("time").reset_index(drop=True)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=offset_hours)
        if df["time"].duplicated().any():
            raise RuntimeError("Duplicate candle timestamps; chart skipped")
        step = FRAME_SECONDS[timeframe_name]
        last_open = df["time"].iloc[-1].to_pydatetime()
        if last_open + timedelta(seconds=step + 3) > now:
            raise RuntimeError("Newest returned candle is not yet closed after timestamp correction")
        if last_open.minute % (step // 60 if step < 3600 else 60) != 0 or last_open.second != 0:
            raise RuntimeError("Candle timestamps do not align with selected timeframe")
        df = calculate_indicators(df)
        tick_info = {"bid": float(tick.bid), "ask": float(tick.ask), "age": tick_age,
                     "offset_hours": offset_hours, "demo": account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO}
        return df, tick_info
    finally:
        mt5.shutdown()


def chart_price(df, count, title):
    plot = df.tail(count).copy()
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=plot["time"], open=plot["open"], high=plot["high"],
                                 low=plot["low"], close=plot["close"], name="Candles",
                                 increasing_line_color="#1bc48d", decreasing_line_color="#f36a74"))
    for column, label, color, dash in [
        ("bb_upper", "BB upper", "#65adff", "dot"),
        ("bb_middle", "BB middle", "#a3aab6", "dot"),
        ("bb_lower", "BB lower", "#65adff", "dot"),
        ("ema50", "EMA 50", "#ffb45c", "solid"),
    ]:
        fig.add_trace(go.Scatter(x=plot["time"], y=plot[column], mode="lines", name=label,
                                 line=dict(color=color, width=1.5, dash=dash)))
    fig.update_layout(template="plotly_dark", height=540, margin=dict(l=10, r=10, t=40, b=10),
                      title=title, xaxis_rangeslider_visible=False, hovermode="x unified",
                      legend=dict(orientation="h", y=1.08, x=0),
                      yaxis=dict(title="Price", autorange=True),
                      xaxis=dict(title="Corrected UTC candle open"))
    return fig


def chart_adx(df, count):
    plot = df.tail(count)
    fig = go.Figure()
    for col, label, color in [("adx", "ADX", "#ffcb59"),
                               ("plus_di", "+DI", "#1bc48d"),
                               ("minus_di", "−DI", "#f36a74")]:
        fig.add_trace(go.Scatter(x=plot["time"], y=plot[col], mode="lines", name=label,
                                 line=dict(color=color, width=2)))
    fig.add_hline(y=25, line_dash="dash", line_color="#98a2ad", annotation_text="ADX 25")
    fig.update_layout(template="plotly_dark", height=290, margin=dict(l=10, r=10, t=15, b=10),
                      yaxis_title="ADX / DI", hovermode="x unified", legend=dict(orientation="h", y=1.15))
    return fig


def chart_volume(df, count):
    plot = df.tail(count)
    colors = np.where(plot["close"] >= plot["open"], "#1bc48d", "#f36a74")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=plot["time"], y=plot["tick_volume"], name="Tick volume", marker_color=colors))
    fig.add_trace(go.Scatter(x=plot["time"], y=plot["volume_avg"], name="20-bar average",
                             mode="lines", line=dict(color="#ffcb59", width=2)))
    fig.update_layout(template="plotly_dark", height=270, margin=dict(l=10, r=10, t=15, b=10),
                      yaxis_title="Ticks", hovermode="x unified", legend=dict(orientation="h", y=1.15))
    return fig


st.title("📈 MT5 AI Trader — Live Market Dashboard")
st.caption("Read-only MT5 market charts and local AI signal journals · Completed candles · No orders placed")

with st.sidebar:
    st.header("Chart settings")
    symbol = st.text_input("MT5 symbol", "EURUSD").strip().upper()
    timeframe_label = st.selectbox("Chart timeframe", list(FRAMES), index=1)
    candle_count = st.slider("Visible candles", 30, 250, 100, 10)
    st.divider()
    mode = st.radio("Signal journal", list(JOURNALS), index=0)
    history_count = st.slider("Journal records", 10, 200, 50, 10)
    st.caption(f"Refresh every {REFRESH_SECONDS}s while page is open.")
    st.caption("Keep `monitor_m15.py` running separately. Dashboard does not start the monitor.")


@st.fragment(run_every=f"{REFRESH_SECONDS}s")
def update_dashboard():
    if not symbol:
        st.warning("Enter an MT5 symbol")
        return

    try:
        # Fixed total lookback adds warm-up history for EMA / ADX.
        df, tick = fetch_mt5_data(symbol, FRAMES[timeframe_label], timeframe_label, 500)
    except Exception as exc:
        st.warning(f"Live market charts temporarily unavailable: {exc}")
        df, tick = None, None

    if df is not None:
        latest = df.iloc[-1]
        a, b, c, d, e = st.columns(5)
        a.metric("Last completed close", f"{latest['close']:.5f}")
        b.metric("Bid / Ask", f"{tick['bid']:.5f} / {tick['ask']:.5f}")
        c.metric("ADX (14)", f"{latest['adx']:.1f}" if pd.notna(latest["adx"]) else "—")
        d.metric("+DI / −DI", f"{latest['plus_di']:.1f} / {latest['minus_di']:.1f}")
        e.metric("Quote age", f"{tick['age']:.0f} sec")
        st.caption(f"Last completed candle open: {utc_label(latest['time'])} | "
                   f"Detected timestamp correction: {tick['offset_hours']:+d}h | "
                   f"MT5 account: {'DEMO' if tick['demo'] else 'LIVE (read-only)'}")

        st.plotly_chart(chart_price(df, candle_count, f"{symbol} · {timeframe_label} · Candles, BB(20,2), EMA(50)"),
                        use_container_width=True, config={"displaylogo": False})
        left, right = st.columns([1, 1])
        with left:
            st.subheader("ADX (14), +DI / −DI")
            st.plotly_chart(chart_adx(df, candle_count), use_container_width=True, config={"displaylogo": False})
        with right:
            st.subheader("Tick volume")
            st.plotly_chart(chart_volume(df, candle_count), use_container_width=True, config={"displaylogo": False})
        st.caption("Indicator calculations reuse your indicators.py. Tick volume is broker tick count, not centralized FX traded volume. "
                   "ADX initialization may differ from the MT5 built-in indicator.")

    st.divider()
    st.subheader("AI monitoring journal")
    if timeframe_label != "M15":
        st.caption("Your existing monitor produces M15 signals only. Journal below stays on M15 while the chart timeframe changes.")
    journal, warning = read_journal(JOURNALS[mode])
    if warning:
        st.info(warning)
    journal = journal[(journal["symbol"].astype(str).str.upper() == symbol) &
                      (journal["timeframe"].astype(str).str.upper() == "M15")].copy() if not journal.empty else journal
    state = read_state()
    key = f"{symbol}:M15:{'SIMULATION' if mode == 'Simulation' else 'REAL_AI'}"
    checkpoint = state.get(key, {}).get("candle_time")
    st.caption(f"Mode: {mode} | Last monitor checkpoint: {utc_label(checkpoint)} | Forced tests excluded")
    if journal.empty:
        st.info("No matching non-forced journal rows yet.")
    else:
        last = journal.iloc[-1]
        j1, j2, j3 = st.columns(3)
        j1.metric("Latest signal", str(last["final_action"]).upper())
        j2.metric("Journaled M15 candles", len(journal))
        j3.metric("Latest signal candle", utc_label(last["candle_time"]))
        st.write(f"**Rule:** {last.get('original_rule_signal','—')}  |  "
                 f"**Fast:** {last.get('fast_action','—')}  |  **Deep:** {last.get('deep_action','—')}")
        st.write(str(last.get("reason", "")))
        columns = [c for c in ["candle_time", "close", "original_rule_signal", "fast_action", "deep_action",
                               "final_action", "confidence", "approved", "reason"] if c in journal]
        recent = journal.sort_values("candle_time", ascending=False)[columns].head(history_count).copy()
        recent["candle_time"] = recent["candle_time"].dt.strftime("%Y-%m-%d %H:%M UTC")
        st.dataframe(recent, hide_index=True, use_container_width=True)
        st.download_button("Download filtered journal", journal.to_csv(index=False).encode("utf-8"),
                           file_name=f"{symbol}_M15_{mode.replace(' ','_').lower()}_journal.csv",
                           mime="text/csv", key=f"download_{mode}_{symbol}")
    st.caption("Simulation model outputs are mocked. Dashboard is for research only and does not validate profitability or execute orders.")


update_dashboard()
