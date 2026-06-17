# Step 4 — Scoreline model (Dixon-Coles) and fixed-weight blend with Elo

## Context

Steps 1–3 produced a working "spine": data layer, leak-free point-in-time Elo
(`ratings_history.elo_before`), and a seeded NumPy Monte Carlo bracket simulator.
The simulator resolved every match with a **placeholder** Elo→Poisson map
(`simulator.elo_to_lambdas`, using uncalibrated `BASE_GOALS`/`ELO_GOAL_SCALE`).

Step 4 (architecture §4.3, build-sequence item 2, decision #8) replaces that
placeholder with a real **Dixon-Coles scoreline model** that yields a full
scoreline distribution and W/D/L, **blended by a fixed configurable weight** with
the Elo-implied outcome, and wires the blend into the simulator. Extra time
continues the Poisson process; penalties stay 50/50.

### Key design decision (researched)

The Dixon-Coles **strength source is anchored to the Elo backbone**, not a separate
per-team attack/defense MLE. Expected goals follow the published FIFA-tournament
form λ = exp(β₀ + β₁·EloDiff (+ host home term)) (Gilch & Müller 2018, *On Elo based
prediction models for the FIFA Worldcup 2018*, <https://arxiv.org/pdf/1806.01930>);
Dixon-Coles contributes the **re-fit low-score (τ/ρ) correction** and **host-only
home advantage** on top. Rationale: (a) it is the established method for
*international, data-starved* play; per-team attack/defense overfits ~245 sparse
international teams (§1.3); (b) it keeps the self-computed Elo as the **single
strength backbone** (§4.2 — no second competing strength model, no double-counting);
(c) it is fast and cheaply re-fittable, which matters for the Step 6 update loop.

The blend is genuine: a **goal-process** W/D/L (DC) vs a **rating-logistic** W/D/L
(Elo), combined at fixed weight **0.5** (configurable; Step 5 calibration may tune).

## What was built

### New `src/forecast/dixon_coles.py` (pure, vectorized DC scoreline math)
- `tau(x, y, lam, mu, rho)` — 4-cell low-score correction.
- `scoreline_matrix(lam, mu, rho, max_goals)` — normalized scalar scoreline pmf.
- `outcome_probs(lam, mu, rho, max_goals)` — vectorized `(pH, pD, pA)`, exactly
  consistent with `scoreline_matrix`.
- `fit_rho(home_goals, away_goals, lam, mu, weights)` — 1-D bounded MLE for ρ.

### New `src/forecast/match_model.py` (Elo goals + Elo outcome + blend + fit)
- `MatchModelParams` (frozen) with `default()` from config seeds.
- `team_lambdas`, `elo_outcome`, `blend`, `predict`, `scoreline_distribution`.
- `fit_match_model(conn, *, half_life_days, before)` — leak-free fit joining
  `matches` with `ratings_history.elo_before`; time-weighted Poisson GLM
  (statsmodels) for β₀/β₁/home term, then ρ and the draw curve. Falls back to
  `default()` on insufficient data.

### Modified `src/forecast/simulator.py`
- Removed `elo_to_lambdas`; `simulate(..., params=None)` fits once when `params` is
  None (tests inject explicit params). Group fixtures sample a blended-outcome +
  DC-conditional scoreline (`_sample_scoreline`); knockouts use the blended W/D/L,
  ET as `Poisson(λ/3)`, 50/50. Host advantage applied only to host-nation non-neutral
  group games; knockout venues neutral. Single seeded RNG preserved.

### Modified `src/forecast/config.py`
- Added `ELO_GOAL_SCALE_LOG`, `HOST_HOME_GOALS_LOG`, `DC_RHO`, `DC_MAX_GOALS`,
  `DRAW_BASE`, `DRAW_DECAY`, `BLEND_WEIGHT`, `DC_FIT_HALF_LIFE_DAYS`; bumped
  `MODEL_VERSION = "0.4.0-step4-dixoncoles"`. (`ELO_GOAL_SCALE` removed.)

### New `scripts/backtest_match_model.py`, modified `scripts/run_simulation.py`
- Backtest fits before a cutoff and reports predicted vs empirical draw rate for
  blended / DC-only / Elo-only. `run_simulation.py` fits params and prints them.

### Tests
- `tests/test_dixon_coles.py`, `tests/test_match_model.py` (incl. synthetic
  end-to-end fit/draw-calibration), extended `tests/test_simulator.py`.

## Acceptance — verified
- `pytest -q` → **50 passed**.
- `scripts/backtest_match_model.py` (cutoff 2018-01-01, test n=8,127): blended
  predicted draw **0.2235** vs empirical **0.2310** (Δ −0.0075) — close to the
  historical international draw rate. DC-only and Elo-only within the same band.
- `run_simulation.py --sims 20000 --seed 7` run twice → **byte-identical**
  (excluding the random `run_id`); seeded reproducibility holds.

## Notes / follow-ups
- Knockout host advantage is intentionally omitted (random matchups, neutral venues
  assumption) — a documented §4.3-aligned simplification a later step could refine.
- Blend weight default 0.5 is configurable; Step 5's calibration harness can tune it.
