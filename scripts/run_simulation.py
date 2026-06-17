#!/usr/bin/env python3
"""Run the Monte Carlo bracket simulator and print live title/stage odds.

Ensures the DB is loaded and Elo is built, runs 50,000 seeded simulations of the
remaining bracket (conditioned on completed group results), writes a prediction
snapshot (architecture §5), and prints ranked title odds — the Step 3 acceptance
check. Re-running with the same seed yields identical numbers.

Usage:
    python scripts/run_simulation.py
    python scripts/run_simulation.py --sims 10000 --seed 7
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
from forecast.simulator import STAGES, simulate, write_predictions  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the WC2026 bracket simulator.")
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

    result = simulate(conn, n_sims=args.sims, seed=args.seed)
    run_id = write_predictions(conn, result)

    print(f"Database : {DB_PATH}")
    print(f"Sims     : {result['n_sims']:,}   seed={result['seed']}   run_id={run_id[:8]}")
    print("-" * 72)
    header = f"{'#':>2}  {'team':<22}" + "".join(f"{s.upper():>8}" for s in STAGES)
    print(header)
    for rank, team in enumerate(result["teams"], 1):
        cells = "".join(f"{team['probs'][s] * 100:>7.1f}%" for s in STAGES)
        print(f"{rank:>2}  {team['name']:<22}{cells}")
        if rank == 24:
            print(f"     ... ({len(result['teams']) - 24} more)")
            break

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
