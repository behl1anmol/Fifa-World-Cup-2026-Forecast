# Step 1 — Project scaffold & data layer

## Context

We are building the live FIFA World Cup 2026 forecasting app described in
`docs/architecture-overview.md` (the source of truth). The repo is currently
empty except for `docs/` and `README.md`, on branch
`claude/fifa-2026-forecasting-app-lzq15h`.

Step 1 (§8 item 1, "Must") lays the foundation everything else stands on: a
Python project skeleton, the SQLite schema from §5, and an **idempotent** loader
that turns the martj42 international-results dataset into `teams` + `matches`
rows. It also establishes how every data source in §6 is fetched and stored.

### Data-source reconnaissance (already verified live)

| Source | Access | Reachable | Notes |
|---|---|---|---|
| martj42 `results.csv` | direct CSV | ✅ 49,478 rows | **Already contains all 72 WC2026 group fixtures** (20 completed as of 2026-06-17, rest `NA`). Doubles as the FIFA fixtures/results feed. |
| martj42 `shootouts.csv`, `goalscorers.csv`, `former_names.csv` | direct CSV | ✅ | aux: shootout history, scorers, team renames (name normalization). |
| eloratings.net `2026.tsv`, `en.teams.tsv` | direct TSV | ✅ | ratings + code→name map. Feature/sanity-check only (we self-compute Elo in Step 2). |
| The Odds API | REST + key | ⚠️ 401 (no key) | Not needed until Step 5. **Deferred with placeholder.** |
| Transfermarkt | scrape | n/a | Optional/cached/Step 8. **Deferred with placeholder.** |
| FIFA site | JS-heavy | 200 | Unneeded — fixtures already in martj42. |

**Scraping verdict: none required.** Every source the core depends on is a
direct file/API download. The only scrape (Transfermarkt) is explicitly optional
and deferred to Step 8.

### Decisions (confirmed with user)
- **Commit raw data AND ship a fetch script.** All files ≤3.7 MB, so commit
  everything; the fetch script refreshes them and would be the on-demand path if
  any file were ever "very very large".
- **Defer Odds API + Transfermarkt** as folders with READMEs + a graceful stub
  fetcher (reads `ODDS_API_KEY` from env, skips if absent).

-----

## Proposed layout

```
requirements.txt              # pandas numpy scipy statsmodels fastapi uvicorn requests streamlit pytest
README.md                     # updated: setup, fetch, init-db, load, test
.gitignore                    # add forecast DB + *.db-journal
datasets/                     # per-source subfolders, NEVER merged ("not interlinked")
  martj42/        results.csv shootouts.csv goalscorers.csv former_names.csv SOURCE.md
  eloratings/     2026.tsv en.teams.tsv SOURCE.md
  odds_api/       README.md          # key requirement, deferred
  transfermarkt/  README.md          # ToS note, optional, deferred
  fifa_2026/      README.md          # note: fixtures come from martj42
src/forecast/
  __init__.py
  config.py        # central paths, source URLs, DB path, model_version
  db.py            # connect() + create_schema() — §5 tables exactly
  data_sources.py  # fetch each source -> datasets/<src>/, retry+backoff, write SOURCE.md
  loader.py        # parse martj42 results.csv -> teams + matches (idempotent upsert)
scripts/
  fetch_data.py    # CLI: refresh all datasets (optional --source)
  init_db.py       # CLI: create schema
  load_data.py     # CLI: run loader, print teams/matches row counts  <-- acceptance
tests/
  conftest.py      # tiny in-repo fixture CSV, temp DB (NO network)
  test_db.py       # schema/tables/columns created
  test_loader.py   # idempotency (run twice = same counts), team extraction, NA->score upsert
```

Stdlib `sqlite3` (not SQLAlchemy) per the "fewest moving parts" principle (§3.1).

-----

## SQLite schema (`db.py`, exactly §5)

- **teams**: `id` PK, `name` UNIQUE, `confederation` (NULL for now — not in martj42),
  `current_elo` (NULL until Step 2).
- **matches**: `id` PK, `date`, `stage`, `home` FK→teams.id, `away` FK→teams.id,
  `result`, `feature_snapshot` (JSON). `UNIQUE(date, home, away, stage)`.
- **ratings_history**: `team_id`, `match_id`, `elo_before`, `elo_after`,
  `timestamp`. (created, populated in Step 2.)
- **predictions**: `run_id`, `model_version`, `timestamp`, `team_id`,
  `stage_probabilities`, `title_prob`. (created, populated later.)

**Faithful, lossless encoding** (keeps §5 fields exactly while preserving data
needed by Dixon-Coles/Elo later):
- `stage` = martj42 `tournament` value (e.g. "Friendly", "FIFA World Cup").
  Knockout-round labels are refined in the simulator step.
- `result` = scoreline string `"h:a"` (e.g. `"2:0"`), `NULL` when score is `NA`
  (unplayed). Scores parsed back out by the Elo/scoreline models.
- `feature_snapshot` JSON = `{"neutral", "city", "country", "tournament"}`.

## Loader (`loader.py`) — idempotency strategy

1. Read local `datasets/martj42/results.csv` with pandas (loader is offline &
   deterministic; `fetch_data.py` is the only network path).
2. Teams: unique names from `home_team` ∪ `away_team` → `INSERT OR IGNORE`
   (never clobbers `current_elo` on re-run).
3. Matches: map to team ids; `INSERT ... ON CONFLICT(date,home,away,stage) DO
   UPDATE SET result=excluded.result, feature_snapshot=excluded.feature_snapshot`.
   → re-running never duplicates, and a fixture whose score flips from `NA` to a
   real result (the live tournament case) is **updated in place**.
4. Print `teams` and `matches` row counts.

## Fetch script (`data_sources.py` + `scripts/fetch_data.py`)
- One function per source; downloads into `datasets/<source>/` with retry +
  exponential backoff (2/4/8/16s), writes a `SOURCE.md` recording URL, license,
  and fetch timestamp.
- Odds API stub: reads `ODDS_API_KEY`; if absent, prints a skip notice and
  leaves the placeholder README. No failure.
- Transfermarkt: no fetch (scrape deferred); README only.

-----

## Tests (offline, pytest)
- `test_db.py`: `create_schema()` produces all 4 tables with expected columns.
- `test_loader.py`:
  - load fixture twice → identical `teams`/`matches` counts (**acceptance**).
  - team set extracted correctly from a tiny fixture.
  - a row with `NA` score then re-loaded with a real score → **updated, not
    duplicated**, and `result` reflects the score.
- Fixture is a ~6-row CSV checked into `tests/`; no network in tests.

## Verification (acceptance)
1. `pip install -r requirements.txt`
2. `python scripts/fetch_data.py` → datasets populated (or already committed).
3. `python scripts/init_db.py` → schema created.
4. `python scripts/load_data.py` then run it **again** → row counts identical
   across both runs; prints team & match counts.
5. `pytest -q` → all green.

Then STOP for user verification before Step 2 (Elo engine).
