"""Read-only GOLD Bollinger reversal rolling walk-forward results dashboard."""
import json
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title='GOLD BB Walk-Forward',layout='wide')
st.title('GOLD • Flexible Triple-BB Walk-Forward Research')
st.caption('Research only • No trading • All session hours are BROKER-CLOCK, not verified UTC.')
folder=Path(st.sidebar.text_input('Results folder','gold_walkforward_results'))
report_path=folder/'optimization_report.json'
if not report_path.exists():
    st.warning('No report found. Run gold_walkforward_optimizer.py first.');st.stop()
report=json.loads(report_path.read_text(encoding='utf-8'))
st.info('Holdout is an untouched validation sample only until you use its results to select new parameters.')
final=report['final_holdout'];selected=final['selected'];base=final['unfiltered'];rolling=report['rolling_oos']
cols=st.columns(4)
for col, label, value in zip(cols,['Holdout selected return','Holdout unfiltered return','Selected profit factor','Holdout selected trades'],[f"{selected['return_pct']:.2f}%",f"{base['return_pct']:.2f}%",str(selected['profit_factor']),str(selected['trades'])]):
    col.metric(label,value)
st.subheader('Final untouched holdout comparison')
comp=pd.DataFrame([{'Strategy':'Selected filters',**selected},{'Strategy':'Unfiltered flexible opposite BB',**base}])
st.dataframe(comp,use_container_width=True,hide_index=True)
st.subheader('Walk-forward out-of-sample summary')
st.json(rolling,expanded=False)
st.subheader('Selected parameters (development-only)')
st.json(report['final_selection'],expanded=True)
selection=folder/'fold_selections.csv'
if selection.exists():
    folds=pd.read_csv(selection)
    st.subheader('Rolling fold selection and validation')
    st.dataframe(folds,use_container_width=True,hide_index=True)
    if 'test_stats' in folds:
        rows=[]
        for _,f in folds.iterrows():
            try:
                stats=json.loads(str(f['test_stats']).replace("'",'"'))
                rows.append({'Fold':f['fold'],'Return %':stats.get('return_pct'), 'Trades':stats.get('trades')})
            except (ValueError,TypeError):pass
        if rows:st.plotly_chart(px.bar(pd.DataFrame(rows),x='Fold',y='Return %',title='Rolling out-of-sample returns'),use_container_width=True)
for file,heading in [('selected_holdout_trades.csv','Selected holdout trades'),('unfiltered_holdout_trades.csv','Unfiltered holdout trades'),('training_grid.csv','Training candidate scores')]:
    target=folder/file
    if target.exists():
        st.subheader(heading)
        data=pd.read_csv(target)
        st.dataframe(data,use_container_width=True,hide_index=True)
        st.download_button('Download '+file,target.read_bytes(),file_name=file,key=file)
with st.expander('Model limitations and safeguards'):
    for warning in report['warnings']:
        st.write('• '+warning)
