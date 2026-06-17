# The Odds API

The Odds API ([the-odds-api.com](https://the-odds-api.com)) provides match odds,
used as a calibration **reference** (architecture §6, Decision #6) in Step 5 and as a
model **feature** in Step 8. It is **not required by the core forecast**.

## Files
- `wc2026_h2h_odds.sample.json` — a small, **illustrative SAMPLE** in the real Odds
  API `h2h` format (committed). It lets the calibration harness and tests run offline.
  It is **not** real market data; do not treat its numbers as a genuine market read.
- `wc2026_h2h_odds.json` — the live snapshot written by the fetcher (git-ignored).

## How to enable live odds
```bash
export ODDS_API_KEY=your_key_here       # never commit the key
python scripts/fetch_data.py --source odds_api
```
The fetcher (`forecast.data_sources.fetch_odds_api`) reads `ODDS_API_KEY` from the
environment, skips gracefully (no failure) when the key is absent, and writes
`wc2026_h2h_odds.json` here. The calibration harness prefers the live file and falls
back to the sample.

## Note on the free tier
The free tier returns only **upcoming** odds (no historical backfill), so scored
market calibration accrues as fixtures complete with odds captured beforehand.
