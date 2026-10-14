import unittest
from types import SimpleNamespace
import pandas as pd
import numpy as np
from probability_engine import evaluate_path, mode_matches

class TestLabelling(unittest.TestCase):
 def row(self):
  return SimpleNamespace(time=pd.Timestamp('2026-01-06T00:15:00Z'),side='BUY',low=99.,high=100.,atr=1.,mid=101.,target=103.,h1_out=True,h4_out=False)
 def m5(self, highs, lows):
  tt=pd.date_range('2026-01-06T00:15:00Z',periods=len(highs),freq='5min')
  return pd.DataFrame({'time_dt':tt,'open':[100.]*len(tt),'high':highs,'low':lows,'spread':[0]*len(tt)})
 def test_target(self):
  a=self.row(); d=self.m5([101.5]*12,[99.8]*12)
  result=evaluate_path(a,0,d,4,.25,.1,.01,0.,'middle')
  self.assertEqual(result['outcome'],'TARGET')
 def test_stop_first_tie(self):
  a=self.row();d=self.m5([102.]*12,[98.]*12)
  result=evaluate_path(a,0,d,4,.25,.1,.01,0.,'middle')
  self.assertEqual(result['outcome'],'STOP')
 def test_no_retrospective_entry(self):
  a=self.row();d=self.m5([101.]*12,[99.8]*12)
  d.loc[0,'time_dt']+=pd.Timedelta(minutes=5)
  self.assertIsNone(evaluate_path(a,0,d,4,.25,.1,.01,0.,'middle'))
 def test_modes_inclusive(self):
  self.assertEqual(mode_matches(self.row()),['H1'])
  a=self.row();a.h4_out=True
  self.assertEqual(mode_matches(a),['H1','H4','BOTH'])
 def test_missing_horizon(self):
  a=self.row();d=self.m5([101.]*3,[99.]*3)
  self.assertIsNone(evaluate_path(a,0,d,4,.25,.1,.01,0.,'middle'))

if __name__=='__main__': unittest.main()
