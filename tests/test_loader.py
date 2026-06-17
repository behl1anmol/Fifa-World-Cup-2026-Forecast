"""Loader tests: parsing, team extraction, and the idempotency acceptance gate."""
from __future__ import annotations

import json

from forecast.db import row_count
from forecast.loader import load

from conftest import EXPECTED_TEAMS


def test_team_extraction(conn, fixture_csv):
    load(conn, fixture_csv)
    names = {r["name"] for r in conn.execute("SELECT name FROM teams")}
    assert names == EXPECTED_TEAMS


def test_load_is_idempotent(conn, fixture_csv):
    """Running the loader twice must not duplicate rows — the Step 1 acceptance."""
    first = load(conn, fixture_csv)
    second = load(conn, fixture_csv)
    assert first["teams"] == second["teams"]
    assert first["matches"] == second["matches"]
    # Fixture has 6 rows and 8 distinct teams.
    assert second["teams"] == len(EXPECTED_TEAMS)
    assert second["matches"] == 6


def test_scoreline_encoding(conn, fixture_csv):
    load(conn, fixture_csv)
    # Mexico 2-0 South Africa on 2026-06-11.
    row = conn.execute(
        "SELECT result FROM matches WHERE date='2026-06-11'"
    ).fetchone()
    assert row["result"] == "2:0"


def test_unplayed_match_has_null_result(conn, fixture_csv):
    load(conn, fixture_csv)
    row = conn.execute(
        "SELECT result FROM matches WHERE date='2026-06-27'"
    ).fetchone()
    assert row["result"] is None


def test_na_to_score_updates_in_place(conn, fixture_csv, tmp_path):
    """A fixture flipping from NA to a real score updates, never duplicates —
    the live-tournament update path."""
    load(conn, fixture_csv)
    before = row_count(conn, "matches")

    # Same file but the 2026-06-27 fixture now has a real score.
    updated = fixture_csv.read_text().replace(
        "2026-06-27,Brazil,South Africa,NA,NA",
        "2026-06-27,Brazil,South Africa,2,1",
    )
    updated_path = tmp_path / "results2.csv"
    updated_path.write_text(updated, encoding="utf-8")

    load(conn, updated_path)
    after = row_count(conn, "matches")

    assert after == before  # no new row
    row = conn.execute(
        "SELECT result FROM matches WHERE date='2026-06-27'"
    ).fetchone()
    assert row["result"] == "2:1"


def test_feature_snapshot_json(conn, fixture_csv):
    load(conn, fixture_csv)
    # Brazil vs Morocco was at a neutral venue.
    row = conn.execute(
        "SELECT feature_snapshot FROM matches WHERE date='2026-06-13'"
    ).fetchone()
    snap = json.loads(row["feature_snapshot"])
    assert snap["neutral"] is True
    assert snap["tournament"] == "FIFA World Cup"
    assert snap["country"] == "United States"
