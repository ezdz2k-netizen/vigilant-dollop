"""Offline regression tests for research-only execution replay."""
import unittest
from types import SimpleNamespace
import pandas as pd
import numpy as np
import step13_2_engine as e

class ReplayTests(unittest.TestCase):
 def setUp(self):
  idx=pd.date_range('2026-01-05 10:00',periods=240,freq='min',tz='UTC')
  price=100+np.linspace(0,2,len(idx))
  self.bars=pd.DataFrame(dict(open=price,high=price+.05,low=price-.05,close=price,spread=np.full(len(idx),2)),index=idx)
  self.sig=dict(time=idx[0],side='BUY',mode='H1',confirmation='reentry',sweep_low=98.,sweep_high=101.,atr=1.,middle=103.,opposite=106.)
  self.info=SimpleNamespace(point=.01,trade_tick_size=.01,trade_tick_value=1.,volume_step=.01,volume_min=.01,volume_max=10.)
  self.cfg=SimpleNamespace(stop_atr=.25,spread=.3,spread_mult=1.,slippage=.1,commission=7.,risk=.005,equity=10000.,max_hold=2.,momentum_body=.5,momentum_progress=.1,activation_r=1.)
 def test_entry_not_before_signal(self):
  r=e.trade_replay(self.bars,self.sig,self.info,self.cfg,e.CONFIGS[0]);self.assertIsNotNone(r)
  self.assertGreaterEqual(pd.Timestamp(r['entry_time']),pd.Timestamp(self.sig['time']))
 def test_delay_requires_favorable_move(self):
  r=e.trade_replay(self.bars,self.sig,self.info,self.cfg,e.CONFIGS[2]);self.assertIsNotNone(r)
  self.assertGreater(float(r['entry_delay_minutes']),0)
 def test_favorable_not_triggered_in_flat_market(self):
  f=self.bars.copy();f[['open','high','low','close']]=[100,100,100,100]
  self.assertIsNone(e.trade_replay(f,self.sig,self.info,self.cfg,e.CONFIGS[3]))
 def test_short_direction_and_no_orders(self):
  from pathlib import Path
  src=Path(e.__file__).read_text(encoding='utf-8');self.assertNotIn('order_send(',src)
  s={**self.sig,'side':'SELL','sweep_high':103.,'middle':98.,'opposite':96.}
  r=e.trade_replay(self.bars,s,self.info,self.cfg,e.CONFIGS[0]);self.assertIsNotNone(r)
  self.assertGreaterEqual(r['entry'],r['exit']) if r['pnl']>0 else None
 def test_no_future_fill_when_data_gapped(self):
  f=self.bars.iloc[8:];self.assertIsNone(e.trade_replay(f,self.sig,self.info,self.cfg,e.CONFIGS[0]))
 def test_weak_momentum_only_after_full_candle(self):
  r=e.trade_replay(self.bars,self.sig,self.info,self.cfg,e.CONFIGS[5]);self.assertIsNotNone(r)
  if r['exit_reason']=='WEAK_EXIT':self.assertGreaterEqual(pd.Timestamp(r['exit_time']),pd.Timestamp(self.sig['time'])+pd.Timedelta(minutes=15))

if __name__=='__main__':unittest.main(verbosity=2)
