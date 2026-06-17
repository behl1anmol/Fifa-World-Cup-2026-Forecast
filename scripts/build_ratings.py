#!/usr/bin/env python3
"""Replay the match history into point-in-time Elo and print the top-20.

Creates the schema if needed, loads martj42 results if the matches table is empty,
runs the leak-free Elo replay, then prints the current top-20 teams alongside the
eloratings.net reference — the Step 2 acceptance check. Re-running yields an
identical table (the replay is deterministic).

Usage:
    python scripts/build_ratings.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.config import DB_PATH, MARTJ42_RESULTS_CSV  # noqa: E402
from forecast.db import connect, create_schema, row_count  # noqa: E402
from forecast.loader import load  # noqa: E402
from forecast.ratings import load_reference_elo, replay_history  # noqa: E402


def main() -> int:
    conn = connect()
    create_schema(conn)

    if row_count(conn, "matches") == 0:
        if not MARTJ42_RESULTS_CSV.exists():
            print(
                f"ERROR: {MARTJ42_RESULTS_CSV} not found.\n"
                "Run: python scripts/fetch_data.py --source martj42",
                file=sys.stderr,
            )
            return 1
        load(conn)

    summary = replay_history(conn)

    print(f"Database: {DB_PATH}")
    print("-" * 60)
    print(f"matches replayed : {summary['matches_replayed']:,}")
    print(f"teams rated      : {summary['teams_rated']:,}")
    print("-" * 60)
    print("Top 20 by current Elo (vs eloratings.net reference):")
    print(f"{'#':>2}  {'team':<24}{'elo':>8}{'ref':>8}{'Δ':>8}")

    reference = load_reference_elo()
    top20 = conn.execute(
        """
        SELECT name, current_elo FROM teams
        WHERE current_elo IS NOT NULL
        ORDER BY current_elo DESC
        LIMIT 20
        """
    ).fetchall()
    for rank, row in enumerate(top20, start=1):
        name, elo = row["name"], row["current_elo"]
        ref = reference.get(name)
        if ref is None:
            ref_str, delta_str = f"{'-':>8}", f"{'-':>8}"
        else:
            ref_str, delta_str = f"{ref:>8.0f}", f"{elo - ref:>+8.0f}"
        print(f"{rank:>2}  {name:<24}{elo:>8.1f}{ref_str}{delta_str}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
