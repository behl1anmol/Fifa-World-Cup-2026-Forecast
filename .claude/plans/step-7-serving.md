# Step 7 — Serving layer (FastAPI) + Streamlit dashboard + shareable export

## Context

Steps 1–6 produced a persisted, idempotent forecast (snapshots in `predictions`, a live
update loop, a queryable history) but nothing *exposes* it. Step 7 (architecture §4.6,
build item 5) adds the serving tier the audience touches: a **FastAPI** read API, a
**Streamlit** dashboard, and a **shareable snapshot export** — clean and interactive,
no animation.

Decisions taken with the user:
- **True reconstructed pre-tournament baseline** — simulate the bracket from scratch with
  completed results ignored and pre-WC point-in-time Elo, stored once under a reserved
  `run_id`. The faithful "pre" side of the pre-vs-now toggle (§7).
- **Shared service layer, direct reads** — one `service.py` both the API and the dashboard
  import; the dashboard needs no running server.

## What was built

### Simulator baseline knobs — `src/forecast/simulator.py`
`simulate(..., condition_on_results=True, elo_override=None)` — two default-off params.
`condition_on_results=False` forces every WC fixture unplayed; `elo_override` supplies
ratings instead of `teams.current_elo` (missing teams → `ELO_DEFAULT_RATING`). Threaded
through `_load_group_fixtures` / `_load_participants`. Defaults preserve prior behaviour.

### Pre-tournament Elo — `src/forecast/ratings.py`
`pretournament_elos(conn, before_date=WC_START_DATE)` → each team's latest leak-free
`elo_after` from a match dated before 11 June 2026.

### Baseline snapshot — `src/forecast/update_loop.py`
`write_baseline_snapshot(conn, …)` → simulate with results ignored + pre-WC Elo, persist
under `BASELINE_RUN_ID = "pretournament"` (idempotent). `list_runs` now **excludes** the
baseline so it never masquerades as "now".

### Shared service layer — `src/forecast/service.py` (new)
`runs` / `latest` / `snapshot` / `team_path` / `baseline` / `pre_vs_now` /
`market_comparison` / `export_snapshot`, plus `STAGE_ORDER`. Pure reads returning
JSON-ready dicts; reuses the Step 6 helpers + `market.*` + `match_model.predict`.
`market_comparison` covers **all** mappable priced matches (completed + upcoming) so the
comparison is visible even with only the committed SAMPLE odds.

### FastAPI app — `src/forecast/api.py` (new) + `scripts/serve_api.py`
`/health`, `/api/runs`, `/api/snapshot/latest`, `/api/snapshot/{run_id}`,
`/api/team/{id}`, `/api/compare`, `/api/market`, `/api/export/{run_id}`. A `get_conn`
dependency opens one SQLite connection per request (thread-safe under uvicorn).

### Streamlit dashboard — `src/forecast/dashboard.py` (new) + `scripts/dashboard.py`
Tabs: ranked title odds (+ pre-vs-now toggle + JSON export), per-team path to the final,
model-vs-market table. Header shows last-updated timestamp, model version, run count.
Degrades gracefully when a snapshot / baseline / odds file is missing.

### Baseline CLI — `scripts/build_baseline.py` (new)

### Modified
`config.py` (`API_HOST/PORT`, `DASHBOARD_PORT`, `BASELINE_RUN_ID`, `WC_START_DATE`;
`MODEL_VERSION = "0.7.0-step7-serving"`), `requirements.txt` (`httpx`), `README.md`.

### Tests
`tests/conftest.py` gains a shared `build_wc_db` + `served_conn` (in-memory) and
`served_db_path` (file, for threaded TestClient) fixtures. New `test_service.py` and
`test_api.py`; `test_simulator.py` gains `condition_on_results` / `elo_override` cases.

## Acceptance — verified
- `pytest -q` → **97 passed** (77 prior + 20 new).
- Built state (4k sims): live Argentina **25.9%** vs pre-tournament baseline **20.8%** —
  a meaningful pre-vs-now shift.
- API (`serve_api.py`): `/health`, `/api/runs` (baseline excluded), `/api/snapshot/latest`,
  `/api/compare` (per-team Δ: Argentina +0.051, Spain −0.041), `/api/market` (8 SAMPLE
  rows, model vs market + result), `/api/team/{id}`, `/api/export/{run_id}` (download
  headers), 404 on unknown → all correct.
- Dashboard (`AppTest`): renders title, 3 tabs, metrics, tables; the pre-vs-now toggle
  runs without error.

## Notes
- Baseline uses the current leak-free model *params* (negligible drift); its
  pre-tournament character comes from pre-WC Elo + ignoring group results.
- SAMPLE odds cover early *completed* group games, so `market_comparison` deliberately
  includes completed matches (with results) rather than only upcoming ones.
- API tests open a per-request connection on a file DB because TestClient dispatches on a
  worker thread (sqlite connections are single-thread).
