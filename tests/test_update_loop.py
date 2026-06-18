"""Tests for the in-tournament update loop (architecture Step 6 acceptance).

Maps each acceptance clause to a test, on a self-contained synthetic WC database
(reusing the simulator test's builder) so the suite stays offline and fast.
"""
from __future__ import annotations

from itertools import combinations

import pytest

from forecast.tournament import GROUP_LETTERS, load_groups
from forecast.update_loop import (
    compute_run_id,
    get_snapshot,
    ingest_result,
    latest_snapshot,
    list_runs,
    run_update,
)

# Small sim count keeps the suite quick while staying deterministic under a seed.
SIMS = 800
SEED = 7


def _build_wc_db(conn, results=None):
    """48 participants + 72 group fixtures; ``results`` maps (home, away) -> "h:a".

    Unlike tests/test_simulator.py (which sets ``current_elo`` directly and calls
    ``simulate``), the update loop runs a full ``replay_history`` that recomputes Elo
    from *played* matches only. So we also seed one played pre-2026 friendly per team
    (consecutive teams drawn 0:0) so every participant gets a rating from the replay.
    The draws leave everyone at the default 1500 — uniform Elo is fine for these tests,
    which assert structural behaviour (determinism, history, elimination), not ranking.
    """
    results = results or {}
    groups = load_groups()
    names = [t for letter in GROUP_LETTERS for t in groups[letter]]
    for team in names:
        conn.execute("INSERT OR IGNORE INTO teams (name) VALUES (?)", (team,))
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}

    # One played friendly per team so replay_history assigns every team an Elo.
    for i in range(0, len(names), 2):
        conn.execute(
            "INSERT INTO matches (date, stage, home, away, result, feature_snapshot) "
            "VALUES ('2024-01-01', 'Friendly', ?, ?, '0:0', '{\"neutral\": true}')",
            (ids[names[i]], ids[names[i + 1]]),
        )

    for letter in GROUP_LETTERS:
        for a, b in combinations(groups[letter], 2):
            conn.execute(
                "INSERT INTO matches (date, stage, home, away, result) VALUES "
                "('2026-06-20', 'FIFA World Cup', ?, ?, ?)",
                (ids[a], ids[b], results.get((a, b))),
            )
    conn.commit()
    return groups


def _title(conn, run_id):
    return {t["name"]: t["title_prob"] for t in get_snapshot(conn, run_id)}


# --- Ingest -----------------------------------------------------------------
def test_ingest_flips_null_to_score_no_duplicate(conn):
    groups = _build_wc_db(conn)
    a, b = groups["A"][0], groups["A"][1]
    before = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    assert ingest_result(conn, "2026-06-20", a, b, 3, 1) is True
    after = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    assert after == before  # updated in place, not inserted

    row = conn.execute(
        "SELECT result FROM matches m JOIN teams h ON h.id=m.home JOIN teams aw "
        "ON aw.id=m.away WHERE h.name=? AND aw.name=? AND m.stage='FIFA World Cup'",
        (a, b),
    ).fetchone()
    assert row["result"] == "3:1"

    # Re-ingesting the same score is a no-op (still one row, same value).
    assert ingest_result(conn, "2026-06-20", a, b, 3, 1) is True
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == before


def test_ingest_unknown_team_raises(conn):
    _build_wc_db(conn)
    with pytest.raises(ValueError):
        ingest_result(conn, "2026-06-20", "Atlantis", "Mexico", 1, 0)


def test_ingest_unknown_fixture_returns_false(conn):
    groups = _build_wc_db(conn)
    a, b = groups["A"][0], groups["A"][1]
    # Right teams, wrong date -> no matching fixture, no phantom row created.
    before = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    assert ingest_result(conn, "2030-01-01", a, b, 1, 0) is False
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == before


# --- Deterministic run_id / reproducible snapshot ---------------------------
def test_same_state_same_run_id_and_rows(conn):
    _build_wc_db(conn)
    a = run_update(conn, n_sims=SIMS, seed=SEED)
    b = run_update(conn, n_sims=SIMS, seed=SEED)
    assert a["run_id"] == b["run_id"]
    # One run-group only: re-running the same state overwrote, not appended.
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 48
    assert _title(conn, a["run_id"]) == _title(conn, b["run_id"])


def test_run_id_changes_with_seed(conn):
    _build_wc_db(conn)
    assert compute_run_id(conn, SIMS, 1) != compute_run_id(conn, SIMS, 2)


# --- History accumulates ----------------------------------------------------
def test_new_result_creates_new_run(conn):
    groups = _build_wc_db(conn)
    first = run_update(conn, n_sims=SIMS, seed=SEED)["run_id"]

    a, b = groups["A"][0], groups["A"][1]
    ingest_result(conn, "2026-06-20", a, b, 5, 0)
    second = run_update(conn, n_sims=SIMS, seed=SEED)["run_id"]

    assert first != second
    runs = list_runs(conn)
    run_ids = {r["run_id"] for r in runs}
    assert {first, second} <= run_ids
    assert all(r["n_teams"] == 48 for r in runs)
    # Newest first.
    assert runs[0]["run_id"] == second


# --- Moves sensibly (eliminate one team, hand a group to another) -----------
def test_eliminated_team_drops_and_winner_rises(conn):
    groups = load_groups()
    a = groups["A"]
    # Snapshot the pristine pre-result forecast.
    _build_wc_db(conn)
    before_id = run_update(conn, n_sims=SIMS, seed=SEED)["run_id"]
    before = _title(conn, before_id)

    # a[0] loses all three; a[1] wins all three -> a[0] eliminated, a[1] tops the group.
    decisive = {
        (a[0], a[1]): "0:3", (a[0], a[2]): "0:3", (a[0], a[3]): "0:3",
        (a[1], a[2]): "2:0", (a[1], a[3]): "2:0", (a[2], a[3]): "2:0",
    }
    for (home, away), score in decisive.items():
        hs, as_ = (int(x) for x in score.split(":"))
        assert ingest_result(conn, "2026-06-20", home, away, hs, as_) is True

    after_id = run_update(conn, n_sims=SIMS, seed=SEED)["run_id"]
    after = _title(conn, after_id)

    assert after[a[0]] == 0.0           # eliminated -> title prob collapses to zero
    assert after[a[0]] < before[a[0]] or before[a[0]] == 0.0
    assert after[a[1]] > before[a[1]]   # clear group winner's title prob rises


# --- Read helpers -----------------------------------------------------------
def test_get_snapshot_and_latest(conn):
    _build_wc_db(conn)
    out = run_update(conn, n_sims=SIMS, seed=SEED)
    snap = get_snapshot(conn, out["run_id"])
    assert len(snap) == 48
    titles = [t["title_prob"] for t in snap]
    assert titles == sorted(titles, reverse=True)  # sorted by title desc
    assert set(snap[0]["stage_probabilities"]) == {"r32", "r16", "qf", "sf", "final", "title"}

    latest = latest_snapshot(conn)
    assert latest["run_id"] == out["run_id"]
    assert len(latest["teams"]) == 48


def test_latest_snapshot_none_when_empty(conn):
    assert latest_snapshot(conn) is None
