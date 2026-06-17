"""Integration tests for the point-in-time Elo replay (architecture §4.2).

Uses the shared in-memory ``conn`` fixture and the tiny ``fixture_csv`` (6 rows:
5 played + 1 unplayed NA). Mexico appears twice, which makes it the cleanest probe
for leak-free continuity.
"""
from __future__ import annotations

from forecast.loader import load
from forecast.ratings import replay_history


def _team_id(conn, name: str) -> int:
    return conn.execute(
        "SELECT id FROM teams WHERE name = ?", (name,)
    ).fetchone()["id"]


def _history(conn, team_id: int):
    """A team's ratings_history rows in point-in-time order."""
    return conn.execute(
        """
        SELECT elo_before, elo_after, timestamp, match_id
        FROM ratings_history WHERE team_id = ?
        ORDER BY timestamp, match_id
        """,
        (team_id,),
    ).fetchall()


def test_leak_free_continuity(conn, fixture_csv):
    load(conn, fixture_csv)
    replay_history(conn)

    # Every team's first appearance starts at the 1500 default, and every later
    # match's elo_before is exactly the prior match's elo_after — no leakage, no
    # rounding drift.
    for row in conn.execute("SELECT id FROM teams"):
        rows = _history(conn, row["id"])
        if not rows:
            continue
        assert rows[0]["elo_before"] == 1500.0
        for prev, cur in zip(rows, rows[1:]):
            assert cur["elo_before"] == prev["elo_after"]

    # Concrete probe: Mexico's second match (06-14) carries its first (06-11).
    mexico = _history(conn, _team_id(conn, "Mexico"))
    assert len(mexico) == 2
    assert mexico[1]["elo_before"] == mexico[0]["elo_after"]
    assert mexico[0]["elo_before"] == 1500.0


def test_unplayed_matches_skipped(conn, fixture_csv):
    load(conn, fixture_csv)
    replay_history(conn)

    # 5 played matches -> exactly 2 rows each -> 10 rows; the NA fixture contributes none.
    total = conn.execute("SELECT COUNT(*) FROM ratings_history").fetchone()[0]
    assert total == 10

    na_match = conn.execute(
        "SELECT id FROM matches WHERE result IS NULL"
    ).fetchone()["id"]
    referenced = conn.execute(
        "SELECT COUNT(*) FROM ratings_history WHERE match_id = ?", (na_match,)
    ).fetchone()[0]
    assert referenced == 0


def test_replay_is_deterministic(conn, fixture_csv):
    load(conn, fixture_csv)

    def snapshot():
        history = conn.execute(
            "SELECT team_id, match_id, elo_before, elo_after, timestamp "
            "FROM ratings_history ORDER BY team_id, match_id"
        ).fetchall()
        elos = conn.execute(
            "SELECT id, current_elo FROM teams ORDER BY id"
        ).fetchall()
        return [tuple(r) for r in history], [tuple(r) for r in elos]

    replay_history(conn)
    first = snapshot()
    replay_history(conn)  # rebuild over the same data
    second = snapshot()
    assert first == second


def test_current_elo_is_latest_after(conn, fixture_csv):
    load(conn, fixture_csv)
    replay_history(conn)

    for name in ("Mexico", "Canada", "Brazil"):
        team_id = _team_id(conn, name)
        rows = _history(conn, team_id)
        current = conn.execute(
            "SELECT current_elo FROM teams WHERE id = ?", (team_id,)
        ).fetchone()["current_elo"]
        assert current == rows[-1]["elo_after"]


def test_neutral_venue_draw_leaves_equal_teams_unchanged(conn, fixture_csv):
    # Brazil-Morocco (06-13) is neutral and a 1-1 draw from 1500/1500, so with no
    # home advantage applied both teams should remain at 1500.
    load(conn, fixture_csv)
    replay_history(conn)
    brazil = _history(conn, _team_id(conn, "Brazil"))
    assert brazil[0]["elo_before"] == 1500.0
    assert brazil[0]["elo_after"] == 1500.0
