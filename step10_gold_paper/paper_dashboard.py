"""Read-only Streamlit dashboard for GOLD four-method forward paper monitor."""
from pathlib import Path
import sqlite3
import pandas as pd
import plotly.express as px
import streamlit as st
from paper_core import STYLES

DB=Path(__file__).resolve().parent/'paper_trades.db'
st.set_page_config(page_title='GOLD Triple BB | Forward Paper',layout='wide')
st.title('GOLD Triple Bollinger Band — Forward Paper Trading')
st.caption('READ-ONLY dashboard · No real orders · Four independent virtual portfolios · FxPro GOLD')
with st.sidebar:
 st.header('Display')
 path=st.text_input('SQLite database',str(DB))
 refresh=st.selectbox('Refresh every (seconds)',[5,10,30,60],index=1)
 st.caption('Keep paper_monitor.py running in a separate VS Code terminal.')
@st.fragment(run_every=f'{refresh}s')
def app():
 if not Path(path).is_file():
  st.info('No paper database yet. Start the monitor and wait for it to initialize.');return
 try:
  db=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro',uri=True)
  marks=pd.read_sql_query('SELECT * FROM marks',db)
  pos=pd.read_sql_query('SELECT * FROM positions ORDER BY id DESC',db)
  sig=pd.read_sql_query('SELECT * FROM signals ORDER BY id DESC',db)
  evt=pd.read_sql_query('SELECT * FROM events ORDER BY id DESC LIMIT 200',db)
  ticks=pd.read_sql_query('SELECT * FROM ticks ORDER BY id DESC LIMIT 500',db)
  db.close()
 except Exception as e:
  st.error(f'Cannot read database: {e}');return
 if len(ticks):
  t=ticks.iloc[0];st.caption(f'Last observed quote: {t.at_utc} · bid {t.bid:.3f} / ask {t.ask:.3f} · quote age based on logged time (not guaranteed live)')
 st.subheader('Independent paper accounts')
 cols=st.columns(4)
 for col,style in zip(cols,STYLES):
  eq=marks.loc[marks['style']==style,'equity'];closed=pos[(pos['style']==style)&(pos['status']=='CLOSED')];active=pos[(pos['style']==style)&(pos['status']=='OPEN')]
  with col:
   st.metric(style.capitalize(),f'{float(eq.iloc[0]):,.2f}' if len(eq) else '—')
   st.caption(f'{len(closed)} closed · {len(active)} open · {len(sig[sig["style"]==style])} signals')
 left,right=st.columns(2)
 with left:
  st.subheader('Closed-trade outcomes')
  if not pos.empty:
   closed=pos[pos.status=='CLOSED'].copy()
   if len(closed):
    f=closed.groupby('style').agg(trades=('id','count'),net_pnl=('net_pnl','sum'),win_rate=('net_pnl',lambda v:100*(v>0).mean()),avg_net=('net_pnl','mean')).reset_index()
    st.dataframe(f,use_container_width=True,hide_index=True)
   else:st.info('No paper trades closed yet.')
  else:st.info('No trades yet. This may take days depending on the setups.')
 with right:
  st.subheader('Closed-trade P&L')
  if len(pos) and (pos.status=='CLOSED').any():
   tmp=pos[pos.status=='CLOSED'];st.plotly_chart(px.bar(tmp.groupby('style',as_index=False)['net_pnl'].sum(),x='style',y='net_pnl'),use_container_width=True)
  else:st.info('Awaiting realized paper P&L.')
 st.subheader('Open virtual positions')
 if len(pos):
  show=pos[pos.status=='OPEN']
  if len(show):st.dataframe(show[['style','side','opened_utc','entry','stop','target','lots','spread_entry','risk_cash']],hide_index=True,use_container_width=True)
  else:st.info('No open paper positions.')
 else:st.info('None')
 st.subheader('Signal journal')
 if len(sig):st.dataframe(sig[['recorded_at_utc','style','side','signal_clock','status','detail']].head(100),hide_index=True,use_container_width=True)
 else:st.info('No qualifying M15 signals recorded since monitor startup.')
 st.subheader('Closed trade ledger')
 if len(pos):
  st.dataframe(pos[pos.status=='CLOSED'].head(200),hide_index=True,use_container_width=True)
  st.download_button('Download trade ledger CSV',pos.to_csv(index=False).encode('utf-8'),'paper_trade_ledger.csv','text/csv')
 with st.expander('Monitor events and warnings'):
  if len(evt):st.dataframe(evt,hide_index=True,use_container_width=True)
 st.warning('Paper fills use observed quotes at polling times, not guaranteed exchange fills. Stops can be crossed between polls; swap, margin, price gaps, and broker restrictions are not modeled. Contract tick-value assumptions may not perfectly match your account currency.')
app()
