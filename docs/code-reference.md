# Code Reference

A map of the codebase: what every module in `src/forecast/` does, and what every script in
`scripts/` is for. Use this to find *where* something lives; for *how* the ideas work, see
[Concepts](concepts.md), and for *how it flows*, see [How It Works](how-it-works.md).

← Back to the [documentation index](README.md).

## Project layout

```
src/forecast/      the application package (importable as `forecast`)
scripts/           thin command-line wrappers around the package
tests/             the offline pytest suite (see testing.md)
datasets/          raw data, one folder per source (see data.md)
docs/              this documentation
reports/           generated calibration artifacts (git-ignored)
forecast.db        the SQLite database (git-ignored, rebuildable)
```

## Modules in `src/forecast/`

The package is a **linear pipeline** with no circular dependencies. Modules are grouped below
by role, roughly in the order data flows through them.

### Foundation

| Module | Purpose | Key items |
|--------|---------|-----------|
| [`config.py`](../src/forecast/config.py) | Single source of truth for **all** file paths, data-source URLs, and tunable parameters. Every other module imports its constants from here. | `DB_PATH`, `N_SIMS`, `SIM_SEED`, `BLEND_WEIGHT`, `ELO_*`, `HOST_NATIONS`, `DATA_SOURCES`. Full table in [Operations](operations.md#configuration-reference). |
| [`db.py`](../src/forecast/db.py) | SQLite schema and connection helpers. Plain `sqlite3` (no ORM) — the data model is just four small tables. | `SCHEMA`, `connect()`, `create_schema()`, `EXPECTED_COLUMNS`. |
| [`data_sources.py`](../src/forecast/data_sources.py) | The **only** place that touches the network: downloads raw datasets with retry/backoff and records provenance. | `fetch_source()`, `fetch_odds_api()`, `fetch_all()`. |
| [`loader.py`](../src/forecast/loader.py) | Loads `martj42/results.csv` into the `teams` and `matches` tables. Idempotent (upserts). | `load()`, `load_teams()`, `load_matches()`, `_scoreline()`. |

### Ratings

| Module | Purpose | Key items |
|--------|---------|-----------|
| [`elo.py`](../src/forecast/elo.py) | The **pure** Elo maths — no I/O, no database. Exactly the knobs the design enumerates (K, home advantage, optional margin-of-victory). | `EloConfig`, `expected_score()`, `goal_difference_index()`, `update_ratings()`. |
| [`ratings.py`](../src/forecast/ratings.py) | Replays all of history in date order to produce **point-in-time** (leak-free) Elo, stored in `ratings_history`. | `replay_history()`, `pretournament_elos()`, `load_reference_elo()`. |

### Match model

| Module | Purpose | Key items |
|--------|---------|-----------|
| [`dixon_coles.py`](../src/forecast/dixon_coles.py) | The **pure** Dixon-Coles goal maths: turn two expected-goal rates into a scoreline grid and win/draw/loss probabilities, with the low-score (τ/ρ) correction. | `tau()`, `scoreline_matrix()`, `outcome_probs()`, `fit_rho()`. |
| [`match_model.py`](../src/forecast/match_model.py) | Combines the Dixon-Coles "goals" view and the Elo "rating" view into one blended win/draw/loss forecast, and **fits** all parameters from history (leak-free). | `MatchModelParams`, `team_lambdas()`, `elo_outcome()`, `blend()` / `blend_n()`, `predict()` / `predict3()`, `fit_match_model()`. |
| [`gbm_view.py`](../src/forecast/gbm_view.py) | **Optional** LightGBM third view. Fully isolated; degrades to `None` if `lightgbm` is missing or data is thin. The core never imports `lightgbm`. | `GBMView`, `fit_gbm_view()`, `lightgbm_available()`. |
| [`squad_strength.py`](../src/forecast/squad_strength.py) | **Optional** squad-value Elo nudge (off by default). Reads a cached JSON, z-scores it, applies a small Elo delta to 2026 teams only. | `squad_elo_adjustments()`, `adjusted_elo_override()`, `resolve_squad_path()`. |

### Simulation

| Module | Purpose | Key items |
|--------|---------|-----------|
| [`tournament.py`](../src/forecast/tournament.py) | Static 2026 bracket structure + loaders for the FIFA data artifacts (groups, the 495-row third-place table). | `R32_MATCHES`, `BRACKET`, `load_groups()`, `load_third_place_table()`, `third_place_assignment()`. |
| [`simulator.py`](../src/forecast/simulator.py) | **The spine.** Plays the remaining bracket 50,000 times (vectorised, seeded) and counts stage/title outcomes. Writes snapshots. | `simulate()`, `write_predictions()`, `_play_knockout()`, `_simulate_group()`. |

### Evaluation

| Module | Purpose | Key items |
|--------|---------|-----------|
| [`metrics.py`](../src/forecast/metrics.py) | The scoring rules and the reliability diagram. | `rps()`, `brier()`, `log_loss()`, `reliability_curve()`, `save_reliability_diagram()`. |
| [`market.py`](../src/forecast/market.py) | Parses bookmaker odds JSON and de-vigs it into win/draw/loss probabilities mapped to our fixtures. | `decimal_to_implied()`, `devig()`, `load_odds_json()`, `map_odds_to_matches()`, `market_probs_by_match_id()`. |
| [`calibration.py`](../src/forecast/calibration.py) | The harness: leak-free time-split backtest, the three-way model/market/odds-free comparison, blend-weight grid search, and the markdown report. | `build_backtest()`, `tune_blend_weight()`, `three_way()`, `check_no_regression()`, `build_report()`. |

### Live operation & serving

| Module | Purpose | Key items |
|--------|---------|-----------|
| [`update_loop.py`](../src/forecast/update_loop.py) | The in-tournament loop: ingest a result, re-run the chain, write a deterministic snapshot. Also the snapshot **read** helpers. | `ingest_result()`, `run_update()`, `write_baseline_snapshot()`, `state_fingerprint()`, `list_runs()`, `get_snapshot()`, `latest_snapshot()`. |
| [`service.py`](../src/forecast/service.py) | A shared, framework-agnostic **read layer** that shapes snapshots into JSON-ready dicts. Both the API and dashboard import it, so they never disagree. | `runs()`, `latest()`, `team_path()`, `pre_vs_now()`, `market_comparison()`, `export_snapshot()`. |
| [`api.py`](../src/forecast/api.py) | A thin **FastAPI** JSON wrapper over `service`. Read-only; one SQLite connection per request. | `app`, endpoints `/health`, `/api/runs`, `/api/snapshot/...`, `/api/team/{id}`, `/api/compare`, `/api/market`, `/api/export/{run_id}`. |
| [`dashboard.py`](../src/forecast/dashboard.py) | The **Streamlit** web UI. Reads `service` directly (no API server needed). | `main()` — tabs for title odds, team path, market comparison. |

## Scripts in `scripts/`

Each script is a thin CLI over the modules above. Run them from the project root, e.g.
`python scripts/run_simulation.py`. Flags shown are the ones each script accepts.

| Script | What it does | Flags |
|--------|-------------|-------|
| `fetch_data.py` | Download raw datasets from upstream (optional — they're committed). | `--source <name>` (default: all) |
| `init_db.py` | Create the SQLite schema. | — |
| `load_data.py` | Load `martj42/results.csv` into the DB. | — |
| `build_ratings.py` | Replay history into point-in-time Elo; print the top-20. | — |
| `fetch_fifa_structure.py` | Fetch + validate the 2026 bracket structure. | — |
| `run_simulation.py` | Fit the model, run the Monte Carlo, write a snapshot, print title odds. | `--sims N`, `--seed S` |
| `build_baseline.py` | Write the reserved pre-tournament baseline snapshot. | `--sims N`, `--seed S` |
| `backtest_match_model.py` | Backtest draw calibration (predicted vs empirical draw rate). | `--cutoff DATE` |
| `evaluate_calibration.py` | Run the full calibration harness; write report + reliability PNG to `reports/`. | `--cutoff DATE`, `--market-weight W`, `--bins N` |
| `tune_blend_weight.py` | Grid-search the fixed blend weight(s) on held-out RPS. | `--cutoff DATE`, `--grid-step S` |
| `update_loop.py` | Ingest a completed result and refresh the forecast. | `--date`, `--home`, `--away`, `--score`, `--reload`, `--sims`, `--seed` |
| `serve_api.py` | Launch the FastAPI server. | `--host`, `--port` |
| `dashboard.py` | Launch the Streamlit dashboard. | `--port` |

For how to use these during the tournament, see [Operations](operations.md).
