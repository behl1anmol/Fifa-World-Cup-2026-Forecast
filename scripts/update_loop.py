#!/usr/bin/env python3
"""Run the in-tournament update loop after a newly completed WC2026 match.

Ingest one completed result (or re-sync the martj42 CSV), then rebuild Elo, refit the
match model, re-simulate the remaining bracket, and write one prediction snapshot
(architecture §3.3, §4, §5). The snapshot's ``run_id`` is a deterministic fingerprint
of the tournament state, so re-running on the same state reproduces the same snapshot;
a newly completed result produces a new history entry.

Usage:
    # Ingest a single completed match, then update:
    python scripts/update_loop.py --date 2026-06-18 --home "Brazil" --away "Mexico" --score 2:1

    # Re-sync the martj42 CSV (picks up newly filled scores), then update:
    python scripts/update_loop.py --reload

    # Just re-run the update on the current DB state:
    python scripts/update_loop.py
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
from forecast.simulator import STAGES  # noqa: E402
from forecast.update_loop import (  # noqa: E402
    ingest_result,
    latest_snapshot,
    list_runs,
    run_update,
)


def _parse_score(text: str) -> tuple[int, int]:
    """Parse a ``"H:A"`` CLI score into integer goals."""
    try:
        home, away = text.split(":")
        return int(home), int(away)
    except (ValueError, AttributeError):
        raise SystemExit(f"ERROR: --score must look like '2:1', got {text!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="WC2026 in-tournament update loop.")
    parser.add_argument("--date", help="completed match date, YYYY-MM-DD")
    parser.add_argument("--home", help="home team name (exact)")
    parser.add_argument("--away", help="away team name (exact)")
    parser.add_argument("--score", help="final score as H:A, e.g. 2:1")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="re-load the martj42 CSV (picks up newly filled scores) before updating",
    )
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

    # Capture the pre-update title odds so we can show how the numbers moved.
    before = latest_snapshot(conn)
    before_title = (
        {t["name"]: t["title_prob"] for t in before["teams"]} if before else {}
    )

    # --- Sync the CSV first, then apply any explicit result on top ----------
    # Order matters: the loader upserts the CSV's score for every fixture, so a --reload
    # run *after* an explicit ingest would overwrite the just-entered score with the
    # upstream CSV value (still NULL until the data provider fills it in). Reloading first
    # and ingesting second means the explicit result always wins.
    if args.reload:
        summary = load(conn)
        print(f"Reloaded martj42 CSV: {summary['matches_processed']:,} fixtures processed")

    # --- Ingest the new result ----------------------------------------------
    explicit = any((args.date, args.home, args.away, args.score))
    if explicit:
        if not all((args.date, args.home, args.away, args.score)):
            print(
                "ERROR: --date, --home, --away and --score must be given together.",
                file=sys.stderr,
            )
            return 1
        hs, as_ = _parse_score(args.score)
        try:
            updated = ingest_result(conn, args.date, args.home, args.away, hs, as_)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if not updated:
            print(
                f"ERROR: no WC2026 fixture {args.home} vs {args.away} on {args.date}.",
                file=sys.stderr,
            )
            return 1
        print(f"Ingested: {args.date}  {args.home} {hs}-{as_} {args.away}")

    # --- Update --------------------------------------------------------------
    out = run_update(conn, n_sims=args.sims, seed=args.seed)
    run_id, result = out["run_id"], out["result"]

    print(f"Database : {DB_PATH}")
    print(f"Sims     : {result['n_sims']:,}   seed={result['seed']}   run_id={run_id}")
    print(f"History  : {len(list_runs(conn))} snapshot(s)")
    print("-" * 78)
    header = f"{'#':>2}  {'team':<22}" + "".join(f"{s.upper():>8}" for s in STAGES) + f"{'Δtitle':>9}"
    print(header)
    for rank, team in enumerate(result["teams"], 1):
        cells = "".join(f"{team['probs'][s] * 100:>7.1f}%" for s in STAGES)
        prev = before_title.get(team["name"])
        if prev is None:
            delta = f"{'-':>9}"
        else:
            delta = f"{(team['probs']['title'] - prev) * 100:>+8.2f}%"
        print(f"{rank:>2}  {team['name']:<22}{cells}{delta}")
        if rank == 24:
            print(f"     ... ({len(result['teams']) - 24} more)")
            break

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
