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
from multi_timeframe import analyze as analyze_mtf

BASE = Path(__file__).resolve().parent
JOURNALS = {
    "Simulation": BASE / "ai_simulation_journal.csv",
    "Real AI": BASE / "ai_signal_journal.csv",
}
STATE_FILE = BASE / "monitor_m15_state_v3.json"
FRAMES = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}
FRAME_SECONDS = {"M5": 300, "M15": 900, "H1": 3600}
REFRESH_SECONDS = 10

st.set_page_config(page_title="MT5 Trading Hub", page_icon="📈", layout="wide")
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
        symbol_info = mt5.symbol_info(symbol)
        tick_info = {"bid": float(tick.bid), "ask": float(tick.ask), "age": tick_age,
                     "digits": int(symbol_info.digits) if symbol_info else 2,
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



# ================================================================
# Multi-asset read-only navigation
# ================================================================
SYMBOLS = {"BITCOIN": "Bitcoin (FxPro)", "GOLD": "Gold / XAUUSD (FxPro)", "#US30": "US30 Index (FxPro)"}

st.title("📊 MT5 Trading Hub")
st.caption("Live market charts ↔ Backtest research | Read-only | No orders | Switching symbols does not change your running monitor")

with st.sidebar:
    st.header("Workspace")
    qs = st.query_params
    default_symbol = str(qs.get("symbol", "GOLD"))
    if default_symbol not in SYMBOLS:
        default_symbol = "GOLD"
    symbol = st.selectbox("Instrument (FxPro symbol)", list(SYMBOLS),
                          index=list(SYMBOLS).index(default_symbol), format_func=lambda k: SYMBOLS[k])
    default_view = str(qs.get("view", "live"))
    if default_view not in ("live", "backtest"):
        default_view = "live"
    view = st.radio("Dashboard", ["Live Market", "Backtesting"],
                    index=0 if default_view == "live" else 1)
    st.caption("Quick navigation links")
    st.markdown(f"[📈 Live market](?view=live&symbol={symbol.replace('#', '%23')})  ·  "
                f"[📊 Backtest](?view=backtest&symbol={symbol.replace('#', '%23')})")
    st.divider()
    st.warning("Account is read-only here; orders are never sent. This selector does NOT change the symbol monitored by monitor_m15.py.")

selected_view = "live" if view == "Live Market" else "backtest"
if st.query_params.get("view") != selected_view or st.query_params.get("symbol") != symbol:
    st.query_params["view"] = selected_view
    st.query_params["symbol"] = symbol

st.subheader(f"{SYMBOLS[symbol]} · {view}")

if view == "Live Market":
    with st.sidebar:
        timeframe_label = st.selectbox("Chart timeframe", list(FRAMES), index=1)
        candle_count = st.slider("Visible candles", 30, 250, 100, 10)
        mode = st.radio("AI signal journal", list(JOURNALS), index=0)
        history_count = st.slider("Journal records", 10, 200, 50, 10)
    st.caption(f"Auto-refresh every {REFRESH_SECONDS}s while this page is open · Completed candles only")

    @st.fragment(run_every=f"{REFRESH_SECONDS}s")
    def live_panel():
        try:
            df, tick = fetch_mt5_data(symbol, FRAMES[timeframe_label], timeframe_label, 500)
        except Exception as exc:
            st.warning(f"MT5 quotes temporarily unavailable for {symbol}: {exc}")
            df, tick = None, None
        if df is not None:
            last = df.iloc[-1]
            digits = tick["digits"]
            a, b, c, d = st.columns(4)
            a.metric("Last closed price", f"{last['close']:.{digits}f}")
            b.metric("Bid / Ask", f"{tick['bid']:.{digits}f} / {tick['ask']:.{digits}f}")
            c.metric("ADX (14)", f"{last['adx']:.1f}" if pd.notna(last['adx']) else "—")
            d.metric("+DI / −DI", f"{last['plus_di']:.1f} / {last['minus_di']:.1f}")
            st.caption(f"Last completed candle: {utc_label(last['time'])} · Quote age: {tick['age']:.0f}s · "
                       f"Detected clock adjustment: {tick['offset_hours']:+d}h · "
                       f"MT5 account: {'DEMO' if tick['demo'] else 'LIVE (read only)'}")
            st.plotly_chart(chart_price(df, candle_count, f"{symbol} {timeframe_label} · BB(20,2) and EMA50"),
                            use_container_width=True, config={"displaylogo": False})
            l, r = st.columns(2)
            with l:
                st.plotly_chart(chart_adx(df, candle_count), use_container_width=True)
            with r:
                st.plotly_chart(chart_volume(df, candle_count), use_container_width=True)
            st.caption("Tick volume is the number of quoted ticks, not centralized traded volume. "
                       "ATR and ADX parameters have not been optimized for these instruments.")

        st.divider()
        st.subheader("H1 trend · M15 setup · M5 timing")
        try:
            result = analyze_mtf(symbol=symbol)
            a, b, c, d = st.columns(4)
            a.metric("H1 trend", result["h1_trend"])
            b.metric("M15 setup", result["m15_rule"])
            c.metric("M5 confirmation", "YES" if result["m5_confirmed"] else "NO")
            d.metric("MTF research signal", result["mtf_signal"])
            st.dataframe(pd.DataFrame([{"Timeframe": tf, **result["timeframes"][tf]}
                                       for tf in ("H1", "M15", "M5")]),
                         hide_index=True, use_container_width=True)
            st.caption("These are generic, unvalidated research rules across BTC, gold and indices; not calibrated trade signals.")
        except Exception as exc:
            st.info(f"MTF analysis unavailable: {exc}")

        st.divider()
        st.subheader("M15 AI journal")
        journal, warning = read_journal(JOURNALS[mode])
        if warning:
            st.info(warning)
        if not journal.empty:
            journal = journal[(journal["symbol"].astype(str).str.upper() == symbol)
                              & (journal["timeframe"].astype(str).str.upper() == "M15")].copy()
        checkpoint = read_state().get(f"{symbol}:M15:{'SIMULATION' if mode=='Simulation' else 'REAL_AI'}", {}).get("candle_time")
        st.caption(f"Monitor checkpoint: {utc_label(checkpoint)} · Journal mode: {mode} · Forced tests excluded")
        if journal.empty:
            st.info(f"No M15 journal records for {symbol}. Changing the dashboard symbol does NOT change the monitor. "
                    "Your monitor must be separately configured and restarted to record this instrument.")
        else:
            latest = journal.iloc[-1]
            st.metric("Latest journaled signal", str(latest["final_action"]).upper())
            subset = journal.sort_values("candle_time", ascending=False).head(history_count)
            st.dataframe(subset, hide_index=True, use_container_width=True)
            st.download_button("Download journal rows", journal.to_csv(index=False),
                               file_name=f"{symbol.replace('#','')}_journal.csv", mime="text/csv")
        st.caption("Read-only market research. No order_send() and no OpenAI API requests.")

    live_panel()

else:
    st.caption("Only saved historical backtests are displayed. This page does not execute tests, connect to trading execution, or change risk settings.")
    def load_generic_backtest(sym):
        # Never display EURUSD results under another symbol.
        paths = [BASE / f"backtest_results_{sym.replace('#','').lower()}", BASE / "backtest_results"]
        for folder in paths:
            info_file, trade_file = folder / "summary.json", folder / "trades.csv"
            if not info_file.exists() or not trade_file.exists():
                continue
            try:
                report = json.loads(info_file.read_text(encoding="utf-8"))
                if str(report.get("config", {}).get("symbol", "")).upper() != sym:
                    continue
                trade_df = pd.read_csv(trade_file)
                return report, trade_df, folder
            except (OSError, ValueError, KeyError, pd.errors.ParserError) as exc:
                st.error(f"Cannot read {folder}: {exc}")
                return None
        return None

    # Gold uses its separately developed broker-aware optimizer.
    if symbol == "GOLD":
        gold_folder = BASE / "xauusd_optimization" / "xau_optimization_results"
        if not gold_folder.exists():
            gold_folder = BASE / "xau_optimization_results"
        gold_report = gold_folder / "optimization_report.json"
        if gold_report.exists():
            try:
                info = json.loads(gold_report.read_text(encoding="utf-8"))
                reported_symbol = str(info.get("symbol_properties", {}).get("symbol", "")).upper()
                if reported_symbol != "GOLD":
                    st.warning(f"Found gold optimizer results for {reported_symbol or 'unknown'}, not GOLD. Hidden to avoid mixing symbols.")
                else:
                    st.success("FxPro GOLD walk-forward optimizer results found")
                    train, holdout = info["training"], info["holdout"]
                    a,b,c,d = st.columns(4)
                    a.metric("Training return", f"{train['return_pct']:.2f}%")
                    b.metric("Holdout return", f"{holdout['return_pct']:.2f}%")
                    c.metric("Holdout trades", holdout["trades"])
                    d.metric("Holdout profit factor", str(holdout.get("profit_factor")))
                    st.subheader("Selected training parameters")
                    st.dataframe(pd.DataFrame([info["selected_training_parameters"]]), hide_index=True)
                    gridfile = gold_folder / "training_grid.csv"
                    if gridfile.exists():
                        grid = pd.read_csv(gridfile)
                        st.subheader("Training search")
                        st.dataframe(grid, use_container_width=True, hide_index=True)
                    for stage, fname in [("Training", "training_trades.csv"), ("Holdout", "holdout_trades.csv")]:
                        f = gold_folder / fname
                        if f.exists():
                            td = pd.read_csv(f)
                            st.subheader(f"{stage} trades")
                            st.dataframe(td, use_container_width=True, hide_index=True)
                    st.warning("Gold historical clock labels and execution assumptions require validation. Holdout is not a live forecast.")
            except (OSError, ValueError, KeyError, pd.errors.ParserError) as exc:
                st.error(f"Gold results could not be opened: {exc}")
        else:
            st.info("No GOLD optimizer results found yet. Run the optimizer first; changing the symbol only selects results, it does not start a backtest.")
            st.code(r'.\.venv\Scripts\python.exe xau_optimizer.py --symbol GOLD --months 12 --train-months 8 --risk 0.02 --strategy mtf', language="powershell")

    generic = load_generic_backtest(symbol)
    if generic:
        info, trades, folder = generic
        st.success(f"Historical backtest loaded from {folder}")
        st.caption(f"Backtest period: {info.get('start_utc','?')} to {info.get('end_utc','?')}")
        rows=[]
        for strategy, stat in info.get("stats", {}).items():
            rows.append({"Strategy":strategy, **stat})
        if rows:
            st.subheader("Strategy comparison")
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            chosen=st.selectbox("Strategy trade ledger", [r["Strategy"] for r in rows])
            subset=trades.loc[trades["strategy"]==chosen] if "strategy" in trades else trades
            if not subset.empty and "equity_after" in subset and "exit_time_utc" in subset:
                subset=subset.copy()
                subset["exit_time_utc"]=pd.to_datetime(subset["exit_time_utc"],format="mixed",utc=True,errors="coerce")
                subset=subset.sort_values("exit_time_utc")
                st.line_chart(subset.set_index("exit_time_utc")["equity_after"])
            st.dataframe(subset, hide_index=True, use_container_width=True)
            st.download_button("Download selected trades", subset.to_csv(index=False),
                               file_name=f"{symbol.replace('#','')}_trades.csv", mime="text/csv")
        for caution in info.get("warnings", []):
            st.caption("• " + str(caution))
    elif symbol != "GOLD":
        st.info(f"No verified {symbol} backtest files found. Existing EURUSD results will not be shown under this symbol.")
        st.warning("The EURUSD backtester uses FX pip assumptions and must NOT be reused unchanged for "
                   "BITCOIN or #US30. A broker-contract-aware backtesting engine is needed first.")
    st.caption("Historical simulations only. No trades are placed and no API calls are made.")
