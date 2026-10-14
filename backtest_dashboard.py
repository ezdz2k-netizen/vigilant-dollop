"""Step 8 read-only performance dashboard. Reads local backtest CSV/JSON only."""
from pathlib import Path
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

BASE = Path(__file__).resolve().parent
OUT = BASE / "backtest_results"
st.set_page_config(page_title="MT5 Step 8 Backtest", layout="wide", page_icon="📊")
st.title("📊 MT5 Step 8 — Historical Backtesting")
st.caption("Offline research dashboard · simulated EURUSD executions · no MT5 orders or AI calls")

summary_path = OUT / "summary.json"
trade_path = OUT / "trades.csv"
if not summary_path.exists() or not trade_path.exists():
    st.warning("No backtest results yet. Run `python backtest_mt5.py --months 12 --risk 0.02 --rr 3` first.")
    st.stop()

try:
    info = json.loads(summary_path.read_text(encoding="utf-8"))
    trades = pd.read_csv(trade_path)
except Exception as exc:
    st.error(f"Could not open backtest outputs: {exc}")
    st.stop()

cfg = info["config"]
st.info(f"Period: {info['start_utc'][:10]} to {info['end_utc'][:10]} | "
        f"Risk: {cfg['risk']*100:g}% | Target: {cfg['reward_r']:g}R | "
        f"ATR stop: {cfg['atr_mult']}× | Detected broker offset: {info['offset_hours']:+d}h")

stats = info.get("stats", {})
labels = list(stats)
if not labels:
    st.warning("The summary contains no strategy results")
    st.stop()
chosen = st.selectbox("Strategy", labels, index=labels.index("mtf") if "mtf" in labels else 0)
x = stats[chosen]

cols = st.columns(4)
cols[0].metric("Trades", f"{x['trades']:,}")
cols[1].metric("Net return", f"{x['return_pct']:+.2f}%")
cols[2].metric("Win rate", "N/A" if x['win_rate_pct'] is None else f"{x['win_rate_pct']:.1f}%")
cols[3].metric("Max closed-trade drawdown", f"{x['max_drawdown_pct']:.2f}%")
cols = st.columns(4)
cols[0].metric("Ending equity (USD)", f"${x['ending_equity']:,.2f}")
cols[1].metric("Profit factor", "N/A" if x['profit_factor'] is None else f"{x['profit_factor']:.2f}")
cols[2].metric("Expectancy / trade", "N/A" if x['expectancy_usd'] is None else f"${x['expectancy_usd']:,.2f}")
cols[3].metric("Average realized R", f"{x.get('avg_r_multiple', 0):+.2f}" if x['trades'] else "N/A")

st.subheader("Strategy comparison")
compare = []
for name, s in stats.items():
    compare.append({"Strategy": name, "Trades": s["trades"], "Return %": s["return_pct"],
                    "Win rate %": s["win_rate_pct"], "Profit factor": s["profit_factor"],
                    "Max closed-trade DD %": s["max_drawdown_pct"], "Expectancy USD": s["expectancy_usd"]})
st.dataframe(pd.DataFrame(compare), hide_index=True, use_container_width=True)

funnel = info.get("signal_funnel", {}).get(chosen, {})
st.caption(f"Signal funnel: {funnel.get('candidate_signals', 0)} M15 breakouts → "
           f"{funnel.get('confirmed_signals', 0)} {chosen} confirmations (before size/entry checks).")

chosen_trades = trades[trades["strategy"] == chosen].copy() if not trades.empty else trades.copy()
if chosen_trades.empty:
    st.warning("No completed trades. This is a valid possible outcome of a restrictive strategy, not a software failure.")
else:
    for col in ["entry_time_utc", "exit_time_utc", "signal_time_utc"]:
        chosen_trades[col] = pd.to_datetime(chosen_trades[col], utc=True, errors="coerce", format="mixed")
    chosen_trades = chosen_trades.sort_values("exit_time_utc")
    equity = pd.concat([pd.DataFrame({"exit_time_utc": [pd.to_datetime(info["start_utc"], utc=True)], "equity_after": [cfg["initial_equity"]]}),
                        chosen_trades[["exit_time_utc", "equity_after"]]], ignore_index=True)
    equity["peak"] = equity["equity_after"].cummax()
    equity["drawdown_pct"] = (equity["equity_after"] / equity["peak"] - 1) * 100
    st.subheader("Equity curve (closed trades)")
    fig = go.Figure(go.Scatter(x=equity["exit_time_utc"], y=equity["equity_after"], mode="lines+markers", name="Equity"))
    fig.update_layout(height=330, xaxis_title="UTC", yaxis_title="USD", margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)
    st.subheader("Drawdown (closed trades)")
    fig = go.Figure(go.Scatter(x=equity["exit_time_utc"], y=equity["drawdown_pct"], fill="tozeroy", mode="lines", name="Drawdown"))
    fig.update_layout(height=260, xaxis_title="UTC", yaxis_title="%", margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)
    a, b = st.columns(2)
    with a:
        st.subheader("P&L per trade")
        fig = go.Figure(go.Histogram(x=chosen_trades["pnl_usd"], nbinsx=35))
        fig.update_layout(height=300, xaxis_title="USD", yaxis_title="Trades")
        st.plotly_chart(fig, use_container_width=True)
    with b:
        st.subheader("Exit reasons")
        reasons = chosen_trades["exit_reason"].value_counts().reset_index()
        reasons.columns = ["reason", "count"]
        fig = go.Figure(go.Bar(x=reasons.reason, y=reasons["count"]))
        fig.update_layout(height=300, xaxis_title="Reason", yaxis_title="Trades")
        st.plotly_chart(fig, use_container_width=True)
    st.subheader("Trade ledger")
    st.dataframe(chosen_trades.sort_values("entry_time_utc", ascending=False), hide_index=True, use_container_width=True)
    st.download_button("Download selected trades CSV", data=chosen_trades.to_csv(index=False), file_name=f"{chosen}_trades.csv", mime="text/csv")

with st.expander("Assumptions, limitations, safety notes", expanded=True):
    for warning in info.get("warnings", []):
        st.write("• " + warning)
    st.write("• 2% is intended simulated risk at entry; actual modelled loss may exceed this due to slippage, gaps or coarse bars.")
    st.write("• Equity curve and drawdown reflect closed trades only, not intratrade drawdown.")
    st.write("• Results are historical simulations and are not a prediction of live performance.")
st.caption("READ ONLY — This page reads local CSV/JSON; it never calls MT5 order_send().")
