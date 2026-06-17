"""Monte Carlo bracket simulator — the spine (architecture §4.4).

Plays the remaining 2026 bracket ``n_sims`` times, conditioned on completed group
results, and reports each team's probability of reaching every stage and of winning
the title. Everything is vectorised over the simulation axis with NumPy and driven
by a single seeded generator, so 50k runs finish in seconds and two runs with the
same seed are identical (§7).

Match model (Step 3 placeholder, replaced by Dixon-Coles in Step 4): each team's
Elo maps to a Poisson scoring rate, so one goal process yields win/draw/loss *and*
scorelines — the latter needed for group tiebreakers. Knockouts level after 90'
play 30' of the same process at the proportional rate (λ/3); still level → 50/50
(decision #7).

The Round-of-32 third-placed-team allocation uses FIFA's literal 495-row table via
``tournament`` — the correctness gate of §4.4.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

import numpy as np

from .config import BASE_GOALS, ELO_GOAL_SCALE, MODEL_VERSION, N_SIMS, SIM_SEED
from .tournament import (
    BRACKET,
    FINAL_MATCH_NO,
    GROUP_LETTERS,
    QF_MATCH_NOS,
    R16_MATCH_NOS,
    R32_MATCHES,
    SF_MATCH_NOS,
    THIRD_SLOT_COLUMN_ORDER,
    load_groups,
    load_third_place_table,
)

# Stage keys, ordered; each is the probability of *reaching* that stage (title = win).
STAGES = ("r32", "r16", "qf", "sf", "final", "title")
_MIN_LAMBDA = 0.05


def elo_to_lambdas(elo_home, elo_away):
    """Map a pair of Elo ratings to Poisson scoring rates ``(λ_home, λ_away)``.

    Accepts scalars or NumPy arrays. The Elo difference becomes a goal supremacy
    that is split around the neutral total ``BASE_GOALS``. Placeholder for Step 4.
    """
    supremacy = (np.asarray(elo_home, float) - np.asarray(elo_away, float)) * ELO_GOAL_SCALE
    lam_home = np.clip((BASE_GOALS + supremacy) / 2.0, _MIN_LAMBDA, None)
    lam_away = np.clip((BASE_GOALS - supremacy) / 2.0, _MIN_LAMBDA, None)
    return lam_home, lam_away


def _play_knockout(elo, home_idx, away_idx, rng):
    """Resolve a knockout match for every sim; return the advancing team indices."""
    n = home_idx.shape[0]
    lam_h, lam_a = elo_to_lambdas(elo[home_idx], elo[away_idx])
    gh, ga = rng.poisson(lam_h), rng.poisson(lam_a)
    home_adv = gh > ga
    tie = gh == ga
    # Extra time: 30 further minutes at the proportional (1/3) rate.
    eh, ea = rng.poisson(lam_h / 3.0), rng.poisson(lam_a / 3.0)
    et_home = eh > ea
    et_tie = eh == ea
    coin = rng.random(n) < 0.5  # penalties: 50/50
    home_advances = home_adv | (tie & et_home) | (tie & et_tie & coin)
    return np.where(home_advances, home_idx, away_idx)


def _build_third_place_lookup():
    """Compile FIFA's 495-row table into NumPy lookups keyed by a 12-bit group mask.

    Returns ``(mask_to_row, src_by_row)`` where ``mask_to_row[mask]`` gives the row
    index for a set of 8 qualifying groups (encoded one bit per group), and
    ``src_by_row[row]`` is the 8 source group indices in ``THIRD_SLOT_COLUMN_ORDER``.
    """
    table = load_third_place_table()
    mask_to_row = np.full(1 << 12, -1, dtype=np.int32)
    src_by_row = np.empty((len(table), 8), dtype=np.int64)
    for row_i, row in enumerate(table):
        mask = 0
        for letter in row["thirds"]:
            mask |= 1 << (ord(letter) - 65)
        mask_to_row[mask] = row_i
        for col, host in enumerate(THIRD_SLOT_COLUMN_ORDER):
            src_by_row[row_i, col] = ord(row["slots"][host]) - 65
    return mask_to_row, src_by_row


def _load_participants(conn: sqlite3.Connection, groups: dict[str, list[str]]):
    """Return (names, team_ids, elos, name_to_idx) for the 48 participants."""
    names = [t for letter in GROUP_LETTERS for t in groups[letter]]
    rows = {
        r["name"]: r
        for r in conn.execute(
            "SELECT id, name, current_elo FROM teams WHERE current_elo IS NOT NULL"
        )
        if r["name"] in set(names)
    }
    missing = [n for n in names if n not in rows]
    if missing:
        raise ValueError(f"WC2026 teams missing current_elo (run build_ratings): {missing}")
    name_to_idx = {n: i for i, n in enumerate(names)}
    team_ids = np.array([rows[n]["id"] for n in names], dtype=np.int64)
    elos = np.array([rows[n]["current_elo"] for n in names], dtype=float)
    return names, team_ids, elos, name_to_idx


def _load_group_fixtures(conn, groups, name_to_idx):
    """Return ``{letter: [(local_a, local_b, result_or_None), ...]}`` (6 each).

    ``local_*`` index into the group's four teams (groups.json order); result is the
    played ``"h:a"`` scoreline or ``None`` for an unplayed fixture to be simulated.
    """
    team_group = {t: letter for letter in GROUP_LETTERS for t in groups[letter]}
    local = {t: i for letter in GROUP_LETTERS for i, t in enumerate(groups[letter])}
    fixtures: dict[str, list] = {letter: [] for letter in GROUP_LETTERS}
    rows = conn.execute(
        """
        SELECT h.name AS home, a.name AS away, m.result AS result
        FROM matches m
        JOIN teams h ON h.id = m.home
        JOIN teams a ON a.id = m.away
        WHERE m.stage = 'FIFA World Cup' AND m.date >= '2026-01-01'
        """
    ).fetchall()
    for r in rows:
        if team_group.get(r["home"]) != team_group.get(r["away"]):
            continue  # not a group fixture (would be a knockout — none exist yet)
        letter = team_group[r["home"]]
        fixtures[letter].append((local[r["home"]], local[r["away"]], r["result"]))
    for letter in GROUP_LETTERS:
        fixtures[letter].sort(key=lambda f: (f[0], f[1]))  # stable RNG-draw order
        if len(fixtures[letter]) != 6:
            raise ValueError(f"Group {letter}: expected 6 fixtures, got {len(fixtures[letter])}")
    return fixtures


def _simulate_group(group_elo, fixtures, n, rng):
    """Simulate one group; return per-sim (winner, runner, third) local indices and
    the third-placed team's cross-group ranking score."""
    points = np.zeros((4, n))
    gd = np.zeros((4, n))
    gf = np.zeros((4, n))
    for a, b, result in fixtures:
        if result is not None:
            hs, as_ = (int(x) for x in result.split(":"))
            ga = np.full(n, hs)
            gb = np.full(n, as_)
        else:
            lam_a, lam_b = elo_to_lambdas(group_elo[a], group_elo[b])
            ga = rng.poisson(lam_a, n)
            gb = rng.poisson(lam_b, n)
        points[a] += 3 * (ga > gb) + (ga == gb)
        points[b] += 3 * (gb > ga) + (ga == gb)
        gf[a] += ga
        gf[b] += gb
        gd[a] += ga - gb
        gd[b] += gb - ga

    # Composite key: points dominate, then GD, then GF, then a random tiebreak.
    score = points * 1e6 + (gd + 100.0) * 1e3 + gf + rng.random((4, n))
    order = np.argsort(-score, axis=0)  # order[0] = winner local idx, etc.
    rows = np.arange(n)
    winner, runner, third = order[0], order[1], order[2]
    tp = points[third, rows]
    tgd = gd[third, rows]
    tgf = gf[third, rows]
    third_score = tp * 1e6 + (tgd + 100.0) * 1e3 + tgf + rng.random(n)
    return winner, runner, third, third_score


def simulate(
    conn: sqlite3.Connection,
    n_sims: int = N_SIMS,
    seed: int = SIM_SEED,
) -> dict:
    """Run the Monte Carlo simulation and return per-team stage/title probabilities.

    Returns ``{"n_sims", "seed", "teams": [{name, team_id, probs:{stage:p}}...]}``
    sorted by title probability descending.
    """
    rng = np.random.default_rng(seed)
    groups = load_groups()
    names, team_ids, elos, name_to_idx = _load_participants(conn, groups)
    fixtures = _load_group_fixtures(conn, groups, name_to_idx)
    n = n_sims
    rows = np.arange(n)

    # Global team index of each group's four teams, in groups.json order.
    group_global = {
        letter: np.array([name_to_idx[t] for t in groups[letter]]) for letter in GROUP_LETTERS
    }

    # --- Group stage ---------------------------------------------------------
    winner_idx, runner_idx, third_idx = {}, {}, {}
    third_scores = np.empty((12, n))
    for gi, letter in enumerate(GROUP_LETTERS):
        gelo = elos[group_global[letter]]
        w, r, t, tscore = _simulate_group(gelo, fixtures[letter], n, rng)
        gg = group_global[letter]
        winner_idx[letter] = gg[w]
        runner_idx[letter] = gg[r]
        third_idx[letter] = gg[t]
        third_scores[gi] = tscore

    # --- Best eight third-placed teams -> R32 slot allocation ---------------
    order = np.argsort(-third_scores, axis=0)  # group indices ranked per sim
    qualifies = np.zeros((12, n), dtype=bool)
    np.put_along_axis(qualifies, order[:8], True, axis=0)
    weights = (1 << np.arange(12)).reshape(12, 1)
    mask = (qualifies * weights).sum(axis=0)  # 12-bit qualifying-group mask per sim

    mask_to_row, src_by_row = _build_third_place_lookup()
    row_of = mask_to_row[mask]
    third_by_group = np.stack([third_idx[l] for l in GROUP_LETTERS])  # (12, n)
    # For each host slot (column order), the advancing third team index per sim.
    slot_third = {}
    for col, host in enumerate(THIRD_SLOT_COLUMN_ORDER):
        src_group = src_by_row[row_of, col]
        slot_third[host] = third_by_group[src_group, rows]

    def resolve(spec):
        kind, key = spec
        if kind == "W":
            return winner_idx[key]
        if kind == "R":
            return runner_idx[key]
        return slot_third[key]  # ("3", host)

    # --- Stage counters ------------------------------------------------------
    counts = {s: np.zeros(48, dtype=np.int64) for s in STAGES}

    # Reach R32 = the 32 qualifiers (12 winners + 12 runners + 8 advancing thirds).
    for letter in GROUP_LETTERS:
        np.add.at(counts["r32"], winner_idx[letter], 1)
        np.add.at(counts["r32"], runner_idx[letter], 1)
    for host in THIRD_SLOT_COLUMN_ORDER:
        np.add.at(counts["r32"], slot_third[host], 1)

    # --- Knockouts -----------------------------------------------------------
    match_winner: dict[int, np.ndarray] = {}
    for match_no, (home_spec, away_spec) in R32_MATCHES.items():
        match_winner[match_no] = _play_knockout(elos, resolve(home_spec), resolve(away_spec), rng)
    for match_no, (src_a, src_b) in BRACKET.items():
        match_winner[match_no] = _play_knockout(
            elos, match_winner[src_a], match_winner[src_b], rng
        )

    # Reaching a stage = winning the previous round's match.
    for mn in R32_MATCHES:  # winners reach R16
        np.add.at(counts["r16"], match_winner[mn], 1)
    for mn in R16_MATCH_NOS:  # winners reach QF
        np.add.at(counts["qf"], match_winner[mn], 1)
    for mn in QF_MATCH_NOS:  # winners reach SF
        np.add.at(counts["sf"], match_winner[mn], 1)
    for mn in SF_MATCH_NOS:  # winners reach final
        np.add.at(counts["final"], match_winner[mn], 1)
    np.add.at(counts["title"], match_winner[FINAL_MATCH_NO], 1)  # champion

    teams = [
        {
            "name": names[i],
            "team_id": int(team_ids[i]),
            "probs": {s: counts[s][i] / n for s in STAGES},
        }
        for i in range(48)
    ]
    teams.sort(key=lambda t: t["probs"]["title"], reverse=True)
    return {"n_sims": n, "seed": seed, "teams": teams}


def write_predictions(
    conn: sqlite3.Connection, result: dict, run_id: str | None = None
) -> str:
    """Persist one prediction snapshot per team (architecture §5). Returns run_id."""
    run_id = run_id or uuid.uuid4().hex
    timestamp = datetime.now(timezone.utc).isoformat()
    rows = []
    for team in result["teams"]:
        stage_probs = {s: team["probs"][s] for s in STAGES}
        rows.append(
            (
                run_id,
                MODEL_VERSION,
                timestamp,
                team["team_id"],
                json.dumps(stage_probs),
                team["probs"]["title"],
            )
        )
    conn.executemany(
        """
        INSERT INTO predictions
            (run_id, model_version, timestamp, team_id, stage_probabilities, title_prob)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (run_id, team_id) DO UPDATE SET
            model_version = excluded.model_version,
            timestamp = excluded.timestamp,
            stage_probabilities = excluded.stage_probabilities,
            title_prob = excluded.title_prob
        """,
        rows,
    )
    conn.commit()
    return run_id
