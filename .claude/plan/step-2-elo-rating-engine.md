# Step 2 — Point-in-Time Elo Rating Engine

## Context

Step 1 built the data layer: SQLite schema (§5) and an idempotent martj42 loader
that filled `teams` (336) and `matches` (~49,475 rows, 49,423 played + 52 unplayed
WC2026 fixtures). The `ratings_history` and `teams.current_elo` columns exist but
are empty.

Step 2 (architecture §4.2, "Must", §8 item 2) adds the **team-strength backbone**:
a custom Elo engine that replays every historical match in date order to produce
**point-in-time** ratings. This is the single hardest correctness constraint in the
whole project after the R32 table — **no leakage**: the rating applied to match *i*
must depend only on matches strictly *before* match *i*. Step 3's simulator and
Step 4's blend both consume these ratings, and Step 5's backtest depends entirely on
them being leak-free, so getting the time-ordering and `elo_before` storage right
now is what makes every later calibration claim trustworthy.

**Outcome:** a deterministic, re-runnable Elo replay that writes `elo_before`/
`elo_after` per team per match into `ratings_history`, updates `teams.current_elo`,
and a sanity print of the current top-20 that looks plausible against eloratings.net.

## Design

Two strictly separated layers (the separation *is* the leakage guard):

- **Pure engine** (`src/forecast/elo.py`) — no DB, no I/O, deterministic float math.
  This is what the hand-checked unit tests exercise.
- **DB replay driver** (`src/forecast/ratings.py`) — walks `matches` in `(date, id)`
  order keeping an in-memory `team_id → rating` dict, reads each match's
  `elo_before` from that dict (which holds only strictly-earlier results) *before*
  computing and writing `elo_after`.

Formula: World Football Elo (eloratings.net family), faithful to §4.2's enumerated
knobs — configurable K, home advantage, optional MOV — and nothing more (single K,
**no** tournament-importance weighting).

## Files to change

### 1. `src/forecast/config.py` (edit)
Add an "Elo model parameters" section and bump the version:
- `ELO_DEFAULT_RATING = 1500.0`, `ELO_K = 40.0`, `ELO_HOME_ADVANTAGE = 100.0`,
  `ELO_USE_MOV = True`
- `MODEL_VERSION = "0.2.0-step2-elo-engine"`

### 2. `src/forecast/elo.py` (new, ~90 lines, pure)
- `EloConfig` — frozen dataclass; defaults read from `config` (`default_rating`,
  `k`, `home_advantage`, `use_mov`).
- `expected_score(rating_home, rating_away, home_advantage, neutral) -> float`:
  `dr = (rating_home + (0 if neutral else home_advantage)) - rating_away`;
  returns `1/(10**(-dr/400)+1)`. The neutral/HA decision lives here (single source).
- `goal_difference_index(goal_diff, use_mov) -> float`: `1.0` if `not use_mov`;
  else `1.0` for `|gd|<=1`, `1.5` for `|gd|==2`, `(11+|gd|)/8` for `|gd|>=3`.
- `update_ratings(rating_home, rating_away, home_score, away_score, neutral, config)
  -> MatchUpdate` where `MatchUpdate(NamedTuple)` = `(home_before, home_after,
  away_before, away_after)`. `W_home ∈ {1,0.5,0}`; away expected = `1 - we_home`
  (zero-sum). No clamping/rounding — purity makes the hand-checked tests exact.

### 3. `src/forecast/ratings.py` (new — DB replay + reference loader)
- `_parse_scoreline("h:a") -> (int, int)`; `_is_neutral(feature_snapshot) -> bool`
  (`json.loads`, default `False`).
- `replay_history(conn, config=None) -> dict`:
  1. `config = config or EloConfig()`.
  2. Idempotent reset in one transaction: `DELETE FROM ratings_history`;
     `UPDATE teams SET current_elo = NULL`.
  3. `SELECT id,date,home,away,result,feature_snapshot FROM matches
     WHERE result IS NOT NULL ORDER BY date, id` (NULL = unplayed skip; order =
     deterministic + leak-free).
  4. Walk a `ratings: dict[int,float]`; `home_before = ratings.get(home_id,
     default_rating)` (the only new-team init point at 1500). Call `update_ratings`,
     write both ratings back, append two `ratings_history` rows
     `(team_id, match_id, elo_before, elo_after, timestamp=match date)`.
  5. One `executemany` insert; then `executemany` `UPDATE teams SET current_elo`
     from the final dict; `commit`.
  6. Return `{"matches_replayed", "teams_rated", "history_rows"}`.
- `load_reference_elo() -> dict[str,float]`: read `en.teams.tsv` (col0 code, col1
  name) + `2026.tsv` (col2 code, col3 Elo) from `ELORATINGS_DIR`; return
  `{name: elo}`. Missing files → `{}` (reference is optional, never fatal).

### 4. `scripts/build_ratings.py` (new — CLI acceptance)
Mirrors `scripts/load_data.py` (shebang, sys.path insert, `main()->int`,
`raise SystemExit(main())`). Ensure matches loaded (call `load(conn)` if empty,
error→`return 1` if martj42 CSV missing), run `replay_history`, then print top-20:
`SELECT name,current_elo FROM teams WHERE current_elo IS NOT NULL ORDER BY
current_elo DESC LIMIT 20`, showing rank, name, our Elo (1 dp), eloratings `ref=`
and `Δ` where the name matches (`ref= -` when unmatched).

### 5. `tests/test_elo.py` (new — hand-checked pure engine)
Explicit `cfg = EloConfig(1500, 40, 100, use_mov=False)`; `pytest.approx(abs=1e-2)`.
- **A (sequence):** M1 A home beats B 1-0 from 1500/1500 → home_after **1514.40**,
  away_after **1485.60** (`expected_score(1500,1500,100,False) ≈ 0.640065`). M2 B home
  (1485.60) draws C(1500) 1-1 → B **1480.77**, C **1504.83**.
- **B (neutral):** neutral 1-0 → `We=0.5`, **1520.00 / 1480.00**; assert differs from
  non-neutral 1514.40; `expected_score(...,neutral=True) ≈ 0.5`.
- **C (MOV):** indices `gd2=1.5, gd4=1.875, gd1=1.0, gd0=1.0`, off→1.0. Full: 3-1
  non-neutral → **1521.60 / 1478.40**; 5-1 → home **1527.00**.
- **D (symmetry):** `home_after-home_before == -(away_after-away_before)`.

### 6. `tests/test_ratings.py` (new — integration; uses `conn` + `fixture_csv`)
Fixture has 5 played + 1 unplayed; Mexico plays twice (continuity probe).
- (a) **leak-free continuity:** per team, rows ordered by `(timestamp,match_id)`;
  first `elo_before == 1500.0`; each later `elo_before == prev elo_after` (exact).
- (b) **unplayed skipped:** no `ratings_history` row for the NA match; total rows
  `== 2*5 == 10`.
- (c) **determinism:** two replays → identical `ratings_history` + `current_elo`.
- (d) **current_elo:** equals each team's last `elo_after`.

### 7. `README.md` (edit)
Add `## Ratings engine (Step 2)`: one paragraph (World-Football-Elo, point-in-time
replay, leakage guard), usage `python scripts/build_ratings.py`, tunables in
`config.py`, tests line; flip the build-status to include Step 2.

## Edge cases
Unplayed (NULL result) skipped via `WHERE`; new team → `dict.get` default 1500;
same-date → `ORDER BY date,id` (no team plays twice/day, so no self-leak); re-run →
DELETE+rebuild in one txn (clean insert, no UNIQUE conflict); missing feature_snapshot
→ neutral defaults False; missing reference TSVs → `{}`, `ref= -`.

## Verification (acceptance)
1. `pytest tests/test_elo.py -q` — hand-checked values pass.
2. `pytest tests/test_ratings.py -q` — continuity / skip / determinism / current_elo.
3. `pytest -q` — full suite green (Step 1 untouched).
4. `python scripts/build_ratings.py` — runs on the ~49k-match DB; top-20 are the usual
   contenders (Spain, Argentina, France, England, Brazil, Portugal, Netherlands cluster).
5. Re-run step 4 — identical top-20 (determinism).

Then STOP for user verification before Step 3 (Monte Carlo simulator).

## Known framing (not bugs)
- **Absolute scale offset vs eloratings.net is expected:** we seed everyone at 1500
  with a single K; they use importance-weighted K and decades of drift. Judge the
  sanity print **ordinally** (ranking/gaps), not on absolute numbers — the printed Δ
  makes the uniform offset visible and explainable.
- **Single K (no importance weighting)** is deliberate per §4.2 — friendlies weigh
  like finals; noisier but faithful and defensible for the MVP.
- Reference name-join is cosmetic; some nations show `ref= -`.
