"""Checks accelerated developing H1/H4 Bollinger Bands against reference implementation."""
import unittest
import numpy as np
import pandas as pd
from gold_exhaustion import DevelopingBandLookup, developing_bands, BAND_PARITY_VERSION

class FastBandTests(unittest.TestCase):
    def test_matches_reference_with_weekend_gap(self):
        print("Band parity version:", BAND_PARITY_VERSION)
        print("Pandas version:", pd.__version__)
        idx = pd.date_range('2026-09-01', periods=15000, freq='5min', tz='UTC')
        idx = idx[idx.weekday < 5].as_unit('us')  # pandas 3 default resolution
        x = 4000 + np.cumsum(np.random.default_rng(13).normal(0, .4, len(idx)))
        m5 = pd.DataFrame({'close': x}, index=idx)
        for tf in ('H1', 'H4'):
            lookup = DevelopingBandLookup(m5, tf)
            boundaries = pd.date_range(idx[0]+pd.Timedelta(days=5), idx[-1], freq='15min', tz='UTC')
            # Include the exact H1 timestamp that failed on the user's Windows setup.
            if tf == 'H1':
                boundaries = boundaries.union(pd.DatetimeIndex([pd.Timestamp('2026-09-08 07:00:00+00:00')]))
            valid = 0
            sampled = list(boundaries[::220])
            if tf == 'H1':
                sampled.append(pd.Timestamp('2026-09-08 07:00:00+00:00'))
            for t in sampled:
                original = developing_bands(m5, tf, t)
                fast = lookup.at(t)
                self.assertEqual(original is None, fast is None, (tf, t))
                if original is not None:
                    valid += 1
                    for key in ('upper','lower','middle','price'):
                        self.assertAlmostEqual(original[key], fast[key], places=5)
                    for key in ('above','below'):
                        self.assertEqual(original[key], fast[key])
            self.assertGreater(valid, 0)

if __name__ == '__main__':
    unittest.main()
