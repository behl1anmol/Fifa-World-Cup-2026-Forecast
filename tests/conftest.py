"""Shared pytest fixtures. No network: everything uses local fixtures + temp DB."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make ``src/`` importable for the test session.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.db import connect, create_schema  # noqa: E402

# A tiny, hand-checkable results.csv mirroring the martj42 schema. Six rows,
# five distinct teams, one unplayed (NA) fixture for the upsert test.
FIXTURE_CSV = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2026-06-11,Mexico,South Africa,2,0,FIFA World Cup,Mexico City,Mexico,FALSE
2026-06-12,Canada,Bosnia and Herzegovina,1,1,FIFA World Cup,Toronto,Canada,FALSE
2026-06-13,Brazil,Morocco,1,1,FIFA World Cup,East Rutherford,United States,TRUE
2026-06-14,Mexico,Canada,3,1,FIFA World Cup,Guadalajara,Mexico,FALSE
2026-06-27,Brazil,South Africa,NA,NA,FIFA World Cup,Miami Gardens,United States,TRUE
1990-07-08,Argentina,Germany,0,1,FIFA World Cup,Rome,Italy,TRUE
"""

# Distinct team names appearing above.
EXPECTED_TEAMS = {
    "Mexico", "South Africa", "Canada", "Bosnia and Herzegovina",
    "Brazil", "Morocco", "Argentina", "Germany",
}


@pytest.fixture()
def fixture_csv(tmp_path: Path) -> Path:
    path = tmp_path / "results.csv"
    path.write_text(FIXTURE_CSV, encoding="utf-8")
    return path


@pytest.fixture()
def conn():
    """An in-memory database with the schema applied."""
    c = connect(":memory:")
    create_schema(c)
    yield c
    c.close()


def build_wc_db(conn, results=None):
    """Populate a schema with 48 WC2026 participants + 72 group fixtures.

    Shared synthetic builder (promoted from tests/test_update_loop.py) for the Step 7
    service/API tests. ``results`` maps (home, away) -> "h:a" for played group games.
    One played pre-2026 friendly per team is seeded so the full Elo replay assigns every
    participant a rating; the draws leave everyone at the default ~1500 (uniform Elo is
    fine for structural assertions).
    """
    from itertools import combinations

    from forecast.tournament import GROUP_LETTERS, load_groups

    results = results or {}
    groups = load_groups()
    names = [t for letter in GROUP_LETTERS for t in groups[letter]]
    for team in names:
        conn.execute("INSERT OR IGNORE INTO teams (name) VALUES (?)", (team,))
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}

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


def _build_served_state(conn):
    """One live snapshot + one pre-tournament baseline on small sims (shared setup)."""
    from forecast.update_loop import run_update, write_baseline_snapshot

    build_wc_db(conn)
    run_update(conn, n_sims=400, seed=7)
    write_baseline_snapshot(conn, n_sims=400, seed=7)


@pytest.fixture()
def served_conn():
    """In-memory DB with a live snapshot + baseline. Backs the service tests (single
    thread), where ``n_sims`` stays tiny for speed."""
    c = connect(":memory:")
    create_schema(c)
    _build_served_state(c)
    yield c
    c.close()


@pytest.fixture()
def served_db_path(tmp_path):
    """File-backed DB with the same served state, for the API tests.

    TestClient dispatches requests on a worker thread, so the API opens a fresh
    connection per request (as in production); a file path lets each request connect in
    its own thread instead of sharing one in-memory connection across threads.
    """
    path = tmp_path / "served.db"
    c = connect(path)
    create_schema(c)
    _build_served_state(c)
    c.close()
    return path
