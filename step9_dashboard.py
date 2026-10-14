"""Streamlit read-only dashboard for Step 9 diagnostics. Run engine first."""
import json
from pathlib import Path
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(page_title='MT5 Step 9 Diagnostics', layout='wide')
st.title('MT5 AI Trader — Step 9 Strategy Diagnostics')
st.caption('Historical, read-only analysis of your backtest ledger. No market access, AI calls or orders.')
root=Path(__file__).resolve().parent
folder=Path(st.sidebar.text_input('Diagnostic results folder',str(root/'step9_results')))
report_file=folder/'diagnostic_report.json'; data_file=folder/'enriched_trades.csv'
if not report_file.is_file() or not data_file.is_file():
    st.info('Run strategy_diagnostics.py first. Expected diagnostic_report.json and enriched_trades.csv in the results folder.')
    st.code(r'.\.venv\Scripts\python.exe strategy_diagnostics.py --trades backtest_results\trades.csv --summary backtest_results\summary.json --output step9_results')
    st.stop()
try:
    report=json.loads(report_file.read_text(encoding='utf-8'))
    d=pd.read_csv(data_file)
    for c in ['entry_time_utc','exit_time_utc','signal_time_utc']:
        d[c]=pd.to_datetime(d[c],utc=True,format='mixed')
except Exception as exc:
    st.error(f'Unable to load results: {exc}');st.stop()
strategies=st.sidebar.multiselect('Strategies', sorted(d.strategy.unique()),default=sorted(d.strategy.unique()))
d=d[d.strategy.isin(strategies)].copy()
if d.empty:st.warning('Choose at least one strategy');st.stop()
st.subheader('Backtest source summary')
stat_rows=[]
for strategy,s in report.get('source_backtest_summary',{}).items():
    if strategy in strategies:
        stat_rows.append({'Strategy':strategy,'Trades':s.get('trades'),'Return %':s.get('return_pct'),
                          'Win rate %':s.get('win_rate_pct'),'Profit factor':s.get('profit_factor'),
                          'Max drawdown %':s.get('max_drawdown_pct'),'Avg R':s.get('avg_r_multiple')})
st.dataframe(pd.DataFrame(stat_rows),hide_index=True,use_container_width=True)
st.caption('These are the original summary.json metrics, not new simulated trades.')
st.subheader('Execution & ledger reconciliation')
checks=pd.DataFrame(report['reconciliation']);checks=checks[checks.strategy.isin(strategies)]
st.dataframe(checks,hide_index=True,use_container_width=True)
issues=pd.DataFrame(report['issues'])
if not issues.empty:issues=issues[issues.strategy.isin(strategies)]
c1,c2,c3=st.columns(3)
c1.metric('Trade records',len(d));c2.metric('Execution flags',len(issues));c3.metric('Total modeled commissions',f"${d.commission_usd.sum():,.2f}")
st.caption('Spread figures are measured from modeled ledger fields; price-impact counterfactuals cannot be reconstructed from this CSV.')
if not issues.empty:
    st.dataframe(issues,hide_index=True,use_container_width=True)
    st.download_button('Download execution flags',issues.to_csv(index=False).encode(),'step9_execution_flags.csv','text/csv')
else:st.success('No flags raised by current static checks.')
st.subheader('Trade outcomes and equity')
left,right=st.columns(2)
with left:
    fig=px.histogram(d,x='r_multiple',color='strategy',nbins=45,title='R-multiple distribution',barmode='overlay')
    st.plotly_chart(fig,use_container_width=True)
with right:
    fig=px.box(d,x='strategy',y='holding_minutes',points=False,title='Holding duration (minutes)')
    st.plotly_chart(fig,use_container_width=True)
curves=[]
for strategy,g in d.groupby('strategy'):
    g=g.sort_values('exit_time_utc').copy()
    g['strategy_equity']=g['equity_after']
    g['peak']=g['strategy_equity'].cummax()
    curves.append(g)
curve=pd.concat(curves)
st.plotly_chart(px.line(curve,x='exit_time_utc',y='strategy_equity',color='strategy',title='Recorded equity after closed trades'),use_container_width=True)
st.caption('Recorded closed-trade equity, not intrabar mark-to-market drawdown.')
st.subheader('Session, timing and directional diagnostics')
dimensions={'UTC hour':'entry_hour_utc','UTC clock bucket (not true session)':'session_utc_proxy',
            'Day of week':'entry_weekday_utc','Month':'entry_month_utc','Direction':'direction',
            'ATR quartile (proxy)':'atr_bucket','Exit reason':'exit_reason'}
selected=st.selectbox('Breakdown',list(dimensions))
col=dimensions[selected]
agg=d.groupby(['strategy',col],observed=True).agg(trades=('pnl_usd','size'),net_pnl=('pnl_usd','sum'),mean_r=('r_multiple','mean'),wins=('pnl_usd',lambda x:(x>0).sum())).reset_index()
agg['win_rate_pct']=100*agg.wins/agg.trades
left,right=st.columns(2)
with left:st.plotly_chart(px.bar(agg,x=col,y='mean_r',color='strategy',barmode='group',title='Average R by category'),use_container_width=True)
with right:st.plotly_chart(px.bar(agg,x=col,y='trades',color='strategy',barmode='group',title='Sample sizes'),use_container_width=True)
st.dataframe(agg.round(3),hide_index=True,use_container_width=True)
st.warning('Small subgroups are noisy. These are descriptive in-sample splits, not evidence of a profitable filter.')
st.subheader('Costs and trade geometry')
left,right=st.columns(2)
with left:st.plotly_chart(px.histogram(d,x='entry_spread_pips',color='strategy',title='Recorded entry spread (pips)',barmode='overlay'),use_container_width=True)
with right:st.plotly_chart(px.scatter(d,x='atr_pips',y='r_multiple',color='strategy',hover_data=['direction','exit_reason'],title='ATR at entry vs realized R'),use_container_width=True)
st.dataframe(d.groupby('strategy').agg(trades=('pnl_usd','size'),modeled_commission_usd=('commission_usd','sum'),avg_entry_spread_pips=('entry_spread_pips','mean'),avg_exit_spread_pips=('exit_spread_pips','mean'),median_stop_pips=('risk_distance_pips','median')).round(3),use_container_width=True)
st.subheader('Signal funnel (summary data only)')
funnel=pd.DataFrame(report.get('signal_funnel',[]))
if not funnel.empty:st.dataframe(funnel[funnel.strategy.isin(strategies)],hide_index=True,use_container_width=True)
st.caption('Rejection causes and outcomes of rejected setups were not logged; this dashboard cannot infer them.')
st.subheader('Data limitations and caveats')
for item in report.get('limitations',[]):st.markdown(f'- {item}')
with st.expander('Warnings from original backtester'):
    for msg in report.get('source_backtest_warnings',[]):st.write('•',msg)
st.subheader('Trade ledger')
shown=d.sort_values('entry_time_utc',ascending=False)
st.dataframe(shown,use_container_width=True,hide_index=True,height=300)
st.download_button('Download filtered enriched trades',shown.to_csv(index=False).encode(),'step9_filtered_trades.csv','text/csv')
