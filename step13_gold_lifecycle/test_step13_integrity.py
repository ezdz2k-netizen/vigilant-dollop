"""Regression checks for lifecycle CSV rows and simulated fill chronology."""
import unittest
from types import SimpleNamespace
from pathlib import Path
import tempfile
import pandas as pd
from step13_engine import lifecycle_paths,LIFECYCLE_COLUMNS,simulate

class IntegrityTests(unittest.TestCase):
    def test_lifecycle_records_not_column_names(self):
        i=pd.date_range('2026-01-05 09:00',periods=60,freq='5min',tz='UTC')
        df=pd.DataFrame({'high':[104.0]*60,'low':[98.0]*60,'close':[101.0]*60},index=i)
        episodes=[dict(key='H1:SELL:1',mode='H1',side='SELL',armed_at=i[9],start_price=101.0,atr=2.0,depth=.4)]
        paths=[]
        paths.extend(lifecycle_paths(df,episodes).to_dict(orient='records'))
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'lifecycle_paths.csv'
            pd.DataFrame(paths,columns=LIFECYCLE_COLUMNS).to_csv(file,index=False)
            loaded=pd.read_csv(file)
            self.assertIn('horizon_m15',loaded.columns)
            self.assertEqual(len(loaded),4)
            self.assertEqual(set(loaded.horizon_m15),{4,8,16,32})
            self.assertTrue((loaded.mfe_atr>=0).all())

    def test_empty_lifecycle_has_schema(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'lifecycle_paths.csv'
            pd.DataFrame([],columns=LIFECYCLE_COLUMNS).to_csv(f,index=False)
            self.assertEqual(list(pd.read_csv(f).columns),LIFECYCLE_COLUMNS)

    def test_entry_timestamp_and_quote_basis(self):
        i=pd.date_range('2026-01-05 09:00',periods=50,freq='5min',tz='UTC')
        df=pd.DataFrame({'open':100.0,'high':100.1,'low':99.9,'close':100.0,'spread':10.0},index=i)
        spec=SimpleNamespace(trade_tick_size=.01,trade_tick_value=1.,point=.01,volume_min=.01,volume_max=10.,volume_step=.01)
        cfg=SimpleNamespace(equity=10000.,risk=.005,slippage=.1,spread=.3,stop_atr=.25,commission=7.,max_hold=1)
        sig=dict(time=i[9],side='SELL',mode='H1',confirmation='reentry',filter='none',episode='demo',sweep_high=103.,sweep_low=95.,atr=2.,middle=99.,opposite=90.,depth=.5)
        trades=simulate(df,[sig],spec,cfg,target='middle')
        self.assertEqual(len(trades),1)
        t=trades[0]
        self.assertGreaterEqual(pd.Timestamp(t['entry_time']),pd.Timestamp(t['signal_known_at']))
        self.assertGreaterEqual(t['entry_delay_seconds'],0)
        self.assertEqual(t['entry_bid_open'],100.0)
        self.assertEqual(t['entry_fill_basis'],'next_available_M5_open_bid_plus_modeled_spread_slippage')

if __name__=='__main__':unittest.main(verbosity=2)
