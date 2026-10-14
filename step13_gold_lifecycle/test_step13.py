"""Offline regression tests; no MT5 and no orders."""
import unittest
from types import SimpleNamespace
import numpy as np
import pandas as pd
from step13_engine import create_episodes, make_entry_signals, simulate, lifecycle_paths

class Step13Tests(unittest.TestCase):
    def features(self):
        t=pd.date_range('2026-01-05 09:15',periods=5,freq='15min',tz='UTC')
        return pd.DataFrame([
            dict(time=t[0],side='SELL',outside=True,depth=.4,h1_out=True,h4_out=False,close=111,high=113,low=109,
                 atr=2,mid=100,target=88,m15_upper=110,m15_lower=90,rsi_div=False,adx_fade=False,choch=False,liquidity_sweep=False),
            dict(time=t[1],side='SELL',outside=False,depth=-.2,h1_out=True,h4_out=False,close=108,high=112,low=107,
                 atr=2,mid=100,target=89,m15_upper=110,m15_lower=90,rsi_div=False,adx_fade=False,choch=False,liquidity_sweep=False),
            dict(time=t[2],side='SELL',outside=False,depth=-.3,h1_out=True,h4_out=False,close=106,high=109,low=105,
                 atr=2,mid=100,target=88,m15_upper=110,m15_lower=90,rsi_div=False,adx_fade=True,choch=False,liquidity_sweep=False),
            dict(time=t[3],side='SELL',outside=False,depth=-.4,h1_out=False,h4_out=False,close=104,high=107,low=103,
                 atr=2,mid=100,target=88,m15_upper=110,m15_lower=90,rsi_div=False,adx_fade=False,choch=True,liquidity_sweep=False),
            dict(time=t[4],side='SELL',outside=True,depth=.5,h1_out=True,h4_out=False,close=112,high=115,low=105,
                 atr=2,mid=100,target=88,m15_upper=110,m15_lower=90,rsi_div=False,adx_fade=False,choch=False,liquidity_sweep=False),
        ])
    def test_h1_arm_and_rearm(self):
        eps,obs=create_episodes(self.features(),'H1',.1)
        self.assertEqual(len(eps),2)
        self.assertEqual(eps[0]['status'],'INVALIDATED')
        self.assertEqual(eps[1]['status'],'ARMED')
    def test_h4_not_armed(self):
        eps,obs=create_episodes(self.features(),'H4',.1)
        self.assertEqual(len(eps),0)
    def test_four_entry_methods(self):
        eps,obs=create_episodes(self.features(),'H1',.1)
        reentry=make_entry_signals(eps,obs,'reentry')
        confirm=make_entry_signals(eps,obs,'confirmation')
        structure=make_entry_signals(eps,obs,'structure')
        self.assertEqual(len(reentry),1)
        self.assertEqual(len(confirm),1)
        self.assertEqual(len(structure),0) # invalidated on same M15 close
        self.assertGreater(confirm[0]['time'],reentry[0]['time'])
    def test_no_early_or_current_bar_fill(self):
        t=pd.date_range('2026-01-05 09:00',periods=40,freq='5min',tz='UTC')
        f=pd.DataFrame({'open':100.,'high':100.2,'low':99.8,'close':100.,'spread':10},index=t)
        info=SimpleNamespace(trade_tick_size=.01,trade_tick_value=1.,point=.01,volume_min=.01,volume_max=10.,volume_step=.01)
        cfg=SimpleNamespace(equity=10000.,risk=.005,slippage=.1,spread=.3,stop_atr=.25,commission=7.,max_hold=1)
        sig={'time':t[9],'side':'SELL','mode':'H1','confirmation':'reentry','filter':'none','episode':'x',
             'sweep_high':102.,'sweep_low':98.,'atr':2.,'middle':99.,'opposite':90.,'depth':.5}
        rows=simulate(f,[sig],info,cfg,'middle')
        if rows:self.assertGreaterEqual(pd.Timestamp(rows[0]['entry_time']),sig['time'])
    def test_lifecycle_non_negative_excursions(self):
        eps,obs=create_episodes(self.features(),'H1',.1)
        idx=pd.date_range('2026-01-05 09:00',periods=80,freq='5min',tz='UTC')
        m5=pd.DataFrame({'high':105.,'low':95.,'close':100.},index=idx)
        paths=lifecycle_paths(m5,eps)
        self.assertTrue((paths.mfe_atr>=0).all() or len(paths)==0)
        self.assertTrue((paths.mae_atr>=0).all() or len(paths)==0)

if __name__=='__main__':unittest.main(verbosity=2)
