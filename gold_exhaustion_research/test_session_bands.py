"""Regression tests for session-shortened GOLD H4 bars and causal BB lookups."""
import unittest
import pandas as pd
import numpy as np
from gold_exhaustion import developing_bands, DevelopingBandLookup

class SessionAwareBandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        idx = pd.date_range('2026-07-06', periods=60*24*12, freq='5min', tz='UTC')
        # Simulate weekend closure and GOLD daily 21:00-22:00 trading break.
        idx = idx[(idx.weekday < 5) & (~((idx.hour == 21)))]
        idx = idx.as_unit('us')
        price = 3500 + np.cumsum(np.random.default_rng(18).normal(0, .5, len(idx)))
        cls.m5 = pd.DataFrame({'close': price}, index=idx)

    def test_session_shortened_h4_matches_reference(self):
        boundaries = pd.date_range(self.m5.index[0] + pd.Timedelta(days=7),
                                   self.m5.index[-1], freq='15min', tz='UTC')
        for tf in ('H1','H4'):
            fast = DevelopingBandLookup(self.m5, tf)
            valid = 0
            for t in boundaries[::9]:
                slow_result = developing_bands(self.m5, tf, t)
                fast_result = fast.at(t)
                self.assertEqual(slow_result is None, fast_result is None, (tf, t))
                if slow_result is not None:
                    valid += 1
                    for key in ('middle','upper','lower','price'):
                        self.assertAlmostEqual(slow_result[key], fast_result[key], places=8)
            self.assertGreater(valid, 30, tf)

    def test_no_future_bar_leakage(self):
        t = pd.Timestamp('2026-07-21 16:15:00', tz='UTC')
        available = self.m5[self.m5.index < t].copy()
        altered = self.m5.copy()
        altered.loc[altered.index >= t, 'close'] += 10000
        for tf in ('H1','H4'):
            a = DevelopingBandLookup(available, tf).at(t)
            b = DevelopingBandLookup(altered, tf).at(t)
            self.assertIsNotNone(a)
            self.assertEqual(a,b)

if __name__ == '__main__':
    unittest.main()
