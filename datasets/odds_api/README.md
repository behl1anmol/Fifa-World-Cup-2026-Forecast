# The Odds API (deferred)

**Status:** placeholder — not fetched in Step 1.

The Odds API ([the-odds-api.com](https://the-odds-api.com)) provides match odds,
used as a model **feature** and a calibration **reference** (architecture §6,
Decision #6). It is **not required by the core forecast** and is first consumed
in Step 5 (calibration harness).

## Why deferred
- Requires an API key. A probe without a key returns `401 MISSING_KEY`.
- Free tier only; the core app must never depend on it.

## How to enable
```bash
export ODDS_API_KEY=your_key_here
python scripts/fetch_data.py --source odds_api
```
The fetcher (`forecast.data_sources.fetch_odds_api`) skips gracefully and does
not fail when no key is present. Fetched odds land in this folder as JSON.
