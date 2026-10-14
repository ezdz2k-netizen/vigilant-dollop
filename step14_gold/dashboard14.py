"""Read-only Step 14 paper validation dashboard. OpenAI review must be requested manually via CLI."""
import sqlite3
from pathlib import Path
import pandas as pd
import streamlit as st
import plotly.express as px
ROOT=Path(__file__).resolve().parent
st.set_page_config(page_title='GOLD Step 14 | Forward Validation',layout='wide')
st.title('GOLD — Forward Paper Validation & AI Research')
st.caption('Six independent virtual portfolios · Baseline vs weak-momentum breakeven · MT5 read-only')
path=st.sidebar.text_input('SQLite database',str(ROOT/'step14_paper.db'))
refresh=st.sidebar.selectbox('Refresh (seconds)',[5,10,30,60],index=1)

def fetch():
 if not Path(path).is_file():return None
 db=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro',uri=True,timeout=5)
 try:
  return {name:pd.read_sql_query(f'SELECT * FROM {name}',db) for name in ['portfolios','positions','evaluations','signals','quotes','events','ai_reviews']}
 finally:db.close()

@st.fragment(run_every=f'{refresh}s')
def view():
 try:d=fetch()
 except Exception as e:st.error(f'Database read failed: {e}');return
 if d is None:st.info('Start paper_monitor14.py to create the database.');return
 p=d['portfolios'];tr=d['positions'];ev=d['evaluations'];quotes=d['quotes'];sig=d['signals']
 if not quotes.empty:
  last=quotes.iloc[-1];st.caption(f"Latest observed quote: GOLD {last['bid']:.3f} / {last['ask']:.3f}; broker offset {int(last['offset_hours']):+d}h, observed {last['at_utc']}")
 cols=st.columns(4);cols[0].metric('M15 evaluations',len(ev));cols[1].metric('Signals recorded',len(sig));cols[2].metric('Open virtual positions',int((tr.status=='OPEN').sum()) if not tr.empty else 0);cols[3].metric('Closed virtual positions',int((tr.status=='CLOSED').sum()) if not tr.empty else 0)
 st.subheader('Six independent virtual portfolios')
 if not p.empty:
  table=p.copy();table['return_pct']=(table.equity/table.initial_equity-1)*100
  st.dataframe(table,hide_index=True,use_container_width=True)
  st.plotly_chart(px.bar(table,x='mode',y='return_pct',color='policy',barmode='group',title='Realized return by mode and policy (%)'),use_container_width=True)
 st.subheader('Realized closed-trade comparison')
 if tr.empty or not (tr.status=='CLOSED').any():st.info('No closed paper trades yet. No performance claims can be made.')
 else:
  done=tr[tr.status=='CLOSED'].copy()
  perf=done.groupby(['mode','policy'],as_index=False).agg(trades=('id','count'),net_pnl=('pnl','sum'),win_rate=('pnl',lambda a:round(100*(a>0).mean(),2)))
  st.dataframe(perf,hide_index=True,use_container_width=True)
  st.plotly_chart(px.bar(perf,x='mode',y='net_pnl',color='policy',barmode='group'),use_container_width=True)
  st.download_button('Download closed trades',done.to_csv(index=False),'step14_closed_trades.csv')
 with st.expander('Open paper positions'):
  if not tr.empty:st.dataframe(tr[tr.status=='OPEN'],hide_index=True,use_container_width=True)
 with st.expander('M15 evaluation coverage and skips'):
  if ev.empty:st.info('Waiting for newly completed M15 boundary. No retrospective entries.')
  else:
   st.dataframe(ev.tail(100).iloc[::-1],hide_index=True,use_container_width=True)
   st.bar_chart(ev.timing.value_counts())
 with st.expander('Signals and latest events'):
  if not sig.empty:st.dataframe(sig.tail(100).iloc[::-1],hide_index=True,use_container_width=True)
  if not d['events'].empty:st.dataframe(d['events'].tail(100).iloc[::-1],hide_index=True,use_container_width=True)
 st.subheader('Optional ChatGPT paper-trade post-mortems')
 st.caption('Reviews are created only when you explicitly run ai_postmortem.py with a closed position ID. The dashboard makes no API calls.')
 if not d['ai_reviews'].empty:
  reviews=d['ai_reviews'][['position_id','created_at','model','review_json']].copy();st.dataframe(reviews,hide_index=True,use_container_width=True)
 else:st.info('No AI post-mortems yet. API billing is separate from ChatGPT plans.')
 st.warning('All prices and P&L are simulated. The monitor polls, so intrapoll stop/target touches may be missed. Paper portfolios exclude floating P&L, swap, margin liquidation and exact tick execution. Never sends MT5 orders.')
view()
