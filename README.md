# FIFA World Cup 2026 — Forecast

A live, all-Python title-odds tracker for the 2026 World Cup. It predicts
per-match outcomes, Monte Carlo–simulates the remaining bracket, and counts how
often each surviving team wins — measured by **calibration**, not by naming a
champion. See [`docs/architecture-overview.md`](docs/architecture-overview.md)
for the full design (the source of truth).

The project is built in steps following the architecture's §8 build sequence.

## Build status

- ✅ **Step 1 — Project scaffold & data layer**
- ✅ **Step 2 — Elo engine (point-in-time)**
- ✅ **Step 3 — Monte Carlo simulator (the spine)**
- ✅ **Step 4 — Scoreline model & blend**
- ✅ **Step 5 — Calibration harness**
- ✅ **Step 6 — Update loop & snapshots**
- ✅ **Step 7 — API & dashboard** *(this step)*

## Layout

```
datasets/        raw data, one subfolder per source (never interlinked)
  martj42/       international results 1872–present (incl. WC2026 fixtures)
  eloratings/    World Football Elo (feature / sanity check)
  odds_api/      The Odds API (needs key); committed *.sample.json for offline use
  transfermarkt/ deferred (optional, scrape) — placeholder
  fifa_2026/     groups + FIFA Annex C third-place table (bracket structure)
src/forecast/    application package (config, db, loader, elo, ratings, tournament,
                 dixon_coles, match_model, simulator, metrics, market, calibration,
                 update_loop, service, api, dashboard)
scripts/         CLIs: fetch_data, init_db, load_data, build_ratings,
                 fetch_fifa_structure, run_simulation, backtest_match_model,
                 evaluate_calibration, update_loop, build_baseline, serve_api, dashboard
reports/         generated calibration artifacts (git-ignored, regenerable)
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

## Ratings engine (Step 2)

A custom World-Football-Elo engine (architecture §4.2) replays every historical
match in date order to produce **point-in-time** ratings: the rating applied to a
match depends only on matches strictly before it (the leakage guard). It stores
`elo_before`/`elo_after` per team per match in `ratings_history` and updates
`teams.current_elo`. The replay is deterministic and rebuilds from scratch on each
run.

```bash
# Replay history into Elo and print the current top-20 (vs eloratings.net).
python scripts/build_ratings.py                 # deterministic: re-run = same table
```

The tunables — `ELO_K`, `ELO_HOME_ADVANTAGE`, `ELO_USE_MOV`, `ELO_DEFAULT_RATING`
— live in `src/forecast/config.py`. The eloratings.net column is a sanity-check
reference only; a uniform scale offset is expected (we seed every team at 1500 with
a single K), so judge it by ranking, not absolute numbers.

## Simulator (Step 3)

The spine (architecture §4.4): a seeded NumPy Monte Carlo that plays the remaining
2026 bracket 50,000 times — conditioned on completed group results — and reports
each team's probability of reaching every stage and of winning the title. The
Round-of-32 third-placed-team allocation uses FIFA's literal 495-combination table
(Annex C), the app's highest-risk input, validated as a correctness gate.

```bash
# 1. Fetch + validate the FIFA bracket structure into datasets/fifa_2026/.
#    (Committed already; this is the regeneration path — the only network step.)
python scripts/fetch_fifa_structure.py

# 2. Run 50k seeded sims, write a prediction snapshot, print ranked title odds.
python scripts/run_simulation.py                 # same seed => identical numbers
python scripts/run_simulation.py --sims 10000 --seed 7
```

Match outcomes come from the Step 4 blended model (below); extra time continues the
goal process at the proportional rate and a level shootout is decided 50/50.
Simulator tunables `N_SIMS` / `SIM_SEED` live in `config.py`.

## Match model (Step 4)

The simulator resolves fixtures with a **Dixon-Coles scoreline model blended with
the Elo-implied outcome** (architecture §4.3, decision #8). Expected goals follow the
published FIFA-tournament form `λ = exp(β₀ + β₁·EloDiff)` (Gilch & Müller 2018) — so
the self-computed Elo stays the single strength backbone (§4.2) — with a re-fit
Dixon-Coles low-score (τ/ρ) correction and a host-only home advantage layered on top.
The Dixon-Coles win/draw/loss is averaged with the Elo-logistic win/draw/loss at a
**fixed configurable weight** (`BLEND_WEIGHT`, default 0.5; not learned stacking,
which overfits ~10 matches/team/year). Group fixtures sample a full scoreline (for
tiebreakers); knockouts need only the winner.

Parameters are re-estimated from historical internationals using point-in-time Elo
(`ratings_history.elo_before`), so the fit is leak-free. The model tunables
(`BLEND_WEIGHT`, `DC_RHO`, `DC_MAX_GOALS`, `DRAW_BASE`, `DRAW_DECAY`,
`DC_FIT_HALF_LIFE_DAYS`) seed `MatchModelParams.default()` and live in `config.py`.

```bash
# Backtest draw calibration: fit before a cutoff, predict the held-out tail,
# compare predicted vs empirical draw rate (blended / Dixon-Coles / Elo-only).
python scripts/backtest_match_model.py                 # default cutoff 2018-01-01
python scripts/backtest_match_model.py --cutoff 2016-01-01
```

## Calibration harness (Step 5)

The harness measures **calibration, not winner-calling** (decision #2): when the app
says 20%, that should happen ~20% of the time. It scores forecasts with **RPS**
(primary, order-aware for win/draw/loss), **Brier**, and **log-loss** (`metrics.py`),
backtests the model on historical internationals with a strict time-split and
point-in-time Elo (no leakage), and saves a reliability diagram.

It also adds the **market as a reference** (`market.py`): The Odds API h2h prices are
de-vigged (margin removed) and compared three ways — the **odds-free** fundamentals
model, the **market**, and a market-aware **model** that fixed-weight-blends the two
(`MARKET_BLEND_WEIGHT`). The goal is to *match* the market, not beat it (decision #6);
the odds-free row tests whether the fundamentals add signal beyond the price.

```bash
python scripts/evaluate_calibration.py                 # offline: uses [SAMPLE] odds
python scripts/evaluate_calibration.py --cutoff 2016-01-01 --market-weight 0.5
```

Outputs print to the console and are written to `reports/` (`reliability_step5.png`,
`step5_calibration_report.md`).

**Live market odds.** The Odds API needs a key. Set it as an environment variable
(never commit it), then fetch:

```bash
export ODDS_API_KEY=your_key            # or set it in your environment config
python scripts/fetch_data.py --source odds_api   # writes datasets/odds_api/wc2026_h2h_odds.json
python scripts/evaluate_calibration.py           # now uses the live odds
```

The free tier serves only *upcoming* odds, so scored market calibration accrues as
fixtures complete with odds captured beforehand; the committed
`wc2026_h2h_odds.sample.json` lets the harness run offline in the meantime.

## Update loop & snapshots (Step 6)

The operational loop the app runs during the tournament (`update_loop.py`). As each
WC2026 match finishes it: **ingests** the result (flips an existing fixture from `NULL`
to a `"h:a"` score, in place), **rebuilds** point-in-time Elo, **refits** the blended
match model, **re-simulates** the remaining bracket, and writes **one prediction
snapshot** to the `predictions` table (architecture §3.3, §4, §5).

```bash
# Ingest a completed result, then refresh the forecast:
python scripts/update_loop.py --date 2026-06-17 --home "Portugal" --away "DR Congo" --score 3:0

# Re-sync the martj42 CSV (pick up newly filled scores), then refresh:
python scripts/update_loop.py --reload

# Just re-run on the current DB state:
python scripts/update_loop.py [--sims N --seed S]
```

The output table adds a **Δtitle** column versus the previous snapshot, so the move is
visible: a winner's title odds rise, a loser's drop toward zero.

**Idempotent & seeded** (§7). The snapshot `run_id` is a deterministic fingerprint of
the tournament state (completed WC2026 results + seed + sims + model version), so
re-running on the same state overwrites the same snapshot (one history entry), while a
newly completed result yields a new `run_id` and a new entry. Snapshots are queryable as
a history via `list_runs`, `get_snapshot`, and `latest_snapshot` — the read surface the
Step 7 API and dashboard consume.

## Serving layer & dashboard (Step 7)

A shared, read-only **service layer** (`service.py`) turns the persisted forecast into
JSON-ready dicts; both the API and the dashboard import it directly, so they never drift
and the dashboard needs no running server.

**FastAPI** (`api.py`, run via `scripts/serve_api.py`) exposes read endpoints:
`/api/snapshot/latest`, `/api/snapshot/{run_id}`, `/api/runs`, `/api/team/{id}`
(a team's stage path), `/api/compare` (pre-tournament vs now), `/api/market`, and
`/api/export/{run_id}` (shareable JSON download). Each request opens its own SQLite
connection; interactive docs at `/docs`.

**Streamlit dashboard** (`dashboard.py`, run via `scripts/dashboard.py`): ranked live
title odds, a per-team path to the final, a **pre-tournament vs now** toggle, a
last-updated timestamp + model version, a **model-vs-market** comparison, and a
shareable snapshot export. Clean and interactive, no animation (§4.6).

The **pre-tournament baseline** is a reconstructed forecast — group results ignored and
pre-WC point-in-time Elo (`ratings.pretournament_elos`) — written once under the reserved
`run_id = "pretournament"` (`scripts/build_baseline.py`); it is the fixed "pre" side of
the toggle and is excluded from the live snapshot history.

```bash
pip install -r requirements.txt                  # fastapi, uvicorn, streamlit, httpx
python scripts/build_ratings.py                  # load + point-in-time Elo
python scripts/run_simulation.py                 # first live snapshot
python scripts/build_baseline.py                 # pre-tournament baseline
python scripts/serve_api.py                      # API on http://127.0.0.1:8000
python scripts/dashboard.py                       # dashboard on http://127.0.0.1:8501
```

After an update (`python scripts/update_loop.py --date … --home … --away … --score …`),
the API and dashboard reflect the new numbers and last-updated timestamp.

## Tests

```bash
pytest -q
```

The suite is offline (uses a small in-repo fixture CSV and an in-memory DB) and
covers schema creation, loader idempotency, the hand-checked Elo engine
(`test_elo.py`), leak-free replay (`test_ratings.py`), the FIFA R32 third-place
table and bracket gate (`test_tournament.py`), the Dixon-Coles scoreline math and
blended match model (`test_dixon_coles.py`, `test_match_model.py`), the simulator
(`test_simulator.py`), the calibration harness — scoring rules, odds de-vig, and
the three-way comparison (`test_metrics.py`, `test_market.py`, `test_calibration.py`) —
the update loop: in-place ingest, deterministic `run_id`, snapshot history, and
sensible movement on a new result (`test_update_loop.py`), and the serving layer:
the shared service functions (`test_service.py`) and the FastAPI endpoints via
`TestClient` (`test_api.py`).
