#!/usr/bin/env python3
"""Calibration / evaluation harness (Step 5 acceptance, architecture §4.5).

Measures forecast honesty — calibration, not winner-calling (decision #2):

1. Time-split historical backtest of the odds-free fundamentals model (fit strictly
   before a cutoff, evaluated on the held-out tail with point-in-time Elo). Prints
   RPS / Brier / log-loss and saves a reliability diagram.
2. Market reference: loads The Odds API h2h prices (live if fetched, else the committed
   [SAMPLE]), de-vigs them, and on matches that are both priced and completed prints a
   three-way RPS / Brier / log-loss for the market-aware model, the market, and the
   odds-free model. For upcoming priced matches it prints a model-vs-market view.
3. Writes a short markdown report.

Usage:
    python scripts/evaluate_calibration.py
    python scripts/evaluate_calibration.py --cutoff 2016-01-01 --market-weight 0.5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.calibration import (  # noqa: E402
    backtest_blend,
    build_backtest,
    build_report,
    check_no_regression,
    evaluate,
    live_comparison,
    load_baseline,
    three_way,
)
from forecast.config import (  # noqa: E402
    BLEND_WEIGHTS_3,
    CALIBRATION_CUTOFF,
    MARKET_BLEND_WEIGHT,
    MARTJ42_RESULTS_CSV,
    PROJECT_ROOT,
    RELIABILITY_BINS,
    REPORTS_DIR,
)
from forecast.db import connect, create_schema, row_count  # noqa: E402
from forecast.gbm_view import fit_gbm_view, lightgbm_available  # noqa: E402
from forecast.loader import load  # noqa: E402
from forecast.market import load_odds_json, map_odds_to_matches, resolve_odds_path  # noqa: E402
from forecast.metrics import reliability_curve, save_reliability_diagram  # noqa: E402
from forecast.ratings import replay_history  # noqa: E402

BASELINE_PATH = PROJECT_ROOT / "tests" / "baselines" / "step5_calibration.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="WC2026 calibration harness.")
    parser.add_argument("--cutoff", default=CALIBRATION_CUTOFF, help="train < cutoff <= test")
    parser.add_argument("--market-weight", type=float, default=MARKET_BLEND_WEIGHT)
    parser.add_argument("--bins", type=int, default=RELIABILITY_BINS)
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

    # --- 1. Historical backtest (odds-free fundamentals model) ----------------
    pred, obs, params = build_backtest(conn, cutoff=args.cutoff)
    hist = evaluate(pred, obs)
    curves = {"odds-free model": reliability_curve(pred, obs, args.bins)}

    print("=" * 64)
    print(f"Historical backtest (odds-free model, time-split < {args.cutoff})")
    print("-" * 64)
    print(f"  matches : {hist['n']:,}")
    print(f"  RPS     : {hist['rps']:.4f}   (primary)")
    print(f"  Brier   : {hist['brier']:.4f}")
    print(f"  log-loss: {hist['log_loss']:.4f}")

    # --- 1b. Optional three-view (DC + Elo + LightGBM) backtest ---------------
    gbm = fit_gbm_view(conn, before=args.cutoff) if lightgbm_available() else None
    final = hist
    if gbm is not None:
        pred3, obs3 = backtest_blend(conn, cutoff=args.cutoff, params=params, gbm_view=gbm,
                                     weights=BLEND_WEIGHTS_3)
        final = evaluate(pred3, obs3)
        curves["three-view (DC+Elo+GBM)"] = reliability_curve(pred3, obs3, args.bins)
        print("-" * 64)
        print(f"  three-view (+LightGBM) weights {BLEND_WEIGHTS_3}")
        print(f"  RPS={final['rps']:.4f}  Brier={final['brier']:.4f}  log-loss={final['log_loss']:.4f}")
    else:
        print("  (LightGBM view unavailable — reporting the two-view blend only)")

    # --- 1c. No-regression gate vs the committed Step 5 baseline --------------
    if BASELINE_PATH.exists():
        baseline = load_baseline(BASELINE_PATH)
        check = check_no_regression(final, baseline)
        print("-" * 64)
        verdict = "PASS — no regression" if check["passed"] else "FAIL — regression!"
        print(f"  Step 5 baseline (cutoff {baseline['cutoff']}): "
              f"RPS {baseline['rps']:.4f} / Brier {baseline['brier']:.4f} / "
              f"log-loss {baseline['log_loss']:.4f}")
        for k in ("rps", "brier", "log_loss"):
            c = check[k]
            print(f"    {k:<9} {c['current']:.4f} vs {c['baseline']:.4f}  "
                  f"{'ok' if c['ok'] else 'REGRESSION'}")
        print(f"  ==> {verdict}")

    # --- 2. Market reference --------------------------------------------------
    odds_path, is_sample = resolve_odds_path()
    tw = {"n": 0, "metrics": {}, "preds": {}, "obs": []}
    print("=" * 64)
    if odds_path is None:
        print("Market reference: no odds file found — skipping market comparison.")
        print("  (set ODDS_API_KEY and run: python scripts/fetch_data.py --source odds_api)")
    else:
        tag = " [SAMPLE]" if is_sample else ""
        print(f"Market reference{tag}: {odds_path.name}")
        print("-" * 64)
        odds_rows = load_odds_json(odds_path)
        matched = map_odds_to_matches(conn, odds_rows)
        tw = three_way(conn, matched, params, weight=args.market_weight)
        if tw["n"] > 0:
            print(f"  three-way on {tw['n']} priced+completed match(es), "
                  f"market weight {args.market_weight:.2f}")
            print(f"  {'source':<12}{'RPS':>9}{'Brier':>9}{'log-loss':>10}")
            for label in ("model", "market", "odds-free"):
                m = tw["metrics"][label]
                print(f"  {label:<12}{m['rps']:>9.4f}{m['brier']:>9.4f}{m['log_loss']:>10.4f}")
            for label, p in tw["preds"].items():
                curves[f"{label} (odds set)"] = reliability_curve(p, tw["obs"], args.bins)
        else:
            print("  no priced+completed matches yet (Odds API serves upcoming odds).")

        upcoming = live_comparison(conn, matched, params, weight=args.market_weight)
        if upcoming:
            print("-" * 64)
            print(f"  upcoming priced matches — model vs market (home win):")
            print(f"  {'fixture':<34}{'model':>8}{'market':>8}{'Δ':>8}")
            for r in upcoming[:12]:
                fixture = f"{r['home']} v {r['away']}"
                print(f"  {fixture:<34}{r['model_home']*100:>7.1f}%{r['market_home']*100:>7.1f}%"
                      f"{r['bullish_home']*100:>+7.1f}")

    # --- 3. Save artifacts ----------------------------------------------------
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    plot_path = save_reliability_diagram(
        curves, REPORTS_DIR / "reliability_step8.png",
        title=f"Calibration — reliability diagram (cutoff {args.cutoff})",
    )
    report = build_report(hist, tw, cutoff=args.cutoff, is_sample=is_sample,
                          weight=args.market_weight)
    report_path = REPORTS_DIR / "step8_calibration_report.md"
    report_path.write_text(report, encoding="utf-8")

    print("=" * 64)
    print(f"Saved reliability plot : {plot_path}")
    print(f"Saved report           : {report_path}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
