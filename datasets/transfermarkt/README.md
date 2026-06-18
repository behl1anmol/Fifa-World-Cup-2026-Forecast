# Transfermarkt / squad-strength (optional, cached) — Step 8

**Status:** implemented as an **optional, cached, opt-in** feature. The core forecast
**never depends on it** (architecture §6, §7 Compliance).

## What lives here
- `squad_strength.sample.json` — a committed, clearly-labelled **[SAMPLE]** illustrative
  squad-value extract for the 48 WC2026 participants. Hand-authored and approximate; it
  is **not scraped**. It exists so the feature is demonstrable and the test suite runs
  offline.
- `squad_strength.json` — an optional **cached** live extract (git-ignored). When present
  it takes precedence over the sample. This project never scrapes it automatically.

## Why cached and not scraped
- Acquiring Transfermarkt data requires scraping, which carries terms-of-service
  exposure (architecture §7, Compliance). Nothing in this repo scrapes the site.
- The feature is **cached** so the core never relies on a live scrape, and **opt-in**
  (`config.SQUAD_STRENGTH_ENABLED`, default `False`) so the default forecast is
  unaffected.

## How it is used
`src/forecast/squad_strength.py` reads the cache, z-scores each team's strength across
the participants, and turns it into a small additive **Elo nudge**
(`SQUAD_STRENGTH_ELO_SCALE` Elo points at +1σ) applied to the *live* 2026 simulation
only. It is deliberately kept out of the historical calibration backtest: we only have a
current squad snapshot, and applying today's values to past matches would be
anachronistic and leak information — so squad strength can never regress the Step 5
calibration baseline.

To populate a real cache, drop a JSON file at `squad_strength.json` with the schema
`{"teams": {"<team name>": <value>}}` and set `SQUAD_STRENGTH_ENABLED = True`.
