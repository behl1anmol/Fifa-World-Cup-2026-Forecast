"""Calibration harness: backtest, market comparison, report (architecture §4.5).

This ties the scoring rules (:mod:`forecast.metrics`) and the market reference
(:mod:`forecast.market`) to the match model (:mod:`forecast.match_model`). Two things
are produced:

1. A **leak-free historical backtest** of the fundamentals (odds-free) model: fit only
   on matches before a cutoff, evaluate the held-out tail with point-in-time Elo
   (``ratings_history.elo_before``). This is the large-N calibration evidence.
2. A **three-way market comparison** on matches that have both odds and a result —
   the odds-free fundamentals model, the de-vigged market, and a market-aware "model"
   that fixed-weight-blends the two (decision #6). This answers "do the fundamentals
   add signal beyond the price?", with the honest goal of *matching* the market.

The same cutoff-fit params drive both, so every prediction is leak-free for both the
historical tail and the (much later) 2026 matches.
"""
from __future__ import annotations

import json
import sqlite3

import numpy as np

from .config import BLEND_WEIGHTS_3, CALIBRATION_CUTOFF, HOST_NATIONS, MARKET_BLEND_WEIGHT
from .dixon_coles import outcome_probs
from .match_model import (
    MatchModelParams,
    _load_fit_rows,
    blend,
    blend_n,
    elo_outcome,
    fit_match_model,
    predict,
    team_lambdas,
)
from .metrics import brier, log_loss, outcome_index, rps
from .ratings import _is_neutral, _parse_scoreline

LABELS = ("model", "market", "odds-free")


def _stack(p_tuple):
    """Stack a ``(pH, pD, pA)`` tuple of arrays into an ``(N, 3)`` array."""
    return np.column_stack([np.atleast_1d(np.asarray(x, float)) for x in p_tuple])


def evaluate(pred, obs) -> dict:
    """Return the three scoring rules plus the sample size for one source."""
    return {
        "rps": rps(pred, obs),
        "brier": brier(pred, obs),
        "log_loss": log_loss(pred, obs),
        "n": int(np.asarray(obs).shape[0]),
    }


def build_backtest(conn: sqlite3.Connection, *, cutoff: str = CALIBRATION_CUTOFF,
                   params: MatchModelParams | None = None):
    """Fundamentals-model predictions on the held-out tail (date ≥ cutoff).

    Fits on ``before=cutoff`` (unless ``params`` is injected) and predicts every later
    match from its point-in-time Elo. Returns ``(pred (N,3), obs (N,), params)``.
    """
    if params is None:
        params = fit_match_model(conn, before=cutoff)
    rows = [r for r in _load_fit_rows(conn, before=None) if r["date"] >= cutoff]
    elo_h = np.array([r["elo_home"] for r in rows], float)
    elo_a = np.array([r["elo_away"] for r in rows], float)
    scores = [_parse_scoreline(r["result"]) for r in rows]
    gh = np.array([s[0] for s in scores])
    ga = np.array([s[1] for s in scores])
    host_home = np.array([not _is_neutral(r["fs"]) for r in rows])
    pred = _stack(predict(params, elo_h, elo_a, host_home))
    obs = outcome_index(gh, ga)
    return pred, obs, params


def _heldout_views(conn, *, cutoff, params, gbm_view=None):
    """Per-view ``(N, 3)`` predictions on the held-out tail (date ≥ cutoff), plus obs.

    Computes each view once — Dixon-Coles, Elo, and (optionally) the LightGBM view — so
    a weight grid-search can sweep cheaply over the cached arrays without re-fitting per
    grid point. Returns ``(views, obs)`` where ``views`` is an ordered dict-like list of
    ``(name, (N,3))`` pairs: ``[("dc", …), ("elo", …)]`` plus ``("gbm", …)`` when a view
    is supplied. Leak-free: uses point-in-time ``elo_before`` exactly like build_backtest.
    """
    rows = [r for r in _load_fit_rows(conn, before=None) if r["date"] >= cutoff]
    elo_h = np.array([r["elo_home"] for r in rows], float)
    elo_a = np.array([r["elo_away"] for r in rows], float)
    host_home = np.array([not _is_neutral(r["fs"]) for r in rows])
    scores = [_parse_scoreline(r["result"]) for r in rows]
    gh = np.array([s[0] for s in scores])
    ga = np.array([s[1] for s in scores])
    obs = outcome_index(gh, ga)

    lam_h, lam_a = team_lambdas(params, elo_h, elo_a, host_home)
    p_dc = _stack(outcome_probs(lam_h, lam_a, params.rho))
    p_elo = _stack(elo_outcome(params, elo_h, elo_a, host_home))
    views = [("dc", p_dc), ("elo", p_elo)]
    if gbm_view is not None:
        views.append(("gbm", _stack(gbm_view.predict(elo_h, elo_a, host_home))))
    return views, obs


def tune_blend_weight(conn, *, cutoff: str = CALIBRATION_CUTOFF,
                      params: MatchModelParams | None = None, grid=None):
    """Grid-search the two-view ``blend_weight`` (weight on Dixon-Coles) on held-out RPS.

    Respects decision #8 — the result is a single *fixed* weight applied to every match,
    chosen by minimising RPS on the time-split tail, **not** per-sample stacking. Returns
    ``(best_weight, {weight: rps})``. Pure evaluation; does not mutate config.
    """
    if params is None:
        params = fit_match_model(conn, before=cutoff)
    if grid is None:
        grid = [round(w, 2) for w in np.linspace(0.0, 1.0, 21)]
    views, obs = _heldout_views(conn, cutoff=cutoff, params=params)
    p_dc = dict(views)["dc"]
    p_elo = dict(views)["elo"]
    table = {}
    for w in grid:
        pred = _stack(blend((p_dc[:, 0], p_dc[:, 1], p_dc[:, 2]),
                            (p_elo[:, 0], p_elo[:, 1], p_elo[:, 2]), w))
        table[w] = rps(pred, obs)
    best = min(table, key=table.get)
    return best, table


def tune_blend_weights_n(conn, *, cutoff: str = CALIBRATION_CUTOFF,
                         params: MatchModelParams | None = None, gbm_view=None,
                         grid_step: float = 0.1):
    """Grid-search the fixed N-view weight simplex (DC, Elo[, GBM]) on held-out RPS.

    Enumerates every weight combination on a ``grid_step`` lattice that sums to 1 and
    returns ``(best_weights, table)`` where ``table`` maps the weight tuple to its RPS.
    With ``gbm_view=None`` this reduces to the two-view search. Fixed weights only.
    """
    if params is None:
        params = fit_match_model(conn, before=cutoff)
    views, obs = _heldout_views(conn, cutoff=cutoff, params=params, gbm_view=gbm_view)
    mats = [m for _, m in views]
    k = len(mats)
    steps = int(round(1.0 / grid_step))
    table = {}
    # Enumerate non-negative integer compositions of ``steps`` into ``k`` parts.
    def compositions(total, parts):
        if parts == 1:
            yield (total,)
            return
        for first in range(total + 1):
            for rest in compositions(total - first, parts - 1):
                yield (first,) + rest

    for comp in compositions(steps, k):
        weights = tuple(c / steps for c in comp)
        triples = [(m[:, 0], m[:, 1], m[:, 2]) for m in mats]
        pred = _stack(blend_n(triples, weights))
        table[weights] = rps(pred, obs)
    best = min(table, key=table.get)
    return best, table


def backtest_blend(conn, *, cutoff: str = CALIBRATION_CUTOFF,
                   params: MatchModelParams | None = None, gbm_view=None, weights=None):
    """Held-out backtest predictions for an arbitrary fixed-weight blend of the views.

    Like :func:`build_backtest` but lets a caller score the re-fit two-view blend or the
    three-view (DC + Elo + LightGBM) blend. With ``gbm_view=None`` and the params'
    ``blend_weight`` this reproduces :func:`build_backtest`. Returns ``(pred (N,3), obs)``.
    """
    if params is None:
        params = fit_match_model(conn, before=cutoff)
    views, obs = _heldout_views(conn, cutoff=cutoff, params=params, gbm_view=gbm_view)
    triples = [(m[:, 0], m[:, 1], m[:, 2]) for _, m in views]
    if weights is None:
        weights = (BLEND_WEIGHTS_3 if gbm_view is not None
                   else (params.blend_weight, 1.0 - params.blend_weight))
    return _stack(blend_n(triples, weights)), obs


def load_baseline(path) -> dict:
    """Load a committed calibration baseline JSON (``{cutoff, rps, brier, log_loss, n}``)."""
    return json.loads(open(path, encoding="utf-8").read())


def check_no_regression(metrics: dict, baseline: dict, eps: float = 1e-3) -> dict:
    """Compare current metrics to a baseline; lower-is-better with tolerance ``eps``.

    Returns ``{metric: {"current", "baseline", "ok"}, "passed": bool}`` for RPS, Brier and
    log-loss. ``ok`` is True when ``current <= baseline + eps`` (no regression).
    """
    out = {"passed": True}
    for key in ("rps", "brier", "log_loss"):
        cur = float(metrics[key])
        base = float(baseline[key])
        ok = cur <= base + eps
        out[key] = {"current": cur, "baseline": base, "ok": ok}
        out["passed"] = out["passed"] and ok
    return out


def market_blend(pred_fund, pred_market, weight: float = MARKET_BLEND_WEIGHT):
    """Market-aware model: fixed-weight blend ``w·market + (1−w)·fundamentals``.

    Reuses :func:`match_model.blend` (the project's single fixed-weight averaging
    primitive, decision #8) column-wise. ``pred_*`` are ``(N, 3)`` arrays.
    """
    fund_t = (pred_fund[:, 0], pred_fund[:, 1], pred_fund[:, 2])
    mkt_t = (pred_market[:, 0], pred_market[:, 1], pred_market[:, 2])
    return _stack(blend(mkt_t, fund_t, weight))


def _completed_2026_elo(conn: sqlite3.Connection) -> dict:
    """Map ``match_id`` → pre-match Elo + venue + scoreline for completed 2026 games."""
    rows = conn.execute(
        """
        SELECT m.id AS id, m.result AS result, m.feature_snapshot AS fs,
               rh_h.elo_before AS eh, rh_a.elo_before AS ea, h.name AS home
        FROM matches m
        JOIN ratings_history rh_h ON rh_h.match_id = m.id AND rh_h.team_id = m.home
        JOIN ratings_history rh_a ON rh_a.match_id = m.id AND rh_a.team_id = m.away
        JOIN teams h ON h.id = m.home
        WHERE m.date >= '2026-01-01' AND m.result IS NOT NULL
        """
    ).fetchall()
    hosts = set(HOST_NATIONS)
    out = {}
    for r in rows:
        gh, ga = _parse_scoreline(r["result"])
        out[r["id"]] = {
            "elo_h": r["eh"],
            "elo_a": r["ea"],
            "host_home": (r["home"] in hosts) and not _is_neutral(r["fs"]),
            "gh": gh,
            "ga": ga,
        }
    return out


def three_way(conn: sqlite3.Connection, matched_rows: list[dict],
              params: MatchModelParams, weight: float = MARKET_BLEND_WEIGHT) -> dict:
    """Scored model/market/odds-free comparison on matches with odds *and* a result.

    ``matched_rows`` come from :func:`market.map_odds_to_matches`. Returns
    ``{"n", "metrics": {label: {...}}, "preds": {label: (n,3)}, "obs": (n,)}`` or
    ``{"n": 0, ...}`` when no row is both priced and completed.
    """
    elo_by_match = _completed_2026_elo(conn)
    fund, market, obs = [], [], []
    for od in matched_rows:
        info = elo_by_match.get(od["match_id"])
        if info is None or od["result"] is None:
            continue  # upcoming or not yet rated → cannot be scored
        pf = predict(params, info["elo_h"], info["elo_a"], info["host_home"])
        fund.append([float(pf[0]), float(pf[1]), float(pf[2])])
        market.append([od["pH"], od["pD"], od["pA"]])
        obs.append(int(outcome_index(info["gh"], info["ga"])))
    if not obs:
        return {"n": 0, "metrics": {}, "preds": {}, "obs": np.array([], int)}

    pred_fund = np.array(fund)
    pred_market = np.array(market)
    pred_model = market_blend(pred_fund, pred_market, weight)
    obs = np.array(obs, int)
    preds = {"model": pred_model, "market": pred_market, "odds-free": pred_fund}
    metrics = {label: evaluate(p, obs) for label, p in preds.items()}
    return {"n": len(obs), "metrics": metrics, "preds": preds, "obs": obs}


def live_comparison(conn: sqlite3.Connection, matched_rows: list[dict],
                    params: MatchModelParams, weight: float = MARKET_BLEND_WEIGHT):
    """Model-vs-market probabilities for *upcoming* priced matches (no scoring yet).

    Uses current Elo (no point-in-time row exists pre-match). Returns a list of dicts
    with the model's and market's home-win prob and the model's bullishness (model −
    market on the home win), useful for the "more/less bullish than the market" view.
    """
    elo = {r["name"]: r["current_elo"] for r in
           conn.execute("SELECT name, current_elo FROM teams")}
    hosts = set(HOST_NATIONS)
    out = []
    for od in matched_rows:
        if od["result"] is not None:
            continue  # already completed → handled by three_way
        eh, ea = elo.get(od["home"]), elo.get(od["away"])
        if eh is None or ea is None:
            continue
        host_home = od["home"] in hosts  # venue flag unknown pre-match; host => home
        pf = predict(params, eh, ea, host_home)
        out.append({
            "home": od["home"],
            "away": od["away"],
            "date": od["date"],
            "model_home": float(pf[0]),
            "market_home": od["pH"],
            "bullish_home": float(pf[0]) - od["pH"],
        })
    return out


def build_report(historical: dict, tw: dict, *, cutoff: str, is_sample: bool,
                 weight: float) -> str:
    """Compose the short markdown calibration report (architecture §4.5 acceptance)."""
    lines = [
        "# Step 5 — Calibration report",
        "",
        "Success is measured by **calibration, not winner-calling** (decision #2): "
        "when the forecast says 20%, that should happen about 20% of the time. The "
        "market is a **reference to match, not beat** (decision #6).",
        "",
        f"## Historical backtest (odds-free fundamentals model, time-split < {cutoff})",
        f"- matches scored: **{historical['n']:,}**",
        f"- RPS (primary): **{historical['rps']:.4f}**  ·  "
        f"Brier: {historical['brier']:.4f}  ·  log-loss: {historical['log_loss']:.4f}",
        "",
        "## Market comparison (matches with odds *and* a result)",
    ]
    if is_sample:
        lines.append("> ⚠️ Using the committed **[SAMPLE]** odds file — illustrative, "
                     "not a real market read. Set `ODDS_API_KEY` and fetch live odds "
                     "for a genuine comparison.")
    if tw["n"] == 0:
        lines += [
            "",
            "_No match is currently both priced and completed._ The Odds API free tier "
            "serves only upcoming odds, so scored market calibration accrues as fixtures "
            "finish with odds captured beforehand. The pipeline is in place and will "
            "populate over the tournament.",
        ]
        return "\n".join(lines) + "\n"

    lines += [
        f"- matches scored: **{tw['n']}**  ·  market blend weight: {weight:.2f}",
        "",
        f"| source | RPS | Brier | log-loss |",
        f"|--------|-----|-------|----------|",
    ]
    for label in LABELS:
        m = tw["metrics"][label]
        lines.append(f"| {label} | {m['rps']:.4f} | {m['brier']:.4f} | {m['log_loss']:.4f} |")

    free = tw["metrics"]["odds-free"]["rps"]
    mkt = tw["metrics"]["market"]["rps"]
    model = tw["metrics"]["model"]["rps"]
    gap = free - mkt
    verdict = (
        "the odds-free fundamentals **match or beat** the market on RPS here"
        if gap <= 0 else
        "the market is **better calibrated** than the odds-free fundamentals here"
    )
    blend_help = (
        "blending the price in **helps** (lower RPS than both inputs)"
        if model < min(free, mkt) else
        "blending the price in does not improve on the better single source"
    )
    lines += [
        "",
        f"On this set, {verdict} (ΔRPS = {gap:+.4f}); {blend_help}. With so few scored "
        "matches these are directional, not verdicts — calibration converges as more "
        "fixtures complete (§4.5).",
    ]
    return "\n".join(lines) + "\n"
