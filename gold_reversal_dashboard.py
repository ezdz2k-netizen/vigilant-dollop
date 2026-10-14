"""Read-only Streamlit comparison of 3 GOLD BB reversal variants."""
from pathlib import Path
import json
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title='GOLD | Triple BB Reversal', layout='wide')
st.title('GOLD — Triple Bollinger Band Reversal')
st.caption('Strict vs Sequential vs Flexible · Middle / Opposite BB targets · Historical M5 OHLC simulation only · NO ORDERS')
folder=Path(__file__).resolve().parent/'gold_reversal_results'
with st.sidebar:
    st.header('Saved results')
    folder=Path(st.text_input('Results directory',str(folder)))
    st.caption('Run gold_bb_reversal.py first. This dashboard reads local CSV/JSON files only.')
if not (folder/'comparison.csv').exists():
    st.warning('No results found. Run the standalone backtest first.'); st.stop()
try:
    comparison=pd.read_csv(folder/'comparison.csv')
    counts=pd.read_csv(folder/'signal_counts.csv')
    trades=pd.read_csv(folder/'trades.csv') if (folder/'trades.csv').exists() else pd.DataFrame()
    report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
except Exception as exc:
    st.error(f'Unable to read backtest results: {exc}'); st.stop()
st.info('Timestamp labels are broker-clock labels, not verified historical UTC. Current instrument: '+report.get('symbol','GOLD'))
c1,c2,c3,c4=st.columns(4)
c1.metric('Historical M5 bars',f"{report.get('m5_bars',0):,}")
c2.metric('H1 bars',f"{report.get('h1_bars',0):,}")
c3.metric('H4 bars',f"{report.get('h4_bars',0):,}")
c4.metric('Configurations',len(comparison)//2)
period=st.radio('Dataset segment', ['holdout','training'], horizontal=True)
part=comparison[comparison.period==period].copy()
part['configuration']=part.variant.str.title()+' · '+part.target.str.title()+' BB'
st.subheader('Comparison')
st.dataframe(part[['configuration','trades','return_pct','profit_factor','win_rate_pct','max_drawdown_pct','avg_r']],use_container_width=True,hide_index=True)
if part.empty:
    st.stop()
left,right=st.columns(2)
with left:
    fig=px.bar(part,x='configuration',y='return_pct',title='Simulated return (%)',color='variant')
    fig.add_hline(y=0,line_dash='dot')
    st.plotly_chart(fig,use_container_width=True)
with right:
    fig=px.bar(part,x='configuration',y='max_drawdown_pct',title='Closed-trade maximum drawdown (%)',color='variant')
    st.plotly_chart(fig,use_container_width=True)
st.subheader('Signal funnel')
st.dataframe(counts,use_container_width=True,hide_index=True)
if not trades.empty:
    a,b=st.columns(2)
    with a:
        variant=st.selectbox('Rule variant',['strict','sequential','flexible'],index=0)
    with b:
        target=st.selectbox('Target',['middle','opposite'],index=0)
    selected=trades[(trades.variant==variant)&(trades.target==target)&(trades.period==period)].copy()
    st.metric('Selected recorded trades',len(selected))
    if not selected.empty:
        selected['entry_broker_clock']=pd.to_datetime(selected.entry_broker_clock,utc=True,format='mixed',errors='coerce')
        selected=selected.sort_values('entry_broker_clock')
        selected['equity_path']=10000+selected.net_pnl.cumsum()
        st.plotly_chart(px.line(selected,x='entry_broker_clock',y='equity_path',title='Segment P&L path (reference $10,000 base)'),use_container_width=True)
        st.dataframe(selected,use_container_width=True,hide_index=True)
        st.download_button('Download selected trades',selected.to_csv(index=False).encode(),file_name='gold_reversal_selected_trades.csv')
    else:
        st.info('No trades in this segment/configuration. This is not proof of a successful filter.')
with st.expander('Methodology, assumptions and limitations',expanded=True):
    for w in report.get('warnings',[]):
        st.markdown('- '+w)
st.caption('Do not optimize parameters using the holdout period. No OpenAI calls and no trade execution.')
