"""Monte Carlo bracket simulator — the spine (architecture §4.4).

Plays the remaining 2026 bracket ``n_sims`` times, conditioned on completed group
results, and reports each team's probability of reaching every stage and of winning
the title. Everything is vectorised over the simulation axis with NumPy and driven
by a single seeded generator, so 50k runs finish in seconds and two runs with the
same seed are identical (§7).

Match model (Step 4): the blended Dixon-Coles + Elo model in ``match_model``. Group
fixtures sample a full scoreline (outcome from the fixed-weight blend, scoreline
texture from the Dixon-Coles conditional) — the scoreline is needed for group
tiebreakers. Knockouts need only the winner: the blend gives win/draw/loss, and a
tie after 90' plays 30' of the same Poisson process at the proportional rate (λ/3),
still level → 50/50 (decision #7). Host home advantage is applied only to host
nations' non-neutral group games (§4.3); knockout venues are treated as neutral.

The Round-of-32 third-placed-team allocation uses FIFA's literal 495-row table via
``tournament`` — the correctness gate of §4.4.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

import numpy as np

from .config import ELO_DEFAULT_RATING, HOST_NATIONS, MODEL_VERSION, N_SIMS, SIM_SEED
from .dixon_coles import outcome_probs
from .match_model import (
    MatchModelParams,
    blend,
    elo_outcome,
    fit_match_model,
    predict,
    scoreline_distribution,
    team_lambdas,
)
from .ratings import _is_neutral
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


def _sample_scoreline(matrix, p_blend, n, rng):
    """Sample ``n`` scorelines: outcome from the blend, scoreline from DC texture.

    ``matrix`` is the Dixon-Coles scoreline pmf for the fixture; ``p_blend`` is the
    fixed-weight-blended ``(pH, pD, pA)``. The outcome (home/draw/away) is drawn from
    the blend, then a scoreline is drawn from ``matrix`` *conditioned* on that
    outcome region — so the W/D/L totals match the blend while goal counts keep the
    Dixon-Coles shape needed for group tiebreakers. Returns ``(home_goals, away_goals)``.
    """
    g = matrix.shape[0]
    cell = np.arange(g * g)
    xs, ys = cell // g, cell % g
    flat = matrix.ravel()

    def region_cdf(mask):
        p = flat * mask
        return np.cumsum(p) / p.sum()

    cdfs = {0: region_cdf(xs > ys), 1: region_cdf(xs == ys), 2: region_cdf(xs < ys)}
    pH, pD, _ = (float(x) for x in p_blend)

    u_out = rng.random(n)
    u_score = rng.random(n)
    outcome = np.where(u_out < pH, 0, np.where(u_out < pH + pD, 1, 2))
    cells = np.empty(n, dtype=np.int64)
    for code, cdf in cdfs.items():
        m = outcome == code
        if m.any():
            cells[m] = np.clip(np.searchsorted(cdf, u_score[m], side="right"), 0, g * g - 1)
    return xs[cells], ys[cells]


def _play_knockout(params, elo, home_idx, away_idx, rng):
    """Resolve a knockout match for every sim; return the advancing team indices.

    Only the winner is needed, so the blended win/draw/loss settles regulation; a tie
    plays 30' of the Dixon-Coles Poisson process at the proportional (1/3) rate, then
    a 50/50 shootout (decision #7). Knockout venues are treated as neutral.
    """
    n = home_idx.shape[0]
    eh_, ea_ = elo[home_idx], elo[away_idx]
    lam_h, lam_a = team_lambdas(params, eh_, ea_, host_home=False)
    p_dc = outcome_probs(lam_h, lam_a, params.rho)
    p_elo = elo_outcome(params, eh_, ea_, host_home=False)
    pH, pD, _ = blend(p_dc, p_elo, params.blend_weight)

    u = rng.random(n)
    home_win = u < pH
    tie = (u >= pH) & (u < pH + pD)
    # Extra time: 30 further minutes at the proportional (1/3) rate.
    et_h, et_a = rng.poisson(lam_h / 3.0), rng.poisson(lam_a / 3.0)
    et_home = et_h > et_a
    et_tie = et_h == et_a
    coin = rng.random(n) < 0.5  # penalties: 50/50
    home_advances = home_win | (tie & et_home) | (tie & et_tie & coin)
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


def _load_participants(
    conn: sqlite3.Connection,
    groups: dict[str, list[str]],
    elo_override: dict[str, float] | None = None,
):
    """Return (names, team_ids, elos, name_to_idx) for the 48 participants.

    ``elo_override`` (Step 7) supplies ratings explicitly — e.g. reconstructed
    pre-tournament Elo for the baseline forecast — instead of ``teams.current_elo``;
    any participant absent from the override falls back to ``ELO_DEFAULT_RATING``. With
    no override the live ``current_elo`` is used and a missing rating is an error
    (run ``build_ratings`` first).
    """
    names = [t for letter in GROUP_LETTERS for t in groups[letter]]
    name_set = set(names)
    rows = {
        r["name"]: r
        for r in conn.execute("SELECT id, name, current_elo FROM teams")
        if r["name"] in name_set
    }
    missing_ids = [n for n in names if n not in rows]
    if missing_ids:
        raise ValueError(f"WC2026 teams not found in DB: {missing_ids}")

    name_to_idx = {n: i for i, n in enumerate(names)}
    team_ids = np.array([rows[n]["id"] for n in names], dtype=np.int64)

    if elo_override is not None:
        elos = np.array(
            [float(elo_override.get(n, ELO_DEFAULT_RATING)) for n in names], dtype=float
        )
    else:
        missing_elo = [n for n in names if rows[n]["current_elo"] is None]
        if missing_elo:
            raise ValueError(
                f"WC2026 teams missing current_elo (run build_ratings): {missing_elo}"
            )
        elos = np.array([rows[n]["current_elo"] for n in names], dtype=float)
    return names, team_ids, elos, name_to_idx


def _load_group_fixtures(conn, groups, name_to_idx, condition_on_results=True):
    """Return ``{letter: [(local_a, local_b, result_or_None, host_home), ...]}``.

    ``local_*`` index into the group's four teams (groups.json order); result is the
    played ``"h:a"`` scoreline or ``None`` for an unplayed fixture to be simulated;
    ``host_home`` is True when the home team is a host nation playing a non-neutral
    game — the only place genuine home advantage applies (§4.3). Six per group.

    ``condition_on_results=False`` (Step 7) forces every fixture to ``None`` so the
    whole group stage is simulated from scratch — used for the pre-tournament baseline.
    """
    team_group = {t: letter for letter in GROUP_LETTERS for t in groups[letter]}
    local = {t: i for letter in GROUP_LETTERS for i, t in enumerate(groups[letter])}
    hosts = set(HOST_NATIONS)
    fixtures: dict[str, list] = {letter: [] for letter in GROUP_LETTERS}
    rows = conn.execute(
        """
        SELECT h.name AS home, a.name AS away, m.result AS result,
               m.feature_snapshot AS fs
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
        host_home = r["home"] in hosts and not _is_neutral(r["fs"])
        result = r["result"] if condition_on_results else None
        fixtures[letter].append(
            (local[r["home"]], local[r["away"]], result, host_home)
        )
    for letter in GROUP_LETTERS:
        fixtures[letter].sort(key=lambda f: (f[0], f[1]))  # stable RNG-draw order
        if len(fixtures[letter]) != 6:
            raise ValueError(f"Group {letter}: expected 6 fixtures, got {len(fixtures[letter])}")
    return fixtures


def _simulate_group(params, group_elo, fixtures, n, rng):
    """Simulate one group; return per-sim (winner, runner, third) local indices and
    the third-placed team's cross-group ranking score."""
    points = np.zeros((4, n))
    gd = np.zeros((4, n))
    gf = np.zeros((4, n))
    for a, b, result, host_home in fixtures:
        if result is not None:
            hs, as_ = (int(x) for x in result.split(":"))
            ga = np.full(n, hs)
            gb = np.full(n, as_)
        else:
            matrix = scoreline_distribution(params, group_elo[a], group_elo[b], host_home)
            p_blend = predict(params, group_elo[a], group_elo[b], host_home)
            ga, gb = _sample_scoreline(matrix, p_blend, n, rng)
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
    params: MatchModelParams | None = None,
    condition_on_results: bool = True,
    elo_override: dict[str, float] | None = None,
) -> dict:
    """Run the Monte Carlo simulation and return per-team stage/title probabilities.

    ``params`` is the blended match model (architecture §4.3). When ``None`` it is
    fit once from the database (leak-free, point-in-time Elo); callers may inject an
    explicit ``MatchModelParams`` to avoid a fit (the unit tests do this).

    Step 7 baseline knobs (both default to today's behaviour):
    ``condition_on_results=False`` simulates the whole group stage from scratch, ignoring
    completed 2026 results; ``elo_override`` supplies ratings (e.g. reconstructed
    pre-tournament Elo) instead of ``teams.current_elo``. Together they produce the
    pre-tournament baseline forecast.

    Returns ``{"n_sims", "seed", "teams": [{name, team_id, probs:{stage:p}}...]}``
    sorted by title probability descending.
    """
    if params is None:
        params = fit_match_model(conn)
    rng = np.random.default_rng(seed)
    groups = load_groups()
    names, team_ids, elos, name_to_idx = _load_participants(conn, groups, elo_override)
    fixtures = _load_group_fixtures(conn, groups, name_to_idx, condition_on_results)
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
        w, r, t, tscore = _simulate_group(params, gelo, fixtures[letter], n, rng)
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
        match_winner[match_no] = _play_knockout(
            params, elos, resolve(home_spec), resolve(away_spec), rng
        )
    for match_no, (src_a, src_b) in BRACKET.items():
        match_winner[match_no] = _play_knockout(
            params, elos, match_winner[src_a], match_winner[src_b], rng
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
