"""Step 11 read-only GOLD forward-paper dashboard: diagnostics and monitor health."""
from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import pandas as pd
import streamlit as st
import plotly.express as px
from paper_core import STYLES

ROOT=Path(__file__).resolve().parent
st.set_page_config(page_title='GOLD Step 11 | Paper Monitor',layout='wide')
st.title('GOLD Triple BB — Paper Trading & Signal Diagnostics')
st.caption('Read-only MT5 research · Four independent virtual accounts · Never sends orders')
with st.sidebar:
 st.header('Dashboard')
 db_path=st.text_input('Paper database',str(ROOT/'paper_trades.db'))
 refresh=st.selectbox('Refresh every (seconds)',[5,10,30,60],index=1)
 max_rows=st.slider('Diagnostics to display',20,500,120,step=20)
 st.caption('Keep paper_monitor.py running separately. Times in signal logic are broker-clock labels.')

def load():
 if not Path(db_path).is_file():return None
 db=sqlite3.connect(f'file:{Path(db_path).resolve().as_posix()}?mode=ro',uri=True,timeout=5)
 try:
  table_names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  required={'marks','signals','positions','events','ticks'}
  if not required.issubset(table_names):raise ValueError(f'Database lacks tables: {sorted(required-table_names)}')
  def read(name,limit=None):
   if name not in table_names:return pd.DataFrame()
   query=f'SELECT * FROM {name}'
   if name in ('ticks','events','candle_diagnostics'):query+=' ORDER BY id DESC'
   if limit:query+=f' LIMIT {int(limit)}'
   return pd.read_sql_query(query,db)
  return {key:read(key,1000 if key=='ticks' else (max_rows if key=='candle_diagnostics' else 1000)) for key in
          ['marks','signals','positions','events','ticks','candle_diagnostics','monitor_health']}
 finally:db.close()

def money(x):return f'{x:,.2f}'

def quote_age(stamp):
 try:return (datetime.now(timezone.utc)-datetime.fromisoformat(str(stamp))).total_seconds()
 except Exception:return None

@st.fragment(run_every=f'{refresh}s')
def view():
 try:data=load()
 except Exception as e:st.error(f'Database read error: {e}');return
 if data is None:st.info('Start the monitor to initialize the SQLite database.');return
 marks,pos,sig,evt,ticks,diag,health=(data[k] for k in ['marks','positions','signals','events','ticks','candle_diagnostics','monitor_health'])
 st.subheader('Monitor health')
 if not health.empty:
  h=health.iloc[0]; age=quote_age(h['observed_utc'])
  c1,c2,c3,c4,c5=st.columns(5)
  c1.metric('Monitor heartbeat',f'{age:.0f}s ago' if age is not None else 'Unknown')
  c2.metric('Broker offset',f"{int(h['offset_hours']):+d}h")
  c3.metric('Observed spread',f"{float(h['spread']):.3f}")
  c4.metric('Last evaluated M15',str(h['last_evaluated_m15']).replace('+00:00','')[-19:])
  c5.metric('Tick age at fetch',f"{float(h['tick_age_seconds']):.1f}s")
  if age is not None and age>max(refresh*3,90):st.error('Monitor heartbeat is stale. Check the monitor terminal.')
  elif age is not None and age>=0:st.success('Recent health record received.')
  st.caption(f"Latest observed GOLD bid/ask: {float(h['bid']):.3f} / {float(h['ask']):.3f} · {h['observed_utc']} UTC")
 else:st.warning('Step 11 monitor health not present yet. Start the upgraded paper_monitor.py.')
 st.subheader('Four independent virtual accounts')
 columns=st.columns(4)
 latest=ticks.iloc[0] if len(ticks) else None
 for col,style in zip(columns,STYLES):
  erow=marks[marks['style']==style]
  realized=float(erow.iloc[0]['equity']) if len(erow) else None
  open_positions=pos[(pos['style']==style)&(pos['status']=='OPEN')] if not pos.empty else pd.DataFrame()
  closed=pos[(pos['style']==style)&(pos['status']=='CLOSED')] if not pos.empty else pd.DataFrame()
  with col:
   st.metric(style.title(),money(realized) if realized is not None else '—')
   st.caption(f'{len(closed)} closed · {len(open_positions)} open · {len(sig[sig["style"]==style]) if len(sig) else 0} signals')
   if len(open_positions):
    st.caption('Open P&L not calculated: requires account-currency tick-value snapshot stored for each position. Displaying realized balance only.')
 st.subheader('M15 signal funnel')
 if diag.empty:
  st.info('No eligible completed M15 candle has been diagnosed yet. Startup and missed/offline candles are intentionally not backfilled.')
 else:
  funnel=[('H4 recent',int(diag.h4_recent.sum())),('H1 recent',int(diag.h1_recent.sum())),('M15 sweep',int(diag.m15_recent.sum())),('M15 inside',int(diag.m15_inside.sum())),('3-TF aligned',int(diag.aligned.sum()))]
  funnels=pd.DataFrame(funnel,columns=['Condition','Side-candle matches'])
  st.caption('Counts below are across BUY and SELL assessments, and are NOT a cumulative funnel or trade count.')
  st.bar_chart(funnels.set_index('Condition'))
  stylecounts=pd.DataFrame({'Method':list(STYLES),'Qualified M15 side-events':[int(diag[k].sum()) for k in STYLES]})
  st.dataframe(stylecounts,hide_index=True,use_container_width=True)
  st.subheader('Last evaluated candle — prerequisites and reasons')
  last=diag.iloc[0]['candle_close'];last_df=diag[diag.candle_close==last]
  st.caption(f'Latest diagnosed M15 candle-close boundary: {last}')
  display=['side','h4_recent','h1_recent','m15_recent','m15_inside','aligned','reentry','confirmation','structure','momentum','reason']
  st.dataframe(last_df[display],hide_index=True,use_container_width=True)
  reasons=diag.groupby('reason').size().sort_values(ascending=False).reset_index(name='side_candle_count')
  with st.expander('Rejection reasons and recent diagnostics'):
   st.dataframe(reasons,hide_index=True,use_container_width=True)
   st.dataframe(diag,hide_index=True,use_container_width=True)
   st.download_button('Download M15 diagnostics CSV',diag.to_csv(index=False).encode(),'gold_m15_diagnostics.csv','text/csv')
 st.subheader('Virtual trade activity')
 if len(pos):
  closed=pos[pos.status=='CLOSED']
  if len(closed):
   grouped=closed.groupby('style',as_index=False).agg(trades=('id','count'),net_pnl=('net_pnl','sum'),win_rate=('net_pnl',lambda x:100*(x>0).mean()))
   st.dataframe(grouped,hide_index=True,use_container_width=True)
   st.plotly_chart(px.bar(grouped,x='style',y='net_pnl',title='Closed-trade P&L by independent strategy'),use_container_width=True)
  else:st.info('No closed virtual trades yet.')
  active=pos[pos.status=='OPEN']
  if len(active):st.dataframe(active[['style','side','opened_utc','entry','stop','target','lots','spread_entry']],hide_index=True,use_container_width=True)
  st.download_button('Download virtual trade ledger',pos.to_csv(index=False).encode(),'gold_paper_positions.csv','text/csv')
 else:st.info('No virtual trades yet.')
 with st.expander('Signal journal'):
  if len(sig):st.dataframe(sig.sort_values('id',ascending=False),hide_index=True,use_container_width=True)
  else:st.info('No qualifying signal recorded.')
 with st.expander('Recent monitor events and warnings'):
  if len(evt):st.dataframe(evt.sort_values('id',ascending=False).head(200),hide_index=True,use_container_width=True)
 st.warning('Research only. Paper fill timing uses periodically observed quotes; missed intrapoll stops, spread changes, gaps, swap, margin rules, contract currency differences and broker DST history are not fully simulated. Read-only; no MT5 orders.')
view()
