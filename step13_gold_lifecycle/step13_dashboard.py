"""Step 13 research dashboard. Reads existing CSV/JSON output, never connects to MT5."""
import json
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title='GOLD | Reversal Lifecycle',layout='wide')
st.title('GOLD — Reversal Lifecycle & Entry Timing')
st.caption('Historical research only · FxPro broker-clock labels · No orders or API calls')
ROOT=Path(__file__).resolve().parent
folder=Path(st.sidebar.text_input('Results folder',str(ROOT/'results')))
refresh=st.sidebar.button('Reload results')

def read_csv(name):
    f=folder/name
    return pd.read_csv(f) if f.exists() and f.stat().st_size else pd.DataFrame()

report_file=folder/'report.json'
if not report_file.is_file():
    st.info('Run step13_engine.py first, then reload this dashboard.')
    st.stop()
report=json.loads(report_file.read_text(encoding='utf-8'))
st.sidebar.subheader('Source')
st.sidebar.write('Instrument:',report.get('symbol'))
st.sidebar.write('M5 bars:',report.get('data',{}).get('m5_bars'))
st.sidebar.write('Rolling folds:',report.get('folds'))

holdout=read_csv('final_holdout.csv')
folds=read_csv('walkforward_folds.csv')
methods=read_csv('holdout_method_comparison.csv')
episodes=read_csv('episodes.csv')
lifecycle=read_csv('lifecycle_paths.csv')
timing=read_csv('reentry_timing.csv')
trades=read_csv('holdout_trades.csv')

st.subheader('Holdout — development-selected versus fixed baseline')
st.caption('Return and drawdown from separately simulated portfolios. Selection was made before reading this final period.')
if not holdout.empty:
    a,b,c=st.columns(3)
    a.metric('Tested variants',len(holdout))
    b.metric('Profitable variants',int((holdout.return_pct>0).sum()))
    c.metric('Holdout start',str(report.get('holdout_from',''))[:10])
    st.dataframe(holdout,use_container_width=True,hide_index=True)
    st.plotly_chart(px.bar(holdout,x='mode',y='return_pct',color='variant',barmode='group',title='Holdout return by HTF mode'),use_container_width=True)

st.subheader('All four entry methods — descriptive holdout comparison')
st.warning('Do not choose a new strategy by ranking this already-viewed holdout. These comparisons are exploratory.')
if not methods.empty:
    target=st.selectbox('Target type',sorted(methods.target.unique()))
    df=methods[methods.target==target]
    st.plotly_chart(px.bar(df,x='method',y='return_pct',color='mode',barmode='group',title='Entry method returns'),use_container_width=True)
    st.dataframe(df.sort_values(['mode','method']),use_container_width=True,hide_index=True)

st.subheader('Walk-forward folds')
if not folds.empty:
    st.dataframe(folds,use_container_width=True,hide_index=True)
    st.plotly_chart(px.bar(folds,x='fold',y='return_pct',color='mode',barmode='group',title='Rolling out-of-sample results'),use_container_width=True)

st.subheader('Exhaustion lifecycle')
if not episodes.empty:
    counts=episodes.groupby(['mode','status'],dropna=False).size().reset_index(name='episodes')
    st.plotly_chart(px.bar(counts,x='mode',y='episodes',color='status',barmode='group',title='Armed episodes and outcomes'),use_container_width=True)
    st.dataframe(counts,hide_index=True,use_container_width=True)
required={'episode','mode','side','horizon_m15','mfe_atr','mae_atr','depth'}
if not lifecycle.empty and required.issubset(lifecycle.columns):
    horizons=sorted(pd.to_numeric(lifecycle['horizon_m15'],errors='coerce').dropna().unique())
    if not horizons:
        st.info('Lifecycle data has no valid M15 horizons. Rerun the patched engine.')
        st.stop()
    h=st.selectbox('MFE/MAE horizon (M15 candles)',horizons,index=min(2,len(horizons)-1))
    subset=lifecycle[lifecycle.horizon_m15==h]
    st.plotly_chart(px.box(subset,x='mode',y='mfe_atr',color='side',title='Maximum favorable excursion / ATR'),use_container_width=True)
    st.plotly_chart(px.box(subset,x='mode',y='mae_atr',color='side',title='Maximum adverse excursion / ATR'),use_container_width=True)
    st.caption('MFE/MAE measured from arm price, not a virtual fill. Window may extend beyond episode invalidation.')
    depth_edges=[0,.1,.3,.6,1,2,float('inf')]
    subset=subset.copy()
    subset['depth_bucket']=pd.cut(subset.depth,depth_edges,include_lowest=True)
    grouped=subset.groupby(['mode','depth_bucket'],observed=True).agg(n=('episode','count'),mean_mfe=('mfe_atr','mean'),mean_mae=('mae_atr','mean')).reset_index()
    grouped['depth_bucket']=grouped.depth_bucket.astype(str)
    st.dataframe(grouped,use_container_width=True,hide_index=True)

if lifecycle.empty:
    st.info('No lifecycle observations saved. Check episodes and rerun the patched Step 13 engine.')
elif not required.issubset(lifecycle.columns):
    st.error(f'Invalid lifecycle CSV schema; missing: {sorted(required-set(lifecycle.columns))}. Rerun the patched engine.')

if not timing.empty:
    st.subheader('Time until band re-entry & additional extension')
    timing['reentered']=timing['bars_to_reentry'].notna()
    timing_summary=timing.groupby('mode',as_index=False).agg(
        episodes=('key','count'),reentry_rate=('reentered','mean'),
        median_bars_to_reentry=('bars_to_reentry','median'),
        median_extra_extension_atr=('extra_extension_atr','median'))
    timing_summary['reentry_rate']=(100*timing_summary['reentry_rate']).round(1)
    st.dataframe(timing_summary,hide_index=True,use_container_width=True)
    st.plotly_chart(px.histogram(timing.dropna(subset=['bars_to_reentry']),x='bars_to_reentry',color='mode',
        barmode='overlay',title='M15 candles from arm to first re-entry'),use_container_width=True)

st.subheader('Virtual trade ledger')
if not trades.empty:
    modes=st.multiselect('Show modes',sorted(trades['mode'].dropna().unique()),default=sorted(trades['mode'].dropna().unique()))
    shown=trades[trades['mode'].isin(modes)]
    st.dataframe(shown,use_container_width=True,hide_index=True)
    st.download_button('Download trade CSV',shown.to_csv(index=False),'step13_trades.csv','text/csv')
with st.expander('Known limitations and warnings'):
    for w in report.get('warnings',[]):st.write('•',w)
