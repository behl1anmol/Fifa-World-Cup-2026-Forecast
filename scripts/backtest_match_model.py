#!/usr/bin/env python3
"""Backtest the Step 4 match model's draw calibration (acceptance check).

Fits the blended Dixon-Coles + Elo model on matches *before* a cutoff date, then
predicts the held-out tail using point-in-time Elo (``ratings_history.elo_before``,
so the held-out predictions are leak-free regardless of the split). Prints the
empirical draw rate alongside each model's mean predicted draw probability for the
blended model, a Dixon-Coles-only variant, and an Elo-only variant — the architecture
frames the goal as honest calibration, not winner-calling (§4.5).

Acceptance (§ build step 4): the blended model's predicted draw rate is close to the
historical international draw rate.

Usage:
    python scripts/backtest_match_model.py
    python scripts/backtest_match_model.py --cutoff 2016-01-01
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.config import DB_PATH, MARTJ42_RESULTS_CSV  # noqa: E402
from forecast.db import connect, create_schema, row_count  # noqa: E402
from forecast.loader import load  # noqa: E402
from forecast.match_model import (  # noqa: E402
    _load_fit_rows,
    fit_match_model,
    predict,
)
from forecast.ratings import _is_neutral, _parse_scoreline, replay_history  # noqa: E402


def _test_set(conn, cutoff):
    """Held-out rows on/after ``cutoff`` with point-in-time Elo and scorelines."""
    rows = [r for r in _load_fit_rows(conn, before=None) if r["date"] >= cutoff]
    elo_h = np.array([r["elo_home"] for r in rows], float)
    elo_a = np.array([r["elo_away"] for r in rows], float)
    scores = [_parse_scoreline(r["result"]) for r in rows]
    gh = np.array([s[0] for s in scores])
    ga = np.array([s[1] for s in scores])
    # Home advantage applied wherever the match was genuinely non-neutral.
    host_home = np.array([not _is_neutral(r["fs"]) for r in rows])
    return elo_h, elo_a, gh, ga, host_home


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest match-model draw calibration.")
    parser.add_argument("--cutoff", default="2018-01-01", help="train < cutoff <= test")
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
    dc_only = dataclasses.replace(params, blend_weight=1.0)
    elo_only = dataclasses.replace(params, blend_weight=0.0)

    elo_h, elo_a, gh, ga, host_home = _test_set(conn, args.cutoff)
    n = len(gh)
    if n == 0:
        print("No test matches after cutoff.", file=sys.stderr)
        return 1
    actual_draw = float(np.mean(gh == ga))

    print(f"Database     : {DB_PATH}")
    print(f"Train/test   : cutoff {args.cutoff}  (test n={n:,})")
    print(
        f"Fitted params: β₀={params.base_goals:.3f} β₁={params.elo_goal_scale:.5f} "
        f"host={params.host_home_goals:.3f} ρ={params.rho:.4f} "
        f"draw_base={params.draw_base:.3f} draw_decay={params.draw_decay:.0f}"
    )
    print("-" * 60)
    print(f"{'model':<14}{'pred draw':>12}{'actual draw':>14}{'Δ':>10}")
    for label, p in (("blended", params), ("dixon-coles", dc_only), ("elo-only", elo_only)):
        _, p_draw, _ = predict(p, elo_h, elo_a, host_home)
        pred = float(np.mean(p_draw))
        print(f"{label:<14}{pred:>12.4f}{actual_draw:>14.4f}{pred - actual_draw:>10.4f}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
