"""Tests for the Monte Carlo simulator (architecture §4.4 acceptance).

Uses a self-contained synthetic database (48 WC teams + round-robin group fixtures)
so the suite stays offline. Covers determinism, internal consistency of the stage
counts, monotonicity, and conditioning on completed results.
"""
from __future__ import annotations

from itertools import combinations

from forecast.simulator import STAGES, simulate, write_predictions
from forecast.tournament import GROUP_LETTERS, load_groups


def _build_wc_db(conn, results=None):
    """Populate an empty schema with the 48 participants and 72 group fixtures.

    ``results`` optionally maps (home_name, away_name) -> "h:a" for played games;
    all other fixtures are left unplayed (NULL) to be simulated. Elo descends with
    team order so there is a clear favourite.
    """
    results = results or {}
    groups = load_groups()
    elo = 2200.0
    for letter in GROUP_LETTERS:
        for team in groups[letter]:
            conn.execute(
                "INSERT OR IGNORE INTO teams (name, current_elo) VALUES (?, ?)",
                (team, elo),
            )
            elo -= 12.0
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}
    for letter in GROUP_LETTERS:
        for a, b in combinations(groups[letter], 2):
            conn.execute(
                "INSERT INTO matches (date, stage, home, away, result) VALUES "
                "('2026-06-20', 'FIFA World Cup', ?, ?, ?)",
                (ids[a], ids[b], results.get((a, b))),
            )
    conn.commit()
    return groups


def test_simulation_is_deterministic(conn):
    _build_wc_db(conn)
    a = simulate(conn, n_sims=1500, seed=42)
    b = simulate(conn, n_sims=1500, seed=42)
    pa = {t["name"]: t["probs"] for t in a["teams"]}
    pb = {t["name"]: t["probs"] for t in b["teams"]}
    assert pa == pb


def test_stage_counts_match_the_format(conn):
    _build_wc_db(conn)
    result = simulate(conn, n_sims=2000, seed=7)
    totals = {s: sum(t["probs"][s] for t in result["teams"]) for s in STAGES}
    # Exactly 32 reach R32, 16 R16, 8 QF, 4 SF, 2 final, 1 champion (per sim).
    for stage, expected in [("r32", 32), ("r16", 16), ("qf", 8), ("sf", 4),
                            ("final", 2), ("title", 1)]:
        assert abs(totals[stage] - expected) < 1e-9, stage
    assert len(result["teams"]) == 48


def test_per_team_probabilities_are_monotonic(conn):
    _build_wc_db(conn)
    result = simulate(conn, n_sims=2000, seed=7)
    for team in result["teams"]:
        p = [team["probs"][s] for s in STAGES]
        assert all(p[i] >= p[i + 1] - 1e-12 for i in range(len(p) - 1)), team["name"]
        assert 0.0 <= p[-1] <= 1.0


def test_favourite_is_plausible_and_leads(conn):
    _build_wc_db(conn)
    result = simulate(conn, n_sims=3000, seed=7)
    top = result["teams"][0]
    # Highest-Elo team should be the strongest title contender, within a sane band.
    assert top["name"] == "Mexico"  # first team inserted -> highest synthetic Elo
    assert 0.05 < top["probs"]["title"] < 0.6


def test_conditioning_eliminates_a_team_that_lost_every_group_game(conn):
    # Group A team0 ("Mexico") loses all three games 0-3; it finishes bottom and
    # cannot reach the Round of 32 (4th place never advances).
    groups = load_groups()
    a = groups["A"]
    results = {
        (a[0], a[1]): "0:3", (a[0], a[2]): "0:3", (a[0], a[3]): "0:3",
        (a[1], a[2]): "2:0", (a[1], a[3]): "2:0", (a[2], a[3]): "2:0",
    }
    _build_wc_db(conn, results=results)
    result = simulate(conn, n_sims=1000, seed=3)
    probs = {t["name"]: t["probs"] for t in result["teams"]}
    assert probs[a[0]]["r32"] == 0.0
    # The group's clear winner advances every time.
    assert probs[a[1]]["r32"] == 1.0


def test_write_predictions_persists_one_row_per_team(conn):
    _build_wc_db(conn)
    result = simulate(conn, n_sims=500, seed=1)
    run_id = write_predictions(conn, result)
    rows = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT team_id) FROM predictions WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert rows[0] == 48 and rows[1] == 48
    # Re-writing the same run is idempotent (upsert on run_id, team_id).
    write_predictions(conn, result, run_id=run_id)
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 48
