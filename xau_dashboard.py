"""Read-only Streamlit view for gold strategy parameter research outputs."""
import json
from pathlib import Path
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(page_title="XAUUSD Strategy Research", layout="wide")
st.title("XAUUSD — Gold Strategy Optimization")
st.caption("Offline, research only · No orders · No API requests · All results are historical simulations")
root = Path(__file__).resolve().parent
path = st.sidebar.text_input("Results directory", str(root / "xau_optimization_results"))
folder = Path(path).expanduser()
report_path = folder / "optimization_report.json"
if not report_path.exists():
    st.info("No gold backtest results yet. Run xau_optimizer.py from PowerShell first, then refresh this page.")
    st.code(r'.\.venv\Scripts\python.exe xau_optimizer.py --symbol XAUUSD --months 12 --train-months 8 --risk 0.02 --strategy mtf')
    st.stop()
report = json.loads(report_path.read_text(encoding="utf-8"))
p = report["selected_training_parameters"]
props = report["symbol_properties"]
st.sidebar.subheader("Broker symbol specifications")
for k in ["symbol", "point", "tick_size", "tick_value", "contract_size", "volume_min", "volume_step", "account_currency"]:
    st.sidebar.write(f"**{k}**: {props.get(k)}")

st.subheader("Training selection versus untouched holdout")
c1,c2,c3,c4 = st.columns(4)
tr,te = report["training"],report["holdout"]
c1.metric("Training return", f'{tr["return_pct"]:.2f}%')
c2.metric("Holdout return", f'{te["return_pct"]:.2f}%')
c3.metric("Holdout profit factor", str(te["profit_factor"]) if te["profit_factor"] is not None else "N/A")
c4.metric("Holdout trades", te["trades"])
st.warning("Parameters were chosen on the training data only. Negative holdout performance means they did not demonstrate a profitable edge in this test. Small samples and one holdout period limit conclusions.")
st.subheader("Selected parameters (training only)")
st.dataframe(pd.DataFrame([p]),hide_index=True,use_container_width=True)
st.caption(f'Training raw-clock labels: {report["training_start_raw"]} → {report["training_end_raw"]}; holdout ends {report["holdout_end_raw"]}. These have not been independently validated as UTC across broker DST changes.')
st.subheader("Strategy comparison")
rows=[]
for phase,rec in [("Training",tr),("Holdout",te)]:
    rows.append({"Period":phase,**rec})
st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)

grid_file=folder/"training_grid.csv"
if grid_file.exists():
    st.subheader("Training search — all tested configurations")
    grid=pd.read_csv(grid_file)
    metric=st.selectbox("Color grid by",["return_pct","profit_factor","max_drawdown_pct","trades"])
    plot=grid.copy()
    plot[metric]=pd.to_numeric(plot[metric],errors="coerce")
    fig=px.scatter(plot,x="atr_mult",y="adx_min",color=metric,size="trades",hover_data=["rr","volume_mult","breakout_atr","score"],
                   title="In-sample grid exploration (not forward performance)")
    st.plotly_chart(fig,use_container_width=True)
    st.dataframe(grid.head(40),hide_index=True,use_container_width=True)

for phase,filename,begin in [("Training","training_trades.csv",10000), ("Holdout","holdout_trades.csv",10000)]:
    f=folder/filename
    st.subheader(f"{phase} — simulated trade history")
    if not f.exists():
        st.write("Missing trade ledger")
        continue
    try:
        trades=pd.read_csv(f)
    except pd.errors.EmptyDataError:
        trades=pd.DataFrame()
    if trades.empty:
        st.info("No eligible trades in this period. No statistical edge can be inferred.")
        continue
    trades["exit_time"]=pd.to_datetime(trades["exit_time"],format="mixed",utc=True,errors="coerce")
    trades=trades.sort_values("exit_time")
    trades["equity"] = begin+trades.pnl_usd.cumsum()
    plot=go.Figure()
    plot.add_trace(go.Scatter(x=trades.exit_time,y=trades.equity,mode="lines+markers",name="Closed-trade equity"))
    plot.update_layout(template="plotly_dark",height=320,xaxis_title="Broker raw-clock label (not validated UTC)",yaxis_title="Simulated USD equity")
    st.plotly_chart(plot,use_container_width=True)
    a,b=st.columns(2)
    with a:
        st.plotly_chart(px.histogram(trades,x="pnl_usd",nbins=25,title="Trade P&L distribution"),use_container_width=True)
    with b:
        st.plotly_chart(px.histogram(trades,x="reason",title="Exit reasons"),use_container_width=True)
    st.dataframe(trades,use_container_width=True,hide_index=True)
    st.download_button(f"Download {phase.lower()} trades",trades.to_csv(index=False),file_name=filename,mime="text/csv")

with st.expander("Model limitations and safeguards",expanded=True):
    for caution in report.get("cautions",[]):st.write("•",caution)
    st.write("Risk settings are hypothetical and NOT applied to your real MT5 account.")
