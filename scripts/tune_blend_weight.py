#!/usr/bin/env python3
"""Re-fit the FIXED blend weight(s) on held-out RPS (Step 8, architecture §4.3, decision #8).

Grid-searches the blend weight that minimises Ranked Probability Score on the strict
time-split tail — for the two-view (Dixon-Coles + Elo) blend and, when ``lightgbm`` is
installed, the optional three-view (… + LightGBM) blend. The weights stay *fixed*
constants applied to every match (not per-sample stacking); this script just chooses
them, and prints the value to paste into ``config.BLEND_WEIGHT`` / ``BLEND_WEIGHTS_3``.

Read-only: it never writes to the database or config. Tuning is deliberately an offline,
committed-constant step so the live forecast never re-fits the weight (reproducibility,
§7; no leakage).

Usage:
    python scripts/tune_blend_weight.py
    python scripts/tune_blend_weight.py --cutoff 2016-01-01 --grid-step 0.05
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.calibration import tune_blend_weight, tune_blend_weights_n  # noqa: E402
from forecast.config import (  # noqa: E402
    BLEND_WEIGHT,
    BLEND_WEIGHTS_3,
    CALIBRATION_CUTOFF,
    MARTJ42_RESULTS_CSV,
)
from forecast.db import connect, create_schema, row_count  # noqa: E402
from forecast.gbm_view import fit_gbm_view, lightgbm_available  # noqa: E402
from forecast.loader import load  # noqa: E402
from forecast.match_model import fit_match_model  # noqa: E402
from forecast.ratings import replay_history  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-fit the fixed blend weight(s) on held-out RPS.")
    parser.add_argument("--cutoff", default=CALIBRATION_CUTOFF, help="train < cutoff <= test")
    parser.add_argument("--grid-step", type=float, default=0.1, help="N-view simplex step")
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

    params = fit_match_model(conn, before=args.cutoff)

    print("=" * 64)
    print(f"Two-view blend (Dixon-Coles + Elo), held-out RPS < {args.cutoff}")
    print("-" * 64)
    best2, table2 = tune_blend_weight(conn, cutoff=args.cutoff, params=params)
    for w in sorted(table2):
        flag = "  <- best" if w == best2 else ""
        marker = "  (current config)" if w == BLEND_WEIGHT else ""
        print(f"  w={w:>4}  RPS={table2[w]:.6f}{flag}{marker}")
    print(f"\n  BEST two-view BLEND_WEIGHT = {best2}  (RPS {table2[best2]:.6f})")

    print("=" * 64)
    if not lightgbm_available():
        print("Three-view blend: lightgbm not installed — skipping (core never depends on it).")
    else:
        gbm = fit_gbm_view(conn, before=args.cutoff)
        if gbm is None:
            print("Three-view blend: LightGBM view could not be fit — skipping.")
        else:
            best3, table3 = tune_blend_weights_n(
                conn, cutoff=args.cutoff, params=params, gbm_view=gbm, grid_step=args.grid_step
            )
            print(f"Three-view blend (DC, Elo, LightGBM), simplex step {args.grid_step}")
            print("-" * 64)
            top = sorted(table3.items(), key=lambda kv: kv[1])[:8]
            for weights, score in top:
                wtxt = ", ".join(f"{x:.2f}" for x in weights)
                cur = "  (current config)" if tuple(round(x, 2) for x in weights) == tuple(BLEND_WEIGHTS_3) else ""
                print(f"  ({wtxt})  RPS={score:.6f}{cur}")
            print(f"\n  BEST three-view BLEND_WEIGHTS_3 = "
                  f"({', '.join(f'{x:.2f}' for x in best3)})  (RPS {table3[best3]:.6f})")

    print("=" * 64)
    print("Paste the chosen value(s) into src/forecast/config.py (offline, committed).")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
