"""Read-only dashboard for GOLD entry confirmation walk-forward comparisons."""
import json
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title='GOLD Entry Confirmation Research',layout='wide')
st.title('GOLD · Triple-BB Reversal — Entry Confirmation')
st.caption('Read-only research • GOLD on FxPro • Broker-clock timestamps are not verified historical UTC • No orders')
root=Path(__file__).resolve().parent
folder=Path(st.sidebar.text_input('Result folder',str(root/'gold_entry_results')))
p=folder/'entry_report.json'
if not p.exists():
    st.warning(f'Report not found at {p}. Run gold_entry_comparison.py first.');st.stop()
report=json.loads(p.read_text(encoding='utf-8'))
hold=pd.DataFrame(report['final_holdout'])
selected=report.get('selected_development_style')
st.info(f'Development-selected method: **{selected or "none (insufficient training data)"}**. All four holdout results are shown for diagnostics; do not choose a new method based on this holdout.')
if selected and selected in hold['style'].values:
    r=hold.loc[hold['style']==selected].iloc[0]
    cells=st.columns(4)
    cells[0].metric('Selected holdout return',f"{r['return_pct']:.2f}%")
    cells[1].metric('Selected holdout profit factor',str(r['profit_factor']))
    cells[2].metric('Selected holdout trades',str(r['trades']))
    cells[3].metric('Selected max drawdown',f"{r['max_drawdown_pct']:.2f}%")
st.subheader('Final holdout — same dates for all entry styles')
st.dataframe(hold,use_container_width=True,hide_index=True)
if not hold.empty:
    st.plotly_chart(px.bar(hold,x='style',y='return_pct',color='style',title='Holdout return by entry type (%)'),use_container_width=True)
    st.plotly_chart(px.bar(hold,x='style',y='trades',color='style',title='Holdout trade count'),use_container_width=True)
fold_path=folder/'fold_comparison.csv'
if fold_path.exists():
    folds=pd.read_csv(fold_path)
    st.subheader('Rolling development: every style, each fold')
    st.dataframe(folds,use_container_width=True,hide_index=True)
    if len(folds):st.plotly_chart(px.bar(folds,x='fold',y='test_return_pct',color='style',barmode='group',title='Rolling OOS fold returns (independently simulated)'),use_container_width=True)
st.subheader('Candidate counts')
st.dataframe(pd.DataFrame([report['entry_candidate_counts']]),hide_index=True,use_container_width=True)
with st.expander('Backtesting assumptions and limitations',expanded=False):
    st.json(report['config'],expanded=False)
    for x in report['warnings']:st.write('• '+x)
for filename in ['holdout_trades.csv','rolling_oos_trades.csv','holdout_comparison.csv','fold_comparison.csv']:
    fp=folder/filename
    if fp.exists():
        with st.expander(filename):
            df=pd.read_csv(fp)
            st.dataframe(df,use_container_width=True,hide_index=True)
            st.download_button('Download '+filename,fp.read_bytes(),file_name=filename,key=filename)
