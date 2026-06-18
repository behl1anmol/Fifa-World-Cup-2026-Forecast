#!/usr/bin/env python3
"""Generate the pre-tournament baseline snapshot (architecture §7) — Step 7.

Simulates the bracket from scratch — ignoring completed 2026 results and using each
team's reconstructed pre-tournament Elo — and writes it under the reserved baseline
``run_id``. This is the fixed "pre" side of the dashboard's "pre-tourney vs now" toggle.
Idempotent: re-running overwrites the baseline in place.

Usage:
    python scripts/build_baseline.py
    python scripts/build_baseline.py --sims 10000 --seed 7
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.config import DB_PATH, MARTJ42_RESULTS_CSV, N_SIMS, SIM_SEED  # noqa: E402
from forecast.db import connect, create_schema, row_count  # noqa: E402
from forecast.loader import load  # noqa: E402
from forecast.ratings import replay_history  # noqa: E402
from forecast.update_loop import write_baseline_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the pre-tournament baseline.")
    parser.add_argument("--sims", type=int, default=N_SIMS)
    parser.add_argument("--seed", type=int, default=SIM_SEED)
    args = parser.parse_args()

    conn = connect()
    create_schema(conn)
    if row_count(conn, "matches") == 0:
        if not MARTJ42_RESULTS_CSV.exists():
            print("ERROR: no data. Run scripts/fetch_data.py then load_data.py.", file=sys.stderr)
            return 1
        load(conn)
    if conn.execute("SELECT COUNT(*) FROM teams WHERE current_elo IS NOT NULL").fetchone()[0] == 0:
        replay_history(conn)

    out = write_baseline_snapshot(conn, n_sims=args.sims, seed=args.seed)
    result = out["result"]

    print(f"Database : {DB_PATH}")
    print(f"Baseline : run_id={out['run_id']}  sims={result['n_sims']:,}  seed={result['seed']}")
    print("-" * 60)
    print(f"{'#':>2}  {'team':<22}{'TITLE':>8}")
    for rank, team in enumerate(result["teams"][:12], 1):
        print(f"{rank:>2}  {team['name']:<22}{team['probs']['title'] * 100:>7.1f}%")
    print("(pre-tournament: no group results, pre-WC Elo)")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
