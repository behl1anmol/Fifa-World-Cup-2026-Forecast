# Getting Started

This page takes you from a fresh clone to a running forecast and dashboard. No machine
learning knowledge needed — you just run a handful of Python scripts in order.

← Back to the [documentation index](README.md).

## 1. Prerequisites

- **Python 3.10 or newer** (the code uses `X | Y` type syntax and modern typing).
- The ability to create a virtual environment and install packages with `pip`.
- That's it — there is **no database server, no Docker, no cloud account** to set up. The
  whole app runs in one process and stores its data in a single local SQLite file.

## 2. Install

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

What gets installed (see [`requirements.txt`](../requirements.txt)):

- **Numerics & data:** `pandas`, `numpy`, `scipy`, `statsmodels`
- **Plot:** `matplotlib` (only used to save the calibration chart as a PNG)
- **Serving:** `fastapi`, `uvicorn`, `streamlit`
- **HTTP:** `requests` (only used when refreshing raw data from the internet)
- **Optional extra model:** `lightgbm`, `scikit-learn` — the app works fine without these
  (it degrades gracefully); see [Concepts](concepts.md#7-an-optional-third-opinion-lightgbm).
- **Tests:** `pytest`, `httpx`

## 3. The data is already in the repo

The raw datasets are **committed** under [`datasets/`](../datasets/), so you do **not**
need internet access or any API key to get started. (Refreshing them from upstream is
optional and covered in [Operations](operations.md).)

## 4. First run: produce a forecast

Run these commands from the project root, in order. Each one is safe to re-run.

```bash
# 1. Create the (empty) SQLite database and its tables.
python scripts/init_db.py

# 2. Load ~50 years of international match results into the database.
python scripts/load_data.py

# 3. Replay all of history to compute each team's Elo strength rating.
python scripts/build_ratings.py

# 4. Run 50,000 tournament simulations and save the first forecast snapshot.
python scripts/run_simulation.py

# 5. (Recommended) Save a "pre-tournament" baseline for the pre-vs-now comparison.
python scripts/build_baseline.py
```

### What each step does and what you'll see

| Step | Script | What happens | What it prints |
|------|--------|--------------|----------------|
| 1 | `init_db.py` | Creates `forecast.db` with 4 tables (`teams`, `matches`, `ratings_history`, `predictions`). | Confirmation of the tables created. |
| 2 | `load_data.py` | Reads `datasets/martj42/results.csv` into the `teams` and `matches` tables. **Idempotent** — re-running gives the same counts. | Row counts for teams and matches. |
| 3 | `build_ratings.py` | Walks every played match in date order and computes point-in-time [Elo](concepts.md#2-rating-team-strength-elo). | The current **top-20 strongest teams**, next to a reference column from eloratings.net. |
| 4 | `run_simulation.py` | Fits the [match model](concepts.md#3-turning-strength-into-a-match-forecast-poisson-and-dixon-coles) and runs the [Monte Carlo](concepts.md#6-playing-the-tournament-50000-times-monte-carlo) simulator. | Ranked **title odds** for all 48 teams. |
| 5 | `build_baseline.py` | Re-simulates as if the tournament hadn't started, saved under the reserved id `pretournament`. | The baseline title odds. |

> **Tip:** `run_simulation.py` accepts `--sims N` and `--seed S`, e.g.
> `python scripts/run_simulation.py --sims 10000 --seed 7` for a faster, different-seed run.
> With the **same** seed you get **identical** numbers every time (see
> [reproducibility](how-it-works.md#reproducibility-and-the-run_id)).

## 5. View the forecast

Two interchangeable front-ends read the same saved forecast:

**Interactive dashboard (recommended for browsing):**

```bash
python scripts/dashboard.py            # opens http://127.0.0.1:8501
```

You'll see ranked title odds, a per-team "path to the final" chart, a *pre-tournament vs
now* toggle, and a model-vs-market comparison tab.

**JSON API (for programmatic access):**

```bash
python scripts/serve_api.py            # serves http://127.0.0.1:8000
```

Then open `http://127.0.0.1:8000/docs` for interactive API documentation, or hit an
endpoint directly, e.g. `http://127.0.0.1:8000/api/snapshot/latest`.

Both front-ends are covered in detail in [Operations](operations.md).

## 6. Where things live

| Thing | Location | Notes |
|-------|----------|-------|
| The database | `forecast.db` in the project root | **Git-ignored** and fully rebuildable from `datasets/`. Delete it any time and re-run steps 1–4. |
| Override the DB path | env var `FORECAST_DB` | e.g. `export FORECAST_DB=/tmp/test.db`. |
| Generated reports | `reports/` | Git-ignored; the calibration chart/report land here. |
| Source code | `src/forecast/` | See the [Code Reference](code-reference.md). |
| CLI scripts | `scripts/` | Thin wrappers around the source modules. |

## 7. Run the tests (optional sanity check)

```bash
pytest -q
```

The suite is fully **offline** (it uses a tiny in-repo CSV and an in-memory database), so
it needs no network and never touches your `forecast.db`. See [Testing](testing.md).

## 8. Troubleshooting

| Symptom | Likely cause & fix |
|---------|--------------------|
| `... results.csv not found` | You skipped `load_data.py`, or the datasets weren't checked out. Run `python scripts/fetch_data.py` to re-download, then `load_data.py`. |
| `WC2026 teams missing current_elo (run build_ratings)` | You ran the simulator before building ratings. Run `python scripts/build_ratings.py` first. |
| Dashboard says "No forecast snapshot yet" | Run `python scripts/run_simulation.py` (and `build_baseline.py` for the pre-vs-now toggle). |
| Market tab shows a **SAMPLE** warning | That's expected offline — it's using the committed illustrative odds. To use real odds, set `ODDS_API_KEY` (see [Operations](operations.md#live-market-odds-optional)). |
| Simulation feels slow | Lower the count: `python scripts/run_simulation.py --sims 10000`. 50k is the default for stable numbers. |

Next: understand the ideas behind the numbers in [Concepts Explained](concepts.md).
