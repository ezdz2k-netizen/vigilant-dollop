"""Small offline regression tests for Step 10A audit checks."""
import unittest
import tempfile
from pathlib import Path
import pandas as pd
from step10a_audit import read_ledger,audit_frame

class TestAudit(unittest.TestCase):
    def test_clean_and_broken_rows(self):
        base={'side':'BUY','entry_price':100.2,'exit_price':101.2,'gross_pnl':100.,'commission':1.,'net_pnl':99.,'r_multiple':.9,'equity_before':10000.,'equity_after':10099.,'lots':1.,'spread_price_at_entry':.2,'signal_broker_clock':'2026-01-01T12:00:00+00:00','entry_broker_clock':'2026-01-01T12:00:00+00:00','exit_broker_clock':'2026-01-01T12:05:00+00:00','exit_reason':'TARGET','period':'holdout','entry_style':'reentry'}
        bad={**base,'entry_broker_clock':'2026-01-01T11:55:00+00:00','net_pnl':101,'equity_after':10105}
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'holdout.csv'
            pd.DataFrame([base,bad]).to_csv(p,index=False)
            data=read_ledger(p,'holdout')
            counts,flags,_=audit_frame(data)
        self.assertEqual(counts['trade_count'],2)
        self.assertEqual(counts['entry_before_signal'],1)
        self.assertEqual(counts['pnl_identity_mismatch'],1)
        self.assertEqual(counts['equity_delta_mismatch'],1)
        self.assertEqual(counts['period_label_mismatch'],0)

if __name__ == '__main__':unittest.main()
