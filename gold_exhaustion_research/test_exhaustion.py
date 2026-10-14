"""Offline regression tests; NO MT5 and no orders."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
import pandas as pd
import numpy as np
from gold_exhaustion import developing_bands,signal_events,simulate

class ResearchTests(unittest.TestCase):
 def test_developing_band_no_future(self):
  idx=pd.date_range('2026-01-01',periods=260,freq='5min',tz='UTC')
  df=pd.DataFrame({'close':np.arange(260)*.01+100},index=idx)
  boundary=pd.Timestamp('2026-01-01 21:00:00',tz='UTC')
  a=developing_bands(df,'H1',boundary)
  self.assertIsNotNone(a)
  df2=df.copy();df2.loc[df2.index>=boundary,'close']=9999.
  b=developing_bands(df2,'H1',boundary)
  self.assertEqual(a,b)
 def test_conditional_invalidation_and_rearm(self):
  start=pd.Timestamp('2026-01-01 12:00',tz='UTC'); row=[]
  def put(i,out,depth,h1,h4,choch=False):
   row.append(dict(time=start+pd.Timedelta(minutes=15*i),side='SELL',outside=out,depth=depth,
                  h1_out=h1,h4_out=h4,high=110+i,low=99,atr=2.,mid=100.,target=90.,
                  adx=25.,rsi_div=False,adx_fade=False,choch=choch,liquidity_sweep=False))
  put(0,True,.4,True,False)
  put(1,False,-.1,False,False,choch=True) # invalidated, no signal
  put(2,False,-.1,False,False,choch=True)
  put(3,True,.5,True,False) # fresh rearm
  put(4,True,.35,True,False,choch=True)
  found=signal_events(pd.DataFrame(row),'H1','choch',.3)
  self.assertEqual(len(found),1)
  self.assertEqual(found[0]['time'],start+pd.Timedelta(minutes=60))
 def test_no_future_segment_trades(self):
  idx=pd.date_range('2026-01-01',periods=100,freq='5min',tz='UTC')
  df=pd.DataFrame({'open':100.,'high':101.,'low':99.,'close':100.,'spread':1},index=idx)
  sig=[dict(time=idx[5],side='BUY',sweep_low=95.,sweep_high=105.,atr=1.,middle=101.,opposite=102.,mode='H1',confirmation='choch',depth=.3)]
  info=SimpleNamespace(trade_tick_size=.01,trade_tick_value=1.,point=.01,volume_min=.01,volume_max=10.,volume_step=.01)
  cfg=SimpleNamespace(equity=10000,risk=.005,commission=7.,spread=.3,slippage=.1,stop_atr=.25,max_hold=48.)
  self.assertEqual(simulate(df,sig,info,cfg,segment_end=idx[10]),[])

if __name__=='__main__':unittest.main()
