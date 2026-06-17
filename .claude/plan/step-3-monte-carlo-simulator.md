# Step 3 — Monte Carlo Bracket Simulator (the spine)

## Context

Steps 1–2 delivered the data layer (`teams`, `matches`, 49,423 played) and a
leak-free point-in-time Elo backbone (`teams.current_elo`, all 48 WC2026 teams
rated). Step 3 (§8 item 1 "Must", §4.4) is **the spine**: simulate the remaining
2026 bracket tens of thousands of times, conditioned on completed group results,
and emit per-team **stage and title probabilities**. After this step we have a
working forecast.

Per §4.4 the **Round-of-32 third-placed-team assignment is the highest bug-risk
piece in the whole app** — a silent bug there corrupts every title probability. It
must be unit-tested against FIFA's actual 495-combination table (Annex C) before any
output is trusted. This plan treats that as a correctness gate.

### Research already done (authoritative data secured, all read-only)

- **Groups A–L** extracted from Wikipedia (FIFA draw); names match martj42 exactly
  and partition identically to the 12 fixture-derived components (recon confirmed):
  - A: Mexico, South Africa, South Korea, Czech Republic
  - B: Canada, Bosnia and Herzegovina, Switzerland, Qatar
  - C: Brazil, Morocco, Scotland, Haiti
  - D: United States, Paraguay, Australia, Turkey
  - E: Germany, Curaçao, Ivory Coast, Ecuador
  - F: Netherlands, Japan, Sweden, Tunisia
  - G: Belgium, Egypt, Iran, New Zealand
  - H: Cape Verde, Spain, Saudi Arabia, Uruguay
  - I: France, Senegal, Iraq, Norway
  - J: Argentina, Algeria, Austria, Jordan
  - K: Portugal, DR Congo, Uzbekistan, Colombia
  - L: England, Croatia, Ghana, Panama
- **R32 bracket** (each match's slots) and **progression** verified internally
  consistent (12 winners + 12 runners-up + 8 thirds = 32):
  - R32: 73:2A-2B · 74:1E-3rd · 75:1F-2C · 76:1C-2F · 77:1I-3rd · 78:2E-2I ·
    79:1A-3rd · 80:1L-3rd · 81:1D-3rd · 82:1G-3rd · 83:2K-2L · 84:1H-2J ·
    85:1B-3rd · 86:1J-2H · 87:1K-3rd · 88:2D-2G
  - The 8 third-place slots are hosted by winners **A,B,D,E,G,I,K,L** (matches
    79,85,81,74,82,77,87,80). Allowed third-groups per slot (FIFA):
    79:{C,E,F,H,I} 85:{E,F,G,I,J} 81:{B,E,F,I,J} 74:{A,B,C,D,F}
    82:{A,E,H,I,J} 77:{C,D,F,G,H} 87:{D,E,I,J,L} 80:{E,H,I,J,K}
  - R16: 89:W74-W77 · 90:W73-W75 · 91:W76-W78 · 92:W79-W80 · 93:W83-W84 ·
    94:W81-W82 · 95:W86-W88 · 96:W85-W87
  - QF: 97:W89-W90 · 98:W93-W94 · 99:W91-W92 · 100:W95-W96
  - SF: 101:W97-W98 · 102:W99-W100 — Final: 104:W101-W102
- **Third-place 495 table** lives in Wikipedia template
  `Template:2026 FIFA World Cup third-place table` (mirrors FIFA Annex C). A parser
  was validated NOW: **495 rows, 0 bijection/constraint errors, all C(12,8)=495
  subsets covered, row 1 matches FIFA** (`A→3E,B→3J,D→3I,E→3F,G→3H,I→3G,K→3L,L→3K`).
  Columns are keyed by host-winner letter (`1A,1B,1D,1E,1G,1I,1K,1L`); each row's 8
  assignment cells `3X` give that slot's third. **The matching is NOT unique** (every
  combination admits 3–214 valid matchings), so the literal table is required — an
  algorithm cannot reproduce FIFA's choices.

## Design decisions (faithful to the architecture)

- **Goal model = Elo→Poisson (required, not optional).** "Elo-implied W/D/L" alone
  cannot rank a group — FIFA tiebreakers need goal difference / goals-for, and §4.4
  explicitly describes a *Poisson goal process* with extra-time as proportional
  minutes. So Step 3 derives, from the two teams' Elo, a pair of Poisson rates
  `(λ_home, λ_away)`; sampling them yields W/D/L **and** scorelines. This is a small,
  documented placeholder that **Step 4 replaces with Dixon-Coles** (same simulator
  wiring). Mapping: `d = (elo_a - elo_b) * ELO_GOAL_SCALE` (supremacy), total
  `μ = BASE_GOALS`, `λ_a = max(0.2,(μ+d)/2)`, `λ_b = max(0.2,(μ-d)/2)`. `ELO_GOAL_SCALE`
  tuned so the favorite title prob lands ~16–20% (§ acceptance); calibration is
  Step 5's job.
- **Knockout resolution (decision #7, §4.4):** sample 90′ from the Poisson process;
  if level, play 30′ ET at the **proportional rate** (`λ/3` each, i.e. 30/90); if
  still level, decide the winner **50/50**.
- **Neutral venues in Step 3.** All simulated matches treated as neutral; host
  advantage for USA/CAN/MEX (§4.3) is deferred to Step 4. Completed matches are
  conditioned on their actual results, so this only affects unplayed fixtures.
- **Elo held fixed within a sim.** Re-rating after real completed matches is the
  Step 6 update loop, not within-sim. Each sim uses current `teams.current_elo`.
- **Group tiebreakers:** points → goal difference → goals for → seeded random.
  Head-to-head is a documented simplification (noted, not implemented in Step 3).
- **Seeded RNG (§7):** one `numpy.random.default_rng(seed)`; two runs same seed →
  identical numbers.

## Files

### Data artifacts (committed; offline at sim time) — `datasets/fifa_2026/`
- `groups.json` — `{ "A": [4 team names], ... "L": [...] }` (the verified mapping above).
- `r32_third_place_combinations.json` — 495 rows, each
  `{"thirds": ["E","F",...], "slots": {"A":"E","B":"J","D":"I","E":"F","G":"H","I":"G","K":"L","L":"K"}}`
  (slot keys = host-winner letters).
- `SOURCE.md` — URLs (Wikipedia article + third-place template), Annex C citation,
  fetch date, and the validation invariants.

### `scripts/fetch_fifa_structure.py` (new — the only network path)
Fetch `?action=raw` for the main 2026 article + per-group subpages (I–L) → group
rosters; fetch the third-place template → parse 495 rows. **Validate hard** before
writing: 12 groups × 4; rosters partition == fixture-derived components; 495 rows;
every row a bijection respecting the allowed-group sets; all 495 subsets covered;
row 1 == known FIFA values. Writes the two JSON files + `SOURCE.md`. Mirrors Step 1's
`data_sources.py` retry/backoff style.

### `src/forecast/tournament.py` (new — static structure + loaders)
- `R32_MATCHES` (16 match specs: winner/runner-up/third slots), `BRACKET` (R16/QF/SF/
  final pairings by match number), `THIRD_PLACE_SLOTS` (host-winner → match → allowed
  groups) — the verified constants above.
- `load_groups()`, `load_third_place_table()` — read the committed JSON.
- `derive_groups_from_fixtures(conn)` — connected-components over WC2026 group
  fixtures (reused by the consistency test).

### `src/forecast/simulator.py` (new — the Monte Carlo engine)
- `elo_to_lambdas(elo_a, elo_b)` → `(λ_a, λ_b)` (the goal model above).
- `simulate(conn, n_sims, seed, config)`:
  1. Load groups, Elo, third-place table, and the 6 fixtures per group (played →
     fixed scoreline; unplayed → sampled, **vectorized over sims** with numpy).
  2. Group standings per sim → 1st/2nd/3rd + (pts,GD,GF); rank the twelve 3rd-placed
     teams across groups, take **best 8** → per-sim qualifying group-set.
  3. **R32 seeding:** winners/runners-up placed deterministically; thirds placed via
     the 495-table. Clean path: precompute a `mask(12 bits)→slot-assignment` lookup
     once, index per sim (fully vectorized); a 50k Python loop of dict lookups is an
     acceptable fallback.
  4. Knockouts R32→Final: each bracket match resolved **vectorized across sims**
     (gather the two slot teams' λ, sample 90′+ET+50/50, advance winners).
  5. Aggregate per team: P(reach R32/R16/QF/SF/final) and P(title), as counts/n_sims.
- `write_predictions(conn, probs, run_id, model_version)` → `predictions` table (§5):
  one row/team `(run_id, model_version, timestamp, team_id, stage_probabilities JSON,
  title_prob)`.

### `scripts/run_simulation.py` (new — acceptance CLI)
Ensure DB loaded + Elo built; run `simulate(n_sims=N_SIMS, seed=SIM_SEED)`; write a
snapshot; print ranked title odds + a stage-probability table. Mirrors
`scripts/build_ratings.py`.

### `src/forecast/config.py` (edit)
Add `N_SIMS=50000`, `SIM_SEED` (e.g. 20260617), `BASE_GOALS=2.6`,
`ELO_GOAL_SCALE` (tuned); bump `MODEL_VERSION="0.3.0-step3-simulator"`.

### Tests
- **`tests/test_tournament.py` (the correctness gate):** loaded third-place table has
  495 entries; every row a bijection; every assignment respects its slot's allowed
  groups; all C(12,8)=495 subsets covered; **spot-check FIFA row 1** and ≥2 more
  against literal values; bracket structure sanity (32 distinct slots; progression
  references valid match numbers); `groups.json` partition == fixture-derived
  components; all group teams have non-null Elo.
- **`tests/test_simulator.py`:** determinism (same seed → identical probs, small N);
  internal consistency (per sim exactly 32 reach R32 / 16 R16 / 8 QF / 4 SF / 2 final
  / 1 champion; per-team stage probs monotonically non-increasing; Σ title ≈ 1.0);
  favorite plausibility (top title prob within a lenient band, e.g. 0.08–0.35, and the
  top team is a strong side). Uses small N (e.g. 2000) for speed; offline (committed
  JSON + a small seeded DB or the real DB if present).

## Verification (acceptance)
1. `python scripts/fetch_fifa_structure.py` — regenerates committed JSON; validation passes.
2. `pytest tests/test_tournament.py -q` — **R32 correctness gate green** (495-table + groups).
3. `pytest tests/test_simulator.py -q` — determinism + consistency.
4. `pytest -q` — full suite green (Steps 1–2 untouched).
5. `python scripts/run_simulation.py` — prints ranked title odds; **favorite ~16–20%**;
   stage counts consistent; **re-run same seed → identical numbers**.

Then STOP for user verification before Step 4 (Dixon-Coles scoreline + blend).

## Housekeeping
- Sync local branch onto merged `main` before coding (Step 2 PR was merged).
- Copy this plan into the repo at `.claude/plan/step-3-monte-carlo-simulator.md`
  (as requested) during implementation.
- README: add a "Simulator (Step 3)" section; flip build-status to ✅ Step 3.

## Risks / framing
- **R32 third-place table is the gate** — mitigated by sourcing FIFA's literal Annex C
  table (validated: 495/bijection/coverage/spot-check) rather than an algorithm
  (proven insufficient — matching is non-unique).
- **Goal model is a placeholder** — `BASE_GOALS`/`ELO_GOAL_SCALE` are not yet
  calibrated (Step 5); Step 4 swaps in Dixon-Coles. The favorite-band test stays lenient.
- **Performance:** group stage + knockouts vectorized over sims with numpy → 50k in
  seconds (§7). The third-place seeding uses a precomputed mask→assignment lookup.
