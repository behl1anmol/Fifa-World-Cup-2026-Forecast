# Step 6 — In-tournament update loop & prediction snapshots

## Context

Steps 1–5 delivered a leak-free fundamentals match model (blended Dixon-Coles + Elo),
a seeded Monte Carlo bracket simulator, and a calibration harness. Step 6 (architecture
build item 4, §3.3/§4 update-loop sequence) adds the **operational loop** the app lives
in during the tournament: as each WC2026 match finishes, refresh the forecast and write
a dated **prediction snapshot** so the product can show "pre-tourney vs now", a per-team
history, and a shareable, auditable trail (§5, §7).

The martj42 dataset already holds all 72 group fixtures; played ones carry scores,
future ones (through 27 June) load as `result = NULL`. So "a newly completed match" is
an existing NULL fixture getting a score — this step is **orchestration + idempotency**
over already-tested components, adding no model logic.

Decisions taken with the user:
- **Deterministic, state-fingerprinted `run_id`** — re-running identical state overwrites
  the same snapshot (one history entry); a newly completed result yields a new `run_id`
  and a new entry. Makes "re-running the same state reproduces the same snapshot" literal.
- **CLI supports both** an explicit single-match ingest *and* a `--reload` mode that
  re-syncs the martj42 CSV (picking up newly filled scores), then updates.

## What was built

### New `src/forecast/update_loop.py`
- `ingest_result(conn, date, home, away, home_score, away_score) -> bool` — flips an
  existing `'FIFA World Cup'` fixture from `NULL` to a `"h:a"` score in place (reuses
  `loader._scoreline` / `_team_id_map`). Idempotent; raises `ValueError` on unknown
  teams; returns `False` (no write) when no fixture matches, so a typo can't create a
  phantom match.
- `state_fingerprint` / `compute_run_id` — SHA-256 (first 16 hex) over `MODEL_VERSION`,
  `n_sims`, `seed`, and the sorted completed WC2026 results. Historical data is static,
  so completed WC results are the only moving part of the forecast state.
- `run_update(conn, n_sims, seed) -> dict` — `replay_history` → `fit_match_model` →
  `simulate` → `write_predictions(run_id=deterministic)`. Idempotent end to end.
- Read helpers (also for Step 7): `list_runs` (newest-first), `get_snapshot(run_id)`,
  `latest_snapshot`.

### New `scripts/update_loop.py`
CLI (repo convention: `sys.path` insert, argparse, `main()->int`, `SystemExit`).
Bootstraps like `run_simulation.py`. Explicit `--date/--home/--away/--score`, optional
`--reload`, shared `--sims/--seed`. Prints the `run_id`, snapshot count, and a ranked
title table with a **Δtitle** column versus the previous snapshot. Errors (unknown team,
no matching fixture, incomplete args) exit 1.

### Modified
`src/forecast/config.py` (`MODEL_VERSION = "0.6.0-step6-update-loop"`), `README.md`
(build status, layout, Step 6 section, tests).

### Tests `tests/test_update_loop.py`
In-place ingest (no duplicate), unknown-team raises, unknown-fixture returns False,
deterministic `run_id` + reproducible rows (one run-group), `run_id` varies with seed,
history accumulates / newest-first, **eliminated team → 0 while group winner rises**
(robust monotone case), and the read helpers. Reuses the `conn` fixture and a synthetic
48-team builder that also seeds one played friendly per team so the full Elo replay
assigns every participant a rating.

## Acceptance — verified
- `pytest -q` → **77 passed** (68 prior + 9 new).
- CLI on the real DB (`scripts/update_loop.py`):
  - Ingest `England 2:0 Croatia` → snapshot written, `run_id=58f8…`, history 1.
  - Re-run identical state → **same `run_id`**, history still 1 (idempotent).
  - New result `Ghana 3:0 Panama` → **new `run_id`**, history 2; Panama (loser) title
    prob → 0.
  - `Portugal 3:0 DR Congo` → Portugal **Δtitle +0.81%** (winner rises).
  - `--reload` re-syncs the CSV (49,477 fixtures) then updates.

## Notes
- **Full replay over incremental Elo:** sub-second on ~48k matches, already unit-tested,
  deterministic, and keeps the leakage guard in one place.
- **Refit each update:** new results change `ratings_history.elo_before`, which the
  leak-free fit consumes; consistent with `run_simulation.py`.
- **Group-stage non-monotonicity:** a single win needn't raise a weak team's title prob
  (e.g. Ghana stayed ~0), so the "winner rises" unit test uses controlled elimination.
- Read helpers live in `update_loop.py` for now; Step 7 may relocate them to a
  `snapshots.py` if the serving layer wants a dedicated read module.
