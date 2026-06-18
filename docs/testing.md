# Testing

How the automated test suite works, how to run it, and what each test protects.

← Back to the [documentation index](README.md).

## How to run

```bash
pytest -q
```

That's it. The suite is **fully offline**: it builds a tiny in-memory SQLite database from a
small in-repo fixture CSV (set up in `tests/conftest.py`), so it needs **no network**, **no
API key**, and **never touches your `forecast.db`**. It's fast and safe to run anytime.

## Why these tests matter

This is a forecasting app, so "it runs without crashing" isn't enough — the *numbers* have to
be right. The tests fall into three kinds:

1. **Exact-maths tests** — pin down hand-calculated values (e.g. a specific Elo update equals
   `1514.40`), so a maths regression is caught immediately.
2. **Property/behaviour tests** — check things that must always hold (probabilities sum to 1,
   the same seed gives identical results, a win raises a team's title odds).
3. **Correctness gates** — guard the highest-risk pieces, especially the FIFA bracket logic.

## What each test file covers

| Test file | Covers | Notable checks |
|-----------|--------|----------------|
| `test_db.py` | Schema creation | The four tables and their columns exist; creation is idempotent. |
| `test_loader.py` | CSV → database | Loads teams/matches; re-loading is a no-op (idempotent); the `"h:a"` score encoding. |
| `test_elo.py` | The Elo engine | **Hand-checked** expected-score, home-advantage, margin-of-victory, and the exact `1514.40` update; zero-sum symmetry. |
| `test_ratings.py` | Point-in-time replay | The replay is deterministic and **leak-free** (a match's rating depends only on earlier matches); re-running rebuilds identically. |
| `test_tournament.py` | **Bracket correctness gate** | The 495-row FIFA third-place table and Round-of-32 allocation behave correctly. This is the highest-risk component. |
| `test_dixon_coles.py` | Scoreline maths | The τ low-score cells, the effect of ρ, Poisson factorisation, and ρ recovery by `fit_rho`. |
| `test_match_model.py` | The blended model | Fitting, prediction, vectorisation, blend arithmetic, and outcome consistency. |
| `test_gbm_view.py` | Optional LightGBM view | Training, prediction, and the three-way blend (skips/degrades gracefully if `lightgbm` is absent). |
| `test_simulator.py` | The Monte Carlo spine | **Determinism** (same seed → identical numbers), probabilities are valid, monotonic stages, conditioning on completed results, correct stage/title counts. |
| `test_metrics.py` | Scoring rules | RPS / Brier / log-loss formulas and bounds. |
| `test_market.py` | Odds handling | Decimal→implied conversion, de-vigging, JSON parsing, team-name aliasing. |
| `test_calibration.py` | The harness | RPS/Brier output, the time-split, and the reliability curve. |
| `test_update_loop.py` | The live loop | In-place ingest, **deterministic `run_id`**, snapshot history, idempotency, and sensible movement after a new result. |
| `test_service.py` | The read layer | Latest snapshot, run history, team path, and the pre-vs-now comparison. |
| `test_api.py` | The FastAPI endpoints | Health probe plus the `/api/runs` and `/api/snapshot` endpoints via FastAPI's `TestClient`. |
| `test_squad_strength.py` | Optional squad feature | The z-scored Elo nudge, and clean no-op behaviour when disabled or uncached. |
| `test_blend_tuning.py` | Blend-weight tuning | The grid search, weight constraints, and baseline comparison. |

## The calibration baseline

`tests/baselines/step5_calibration.json` stores the reference calibration scores from Step 5.
The optional Step 8 features are gated on **not regressing** against it: the tuned two-view and
three-view blends both score slightly *better* held-out RPS than this baseline (see
[Concepts §5](concepts.md#5-combining-opinions-the-fixed-weight-blend)), which is how the
project proves the extra features don't quietly make the forecast worse.

## A good habit

If you change anything in `src/forecast/`, run `pytest -q` before committing. The exact-maths
and determinism tests will catch most accidental regressions immediately.

Next: quick definitions in the [Glossary & FAQ](glossary.md).
