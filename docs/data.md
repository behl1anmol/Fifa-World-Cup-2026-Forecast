# Data & Datasets

This page explains every piece of data the app uses: where it comes from, what format it's
in, and how it's stored in the database.

← Back to the [documentation index](README.md). Design rationale:
[architecture §5–§6](architecture-overview.md#5-data-model).

## Design principle: one folder per source

All raw data lives under [`datasets/`](../datasets/), with **one subfolder per source**.
Sources are never interlinked or merged on disk — each is fetched, stored, and documented on
its own. Network access happens **only** in [`data_sources.py`](../src/forecast/data_sources.py);
everything else reads local files, so the app (and its tests) run offline and deterministically.

```
datasets/
├── martj42/         international match results 1872–present (the core dataset)
├── eloratings/      reference Elo ratings (sanity check only)
├── fifa_2026/       the 2026 tournament structure (groups + bracket table)
├── odds_api/        bookmaker odds (optional, needs an API key)
└── transfermarkt/   squad-strength values (optional, cached, off by default)
```

## The datasets

### `martj42/` — the core dataset

The backbone of the whole app: every men's international football result since 1872, plus
the 2026 World Cup fixtures.

| File | What it is | Used? |
|------|-----------|-------|
| `results.csv` | One row per match: `date, home_team, away_team, home_score, away_score, tournament, city, country, neutral`. | **Yes** — the only file the loader reads. |
| `goalscorers.csv` | Goal-by-goal detail. | Not used by the core forecast. |
| `shootouts.csv` | Historical penalty-shootout outcomes. | Not used by the core forecast. |
| `former_names.csv` | Historical team-name changes. | Not used by the core forecast. |

- **Source:** the [martj42/international_results](https://github.com/martj42/international_results)
  GitHub repo. **License:** CC0 (public domain).
- **How WC2026 fixtures appear:** future fixtures are rows in `results.csv` with the scores
  left blank (`NA`). The loader stores those as `result = NULL`; the update loop fills them in
  as matches are played (see [the `result` convention](#how-a-score-is-stored)).
- **Provenance** is recorded in `datasets/martj42/SOURCE.md` after each fetch.

### `eloratings/` — reference ratings (sanity check only)

`2026.tsv` and `en.teams.tsv` hold the published ratings from
[eloratings.net](https://www.eloratings.net). The app **does not** forecast with these — it
computes its *own* Elo ([Concepts §2](concepts.md#2-rating-team-strength-elo)). They appear only
as a side-by-side "reference" column when you run `build_ratings.py`, to sanity-check the
ranking. Because the app seeds every team at 1500 with a single K-factor, expect a uniform
scale offset versus eloratings.net — judge by **ranking**, not absolute numbers.

### `fifa_2026/` — the tournament structure

The fixed scaffolding the simulator pours results through (it is *never* randomised).

| File | What it is |
|------|-----------|
| `groups.json` | The 12 groups (A–L) of 4 teams each: `{ "A": ["Mexico", "South Africa", …], … }`. |
| `r32_third_place_combinations.json` | FIFA's **495-row** Annex C lookup table that decides which third-placed teams advance and into which Round-of-32 slot. |

The bracket *topology* (which match feeds which) is hard-coded in
[`tournament.py`](../src/forecast/tournament.py) and validated against this data.
`scripts/fetch_fifa_structure.py` fetches and validates these artifacts.

> **Why the 495-row table matters.** Working out which of the eight best third-placed teams
> goes where is genuinely ambiguous (FIFA's own rules admit many valid matchings), so no
> simple algorithm can reproduce FIFA's published choices. The app therefore loads the literal
> table as data. It's the single highest-risk input — a mistake here would silently corrupt
> every title probability — so it has a dedicated correctness test (see [Testing](testing.md)).

### `odds_api/` — bookmaker odds (optional)

Head-to-head decimal odds from [The Odds API](https://the-odds-api.com).

| File | What it is |
|------|-----------|
| `wc2026_h2h_odds.sample.json` | A **committed, illustrative sample** so the calibration harness and dashboard work offline. Clearly flagged as `[SAMPLE]` in the UI. |
| `wc2026_h2h_odds.json` | The **live** fetch (git-ignored). Created only when you set `ODDS_API_KEY` and run `fetch_data.py --source odds_api`. |

The app **never requires** odds. They serve as a calibration *reference* and, when live odds
are present, an optional input feature. See
[Concepts §9](concepts.md#9-the-betting-market-as-a-yardstick-odds-and-de-vigging) and
[Operations](operations.md#live-market-odds-optional). Setup notes live in
`datasets/odds_api/README.md`.

### `transfermarkt/` — squad strength (optional, off by default)

Squad market-value figures, used as a tiny optional Elo nudge for the 2026 teams.

| File | What it is |
|------|-----------|
| `squad_strength.sample.json` | A committed illustrative cache (`{"teams": {name: value}}`). |
| `squad_strength.json` | A git-ignored live cache, if you choose to create one. |

This feature is **disabled by default** (`SQUAD_STRENGTH_ENABLED = False`) and the code
**never scrapes** — it only reads a cached file. It's also deliberately kept *out* of the
historical backtest (we only have *current* squad values, so applying them to the past would
leak future information). See `datasets/transfermarkt/README.md` and
[Concepts §10](concepts.md#10-two-ideas-that-keep-it-honest).

## The database schema

The app stores everything in a single SQLite file (`forecast.db`), defined in
[`db.py`](../src/forecast/db.py). It is **git-ignored and fully rebuildable** from `datasets/`
— delete it any time and re-run the setup steps. There are exactly four tables (the ER diagram
is in [How It Works](how-it-works.md#the-database-at-the-centre)).

### `teams` — one row per national team

| Column | Type | Meaning |
|--------|------|---------|
| `id` | INTEGER PK | Internal team id. |
| `name` | TEXT, unique | Team name (martj42 convention, e.g. "South Korea"). |
| `confederation` | TEXT | Confederation (may be unset). |
| `current_elo` | REAL | The team's **latest** Elo, set by the ratings replay. |

### `matches` — historical results + 2026 fixtures

| Column | Type | Meaning |
|--------|------|---------|
| `id` | INTEGER PK | Internal match id. |
| `date` | TEXT | ISO date `YYYY-MM-DD`. |
| `stage` | TEXT | The competition/tournament name (e.g. `"FIFA World Cup"`, `"Friendly"`). |
| `home` | INTEGER FK → teams | Home team. |
| `away` | INTEGER FK → teams | Away team. |
| `result` | TEXT or NULL | The scoreline (see below). `NULL` = not yet played. |
| `feature_snapshot` | TEXT (JSON) | Venue context: `{neutral, city, country, tournament}`. |

A uniqueness constraint on `(date, home, away, stage)` makes loading **idempotent** —
re-loading never duplicates a match.

#### How a score is stored

The `result` column holds a compact string `"h:a"` — home goals, a colon, away goals.
Examples: a 2–1 home win is `"2:1"`; a goalless draw is `"0:0"`; an unplayed fixture is
`NULL`. This single convention is used everywhere (the loader writes it, the ratings replay and
the simulator parse it). When a 2026 match finishes, the update loop simply flips that fixture's
`result` from `NULL` to the real `"h:a"`.

### `ratings_history` — point-in-time Elo

The leak-free audit trail of every rating change (see
[Concepts §10](concepts.md#10-two-ideas-that-keep-it-honest)).

| Column | Type | Meaning |
|--------|------|---------|
| `id` | INTEGER PK | Row id. |
| `team_id` | INTEGER FK → teams | The team. |
| `match_id` | INTEGER FK → matches | The match. |
| `elo_before` | REAL | The team's rating **before** this match (what the model is allowed to use). |
| `elo_after` | REAL | The team's rating after this match. |
| `timestamp` | TEXT | The match date. |

Unique on `(team_id, match_id)` — one rating change per team per match.

### `predictions` — forecast snapshots

Each forecast run writes one row **per team**.

| Column | Type | Meaning |
|--------|------|---------|
| `id` | INTEGER PK | Row id. |
| `run_id` | TEXT | The snapshot's fingerprint id (see [run_id](how-it-works.md#reproducibility-and-the-run_id)); `"pretournament"` for the baseline. |
| `model_version` | TEXT | The model version that produced it (e.g. `0.8.0-step8-features`). |
| `timestamp` | TEXT | When it was written (UTC). |
| `team_id` | INTEGER FK → teams | The team. |
| `stage_probabilities` | TEXT (JSON) | `{r32, r16, qf, sf, final, title}` → probability of reaching each. |
| `title_prob` | REAL | The title probability (also duplicated inside the JSON, for convenient sorting). |

Unique on `(run_id, team_id)` — re-running the same state overwrites the same snapshot rather
than duplicating it.

Next: a map of every module and script in [Code Reference](code-reference.md).
