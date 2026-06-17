#!/usr/bin/env python3
"""Load martj42 results into teams + matches, then print row counts.

Creates the schema if needed, runs the idempotent loader, and reports counts —
this is the Step 1 acceptance check. Running it twice yields identical counts.

Usage:
    python scripts/load_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.config import DB_PATH, MARTJ42_RESULTS_CSV  # noqa: E402
from forecast.db import connect, create_schema  # noqa: E402
from forecast.loader import load  # noqa: E402


def main() -> int:
    if not MARTJ42_RESULTS_CSV.exists():
        print(
            f"ERROR: {MARTJ42_RESULTS_CSV} not found.\n"
            "Run: python scripts/fetch_data.py --source martj42",
            file=sys.stderr,
        )
        return 1

    conn = connect()
    create_schema(conn)
    summary = load(conn)
    conn.close()

    print(f"Database: {DB_PATH}")
    print(f"Source:   {MARTJ42_RESULTS_CSV}")
    print("-" * 40)
    print(f"teams   : {summary['teams']:,}  (+{summary['new_teams']} new)")
    print(f"matches : {summary['matches']:,}  ({summary['matches_processed']:,} processed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
