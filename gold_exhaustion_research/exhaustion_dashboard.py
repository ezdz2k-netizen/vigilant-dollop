"""Read-only Streamlit dashboard for GOLD exhaustion reversal walk-forward reports."""
from pathlib import Path
import json
import pandas as pd
import streamlit as st
import plotly.express as px

st.set_page_config(page_title='GOLD BB Exhaustion | Research',layout='wide')
st.title('GOLD — Developing H1/H4 Bollinger Exhaustion Research')
st.caption('Paper research only · H1 / H4 / BOTH · Completed M15 outside close · Causal developing bands · No orders')
root=Path(__file__).resolve().parent
folder=Path(st.sidebar.text_input('Report directory',str(root/'gold_exhaustion_results')))
if not folder.is_absolute(): folder=(root/folder).resolve()
st.sidebar.caption('Reports are created by gold_exhaustion.py, not by this dashboard.')
report_file=folder/'report.json'
if not report_file.exists():
    st.info('No backtest report yet. Run gold_exhaustion.py first, then refresh this page.')
    st.stop()
rep=json.loads(report_file.read_text(encoding='utf-8'))
st.warning('Historical simulation, not a live forecast. Broker clock / DST unverified; M5 OHLC fills approximate true execution.')
cols=st.columns(3)
cols[0].metric('Instrument',rep.get('symbol','—'))
cols[1].metric('M5 bars',f"{rep.get('data',{}).get('bars',0):,}")
cols[2].metric('Final holdout begins',rep.get('holdout_start','—')[:10])
st.subheader('Final holdout — development-selected variant per mode')
final=pd.read_csv(folder/'final_holdout.csv')
base=pd.read_csv(folder/'holdout_baselines.csv')
st.dataframe(final,hide_index=True,use_container_width=True)
joined=final[['mode','return_pct']].rename(columns={'return_pct':'selected'}).merge(base[['mode','return_pct']].rename(columns={'return_pct':'baseline'}),on='mode')
long=joined.melt(id_vars='mode',var_name='Variant',value_name='Return (%)')
st.plotly_chart(px.bar(long,x='mode',y='Return (%)',color='Variant',barmode='group',title='Identical holdout: selected vs fixed re-entry baseline'),use_container_width=True)
st.subheader('Walk-forward folds')
fold=pd.read_csv(folder/'walkforward_folds.csv')
st.dataframe(fold,hide_index=True,use_container_width=True)
if not fold.empty:
    st.plotly_chart(px.bar(fold,x='fold',y='return_pct',color='mode',barmode='group',title='Out-of-sample return by fold'),use_container_width=True)
st.subheader('Holdout trade ledger')
tradefile=folder/'holdout_trades.csv'
if tradefile.exists() and tradefile.stat().st_size:
    trades=pd.read_csv(tradefile)
    if not trades.empty:
        st.dataframe(trades.tail(300),hide_index=True,use_container_width=True)
        st.download_button('Download holdout trades',trades.to_csv(index=False),file_name='gold_exhaustion_holdout_trades.csv')
else:st.info('No holdout trades in the selected variants.')
with st.expander('Assumptions, costs and limitations'):
    st.json(rep.get('config',{}))
    for w in rep.get('warnings',[]):st.write('• '+w)
