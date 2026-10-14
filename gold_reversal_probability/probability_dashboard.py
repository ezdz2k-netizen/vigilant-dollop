"""Read-only Streamlit dashboard for GOLD reversal research outputs."""
from pathlib import Path
import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title='GOLD | Reversal Probability Research',layout='wide')
st.title('GOLD — Bollinger Band Reversal Probability Research')
st.caption('Historical descriptive rates and calibrated probability research · Not live signals · Never executes orders')

DEFAULT=Path(__file__).resolve().parent/'results'
with st.sidebar:
    folder=Path(st.text_input('Results folder',str(DEFAULT)))
    mode=st.selectbox('Higher timeframe',['H1','H4','BOTH'])
    target=st.selectbox('Target',['middle','opposite'])
    horizon=st.select_slider('Horizon (M15 bars)',options=[4,8,16,32],value=16)
    min_sample=st.slider('Minimum observations per depth bin',1,100,20)

try:
    rep=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    rates=pd.read_csv(folder/'holdout_rates.csv')
    depth=pd.read_csv(folder/'depth_outcomes.csv')
    models=pd.read_csv(folder/'probability_metrics.csv')
    pred=pd.read_csv(folder/'holdout_predictions.csv') if (folder/'holdout_predictions.csv').stat().st_size else pd.DataFrame()
except Exception as exc:
    st.warning(f'Run probability_engine.py first; results not available: {exc}')
    st.stop()

current=lambda d:d[(d['mode']==mode)&(d['target_type']==target)&(d['horizon_m15']==horizon)].copy()
rr=current(rates);bb=current(depth); mm=current(models); pp=current(pred) if not pred.empty else pd.DataFrame()

st.warning('The rate is the chance of hitting a fixed target **before** a modeled stop within the selected horizon. It is NOT the chance that price will reverse, and it is not a trading recommendation.')
a,b,c,d=st.columns(4)
if len(rr):
    r=rr.iloc[0]
    a.metric('Historical holdout hit rate',f'{100*r.rate:.1f}%')
    b.metric('Labeled setup variants',int(r.n))
    c.metric('Target-first events',int(r.hits))
else:
    a.metric('Holdout hit rate','—');b.metric('Setups',0);c.metric('Hits',0)
if len(mm):
    m=mm.iloc[0];d.metric('Calibrated Brier score',f'{m.brier_calibrated:.3f}' if pd.notna(m.brier_calibrated) else 'Not fitted')
else:d.metric('Calibration','—')

st.subheader('M15 extension depth vs target-first frequency')
st.caption('Wilson 95% intervals describe sampling uncertainty per bin. Overlapping candidate events mean these intervals may be too narrow.')
bb=bb[bb['count']>=min_sample]
if len(bb):
    fig=go.Figure()
    fig.add_trace(go.Bar(x=bb.depth_bucket,y=100*bb.hit_rate,error_y=dict(type='data',symmetric=False,array=100*(bb.ci_high-bb.hit_rate),arrayminus=100*(bb.hit_rate-bb.ci_low)),text=bb['count'].astype(str),textposition='outside',name='Empirical hit rate'))
    fig.update_layout(yaxis_title='Target before stop (%)',xaxis_title='Outside M15 close distance / ATR',height=360)
    st.plotly_chart(fig,use_container_width=True)
else: st.info('Not enough samples in the selected depth bins.')

col1,col2=st.columns(2)
with col1:
    st.subheader('Favorable / adverse excursions')
    if len(bb):
        tmp=bb.melt(id_vars='depth_bucket',value_vars=['avg_mfe_atr','avg_mae_atr'],var_name='Measure',value_name='ATR')
        st.plotly_chart(px.bar(tmp,x='depth_bucket',y='ATR',color='Measure',barmode='group',title='Average MFE / MAE in ATR units'),use_container_width=True)
    else:st.info('No grouped excursions for selection.')
with col2:
    st.subheader('Event timing')
    if len(bb): st.plotly_chart(px.bar(bb,x='depth_bucket',y='avg_mins_to_event',title='Average minutes until stop, target or horizon'),use_container_width=True)
    else:st.info('No timing events in selection.')

st.subheader('Prediction calibration on untouched holdout')
if len(mm):
    st.dataframe(mm[['status','n_train','n_calibration','n_holdout','holdout_base_rate','brier_constant','brier_raw','brier_calibrated','auc_calibrated']],hide_index=True,use_container_width=True)
if len(pp):
    pp=pp.copy(); pp['bucket']=pd.cut(pp.prob_calibrated,bins=[0,.2,.4,.6,.8,1.00001],include_lowest=True)
    agg=pp.groupby('bucket',observed=True).agg(predicted=('prob_calibrated','mean'),observed=('success','mean'),n=('success','size')).reset_index()
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=[0,1],y=[0,1],mode='lines',name='Perfect calibration',line=dict(dash='dash')))
    fig.add_trace(go.Scatter(x=agg.predicted,y=agg.observed,mode='lines+markers+text',text=agg.n,name='Holdout buckets'))
    fig.update_layout(xaxis_title='Predicted frequency',yaxis_title='Observed frequency',xaxis_range=[0,1],yaxis_range=[0,1])
    st.plotly_chart(fig,use_container_width=True)
else:st.info('Insufficient independent events to fit/evaluate a calibrated model for these settings. Historical rates remain available.')

st.subheader('All higher-timeframe modes')
summary=rates[(rates.target_type==target)&(rates.horizon_m15==horizon)].copy()
st.dataframe(summary[['mode','n','hits','rate']],hide_index=True,use_container_width=True)
st.download_button('Download holdout rates',rates.to_csv(index=False),'gold_holdout_rates.csv','text/csv')
with st.expander('Research design and limitations'):
    st.json(rep,expanded=False)
