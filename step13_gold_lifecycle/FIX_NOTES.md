# Step 13.1 integrity fix

## Confirmed defect
`lifecycle.extend(lifecycle_paths(m5,eps))` iterated a pandas DataFrame and
appended its column labels, producing a malformed single-column CSV (`0`).

## Code repairs
- Serializes actual lifecycle records and writes an explicit CSV schema, including when empty.
- Dashboard validates columns and horizon options; it won't crash on old files.
- Trade ledger logs the signal decision time, entry bar opening time, delay,
  observed historical bid OHLC open, modeled spread and slippage.
- Added three offline tests covering record counts, empty CSV schema, and entry timing.

## Boundaries
- This is still next-M5-open **historical simulation**, not tick-verified fills.
- Same-timestamp signal and entry can be causally possible where an M15 close coincides with the next M5 open, but no executable quote is proven.
- Historical DST/broker timezone and true tick fill quality remain unverified.
- Strategy parameter definitions and paper-trading scripts are unchanged.
