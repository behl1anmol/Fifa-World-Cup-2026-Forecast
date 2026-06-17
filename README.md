# FIFA World Cup 2026 — Forecast

A live, all-Python title-odds tracker for the 2026 World Cup. It predicts
per-match outcomes, Monte Carlo–simulates the remaining bracket, and counts how
often each surviving team wins — measured by **calibration**, not by naming a
champion. See [`docs/architecture-overview.md`](docs/architecture-overview.md)
for the full design (the source of truth).

The project is built in steps following the architecture's §8 build sequence.

## Build status

- ✅ **Step 1 — Project scaffold & data layer** *(this step)*
- ⬜ Step 2 — Elo engine (point-in-time)
- ⬜ Step 3 — Monte Carlo simulator (the spine)
- ⬜ Step 4 — Scoreline model & blend
- ⬜ Step 5 — Calibration harness
- ⬜ Step 6 — Update loop & snapshots
- ⬜ Step 7 — API & dashboard

## Layout

```
datasets/        raw data, one subfolder per source (never interlinked)
  martj42/       international results 1872–present (incl. WC2026 fixtures)
  eloratings/    World Football Elo (feature / sanity check)
  odds_api/      deferred (needs API key) — placeholder
  transfermarkt/ deferred (optional, scrape) — placeholder
  fifa_2026/     fixtures come from martj42 — note only
src/forecast/    application package (config, db, loader, data_sources)
scripts/         CLIs: fetch_data, init_db, load_data
tests/           offline pytest suite
docs/            architecture overview
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Data layer (Step 1)

```bash
# 1. (Optional) refresh raw datasets from upstream — they are also committed.
python scripts/fetch_data.py                 # all sources
python scripts/fetch_data.py --source martj42

# 2. Create the SQLite schema (architecture §5).
python scripts/init_db.py

# 3. Load martj42 results into teams + matches and print row counts.
python scripts/load_data.py                  # idempotent: re-run = same counts
```

The database (`forecast.db`) is git-ignored and fully rebuildable from
`datasets/`. The Odds API and Transfermarkt are deferred with placeholder
READMEs; the core never depends on them.

## Tests

```bash
pytest -q
```

The suite is offline (uses a small in-repo fixture CSV and an in-memory DB) and
covers schema creation and loader idempotency.
