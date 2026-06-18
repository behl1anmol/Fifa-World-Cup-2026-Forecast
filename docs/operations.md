# Operations — running it live & configuration

This page covers running the app during the tournament: feeding in results, serving the
API/dashboard, the optional features, and every configuration knob.

← Back to the [documentation index](README.md). If you haven't produced a first forecast yet,
start with [Getting Started](getting-started.md).

## During the tournament: the update loop

The app is "self-evolving" in one specific sense: after each completed 2026 match you feed in
the result and the whole forecast refreshes. This is one command,
[`scripts/update_loop.py`](../scripts/update_loop.py):

```bash
# A match finished — ingest the result and refresh the forecast.
python scripts/update_loop.py --date 2026-06-17 --home "Portugal" --away "DR Congo" --score 3:0
```

Under the hood this runs the full chain (see the
[sequence diagram](how-it-works.md#the-live-update-loop)): it flips that fixture's `result`
from `NULL` to `"3:0"`, rebuilds point-in-time Elo, refits the match model, re-simulates the
remaining bracket, and writes a new snapshot. The printed table includes a **Δtitle** column
versus the previous snapshot, so you can see the odds move.

Two other modes:

```bash
# Re-sync the martj42 CSV first (picks up scores the data provider has filled in), then refresh.
python scripts/update_loop.py --reload

# Just re-run on whatever is currently in the database (e.g. after changing a config knob).
python scripts/update_loop.py [--sims N --seed S]
```

Notes:

- **Team names must match exactly** the martj42 convention in the database (e.g. "South
  Korea", "United States"). An unknown name is rejected rather than silently creating a
  phantom match.
- **It's idempotent.** Ingesting the same result twice is a no-op, and re-running on an
  unchanged state overwrites the same snapshot (same `run_id`) instead of duplicating it. See
  [run_id](how-it-works.md#reproducibility-and-the-run_id).
- If `--reload` and an explicit `--date/--home/--away/--score` are combined, the reload happens
  first so your explicit result always wins.

## Serving the forecast

Both front-ends read the **same** saved snapshots through the shared
[`service.py`](../src/forecast/service.py) layer, so they can never disagree, and the dashboard
needs no running API server.

### Streamlit dashboard

```bash
python scripts/dashboard.py                 # http://127.0.0.1:8501
python scripts/dashboard.py --port 8600     # custom port
# or directly:
streamlit run src/forecast/dashboard.py
```

Three tabs:

- **Title odds** — ranked title probabilities, with a "pre-tournament vs now" toggle and a
  one-click JSON export of the current snapshot.
- **Team path** — pick a team, see its probability of reaching each stage (R32 → … → Champion),
  with a pre-vs-now comparison.
- **Market comparison** — the model's vs the market's home-win probability per priced fixture,
  and the model's "bullishness" (model − market). Flags the `[SAMPLE]` odds when offline.

The page degrades gracefully: if there's no snapshot, no baseline, or no odds, it tells you
which script to run.

### FastAPI JSON API

```bash
python scripts/serve_api.py                 # http://127.0.0.1:8000
python scripts/serve_api.py --host 0.0.0.0 --port 8080
```

Interactive docs (Swagger UI) are at `/docs`. All endpoints are **read-only**:

| Endpoint | Returns |
|----------|---------|
| `GET /health` | Liveness probe + model version. |
| `GET /api/runs` | Snapshot history, newest first (baseline excluded). |
| `GET /api/snapshot/latest` | The latest snapshot: ranked title/stage probabilities. |
| `GET /api/snapshot/{run_id}` | A specific snapshot. |
| `GET /api/team/{team_id}` | One team's stage path (optionally `?run_id=...`). |
| `GET /api/compare` | Per-team pre-tournament-vs-now title comparison. |
| `GET /api/market` | Model-vs-market home-win probabilities for priced matches. |
| `GET /api/export/{run_id}` | A downloadable, self-contained snapshot JSON. |

## The pre-tournament baseline

To show how odds have *moved* since kickoff, the app keeps a fixed reference snapshot —
a re-simulation as if no group games had been played, under the reserved id `"pretournament"`:

```bash
python scripts/build_baseline.py
```

Run this once before the tournament (or to reset the reference). It's the fixed "pre" side of
the dashboard toggle and is excluded from the live snapshot history. Details:
[How It Works](how-it-works.md#the-pre-tournament-baseline).

## Checking the forecast is trustworthy (calibration)

You don't need this to run the app, but it's how you verify the model is well-calibrated:

```bash
python scripts/evaluate_calibration.py
python scripts/evaluate_calibration.py --cutoff 2016-01-01 --market-weight 0.5
```

This prints RPS/Brier/log-loss for the model, the market, and an odds-free baseline, and writes
`reports/reliability_step5.png` (the reliability diagram) and a markdown report. See
[Concepts §8](concepts.md#8-judging-the-forecast-calibration-and-scoring-rules).

## Optional features (Step 8)

These are "could-have" extras. **The core forecast never depends on them** — with the defaults
below, the live forecast is byte-identical to a build without them.

### Live market odds (optional)

By default the calibration harness uses the committed `[SAMPLE]` odds, and the **live**
forecast ignores odds entirely. To make the live forecast market-aware:

```bash
export ODDS_API_KEY=your_key                      # never commit this
python scripts/fetch_data.py --source odds_api    # writes datasets/odds_api/wc2026_h2h_odds.json
python scripts/update_loop.py                      # now blends de-vigged odds into priced group fixtures
```

The de-vigged market probabilities are blended into the simulator for **upcoming, priced group
fixtures** only (weight `MARKET_BLEND_WEIGHT`), as an *input* — never a "beat-the-market"
target ([decision #6](architecture-overview.md#2-locked-decisions)). The committed sample is
**never** used by the live forecast, only by the offline calibration harness. The Odds API free
tier serves only upcoming odds, so scored market calibration accrues over the tournament.

### Squad strength (optional, off by default)

A small Elo nudge from cached squad-value data, applied to 2026 teams only. It's disabled by
default (`SQUAD_STRENGTH_ENABLED = False`) and the code never scrapes — it reads a cached file.
To experiment, enable it in `config.py` and place a cache at
`datasets/transfermarkt/squad_strength.json` (schema `{"teams": {name: value}}`). It's kept out
of the historical backtest by design (see [Concepts §10](concepts.md#10-two-ideas-that-keep-it-honest)).

### LightGBM third view (optional)

If `lightgbm` is installed (it's in `requirements.txt`), the match model can blend in a third
machine-learning view automatically; if it's absent, the app falls back to the two-view blend
with no change required. To re-tune the fixed blend weights on held-out data:

```bash
python scripts/tune_blend_weight.py
```

This grid-searches and *prints* the best weights; the chosen constants are committed in
`config.py` (`BLEND_WEIGHT`, `BLEND_WEIGHTS_3`). Tuning stays offline so the live path never
re-fits weights (preserving reproducibility).

## Configuration reference

All knobs live in [`config.py`](../src/forecast/config.py). The most useful ones:

### Paths & bookkeeping

| Constant | Default | Meaning |
|----------|---------|---------|
| `DB_PATH` | `forecast.db` (or `$FORECAST_DB`) | SQLite database location. |
| `MODEL_VERSION` | `"0.8.0-step8-features"` | Stamped onto every snapshot. |
| `REPORTS_DIR` | `reports/` | Where calibration artifacts are written. |

### Simulator

| Constant | Default | Meaning |
|----------|---------|---------|
| `N_SIMS` | `50000` | Simulations per run. More = smoother numbers, slower. |
| `SIM_SEED` | `20260617` | RNG seed — fixed for reproducibility. |

### Match model

| Constant | Default | Meaning |
|----------|---------|---------|
| `BASE_GOALS` | `2.6` | Expected total goals in an even, neutral game. |
| `ELO_GOAL_SCALE_LOG` | `0.0017` | How much an Elo edge becomes a scoring edge (β₁ seed). |
| `HOST_HOME_GOALS_LOG` | `0.20` | Host-nation home goal bonus (seed). |
| `DC_RHO` | `-0.05` | Dixon-Coles low-score correction (seed; re-fit). |
| `DC_MAX_GOALS` | `10` | Size of the scoreline grid. |
| `DRAW_BASE` | `0.27` | Draw chance for an even match (seed). |
| `DRAW_DECAY` | `350.0` | How fast draws fade as the Elo gap grows (seed). |
| `BLEND_WEIGHT` | `0.6` | Weight on the Dixon-Coles view (rest goes to Elo). |
| `BLEND_WEIGHTS_3` | `(0.6, 0.2, 0.2)` | Weights for (Dixon-Coles, Elo, LightGBM) when the GBM view is present. |
| `DC_FIT_HALF_LIFE_DAYS` | `~8 years` | Time-decay half-life for weighting historical matches. |

### Elo engine

| Constant | Default | Meaning |
|----------|---------|---------|
| `ELO_DEFAULT_RATING` | `1500.0` | Every team's starting rating. |
| `ELO_K` | `40.0` | Update step size. |
| `ELO_HOME_ADVANTAGE` | `100.0` | Rating bonus for a non-neutral home side. |
| `ELO_USE_MOV` | `True` | Scale updates by margin of victory. |
| `HOST_NATIONS` | USA, Canada, Mexico | The only teams given home advantage in 2026. |

### Calibration & optional features

| Constant | Default | Meaning |
|----------|---------|---------|
| `CALIBRATION_CUTOFF` | `"2018-01-01"` | Time-split: fit before, test after. |
| `RELIABILITY_BINS` | `10` | Bins in the reliability diagram. |
| `MARKET_BLEND_WEIGHT` | `0.5` | Weight on the market when blending it in. |
| `SQUAD_STRENGTH_ENABLED` | `False` | Master switch for the squad-strength feature. |
| `SQUAD_STRENGTH_ELO_SCALE` | `25.0` | Elo points applied at +1σ of squad strength. |

### Serving

| Constant | Default | Meaning |
|----------|---------|---------|
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | FastAPI bind address. |
| `DASHBOARD_PORT` | `8501` | Streamlit port. |
| `BASELINE_RUN_ID` | `"pretournament"` | Reserved id for the baseline snapshot. |
| `WC_START_DATE` | `"2026-06-11"` | Boundary for reconstructing pre-tournament Elo. |

Next: how the automated tests protect all this in [Testing](testing.md).
