"""Match-outcome model: Elo-anchored goals, Dixon-Coles scorelines, fixed blend.

This is the Step 4 successor to the simulator's placeholder Elo→goals map
(architecture §4.3, decision #8). Two views of every fixture are combined:

* **Goal-process view (Dixon-Coles).** Expected goals follow the published
  FIFA-tournament form ``λ = exp(β₀ + β₁·EloDiff (+ host term))`` (Gilch & Müller
  2018), fed into :mod:`forecast.dixon_coles` for a full scoreline distribution and
  win/draw/loss probabilities with the re-fit low-score (τ/ρ) correction.
* **Rating view (Elo-implied outcome).** The logistic expected score on the Elo
  difference, split into W/D/L by a fitted draw curve.

The two W/D/L vectors are combined by a **fixed configurable weight**
(``blend_weight``) — not learned stacking, which overfits ~10 matches/team/year
(§4.3). Keeping λ anchored to Elo preserves the self-computed Elo as the single
strength backbone (§4.2): there is no second per-team strength model to double-count.

``MatchModelParams.default()`` returns config-seeded parameters so the simulator and
tests can run without a fit; :func:`fit_match_model` re-estimates them from historical
internationals using point-in-time Elo (``ratings_history.elo_before``), so the fit is
leak-free by construction (§4.2, §7).
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

import numpy as np

from .config import (
    BASE_GOALS,
    BLEND_WEIGHT,
    DC_FIT_HALF_LIFE_DAYS,
    DC_MAX_GOALS,
    DC_RHO,
    DRAW_BASE,
    DRAW_DECAY,
    ELO_GOAL_SCALE_LOG,
    ELO_HOME_ADVANTAGE,
    HOST_HOME_GOALS_LOG,
)
from .dixon_coles import fit_rho, outcome_probs, scoreline_matrix
from .ratings import _is_neutral, _parse_scoreline

_MIN_LAMBDA = 1e-3
_MIN_FIT_ROWS = 500  # below this, the fit is untrustworthy → fall back to defaults


@dataclass(frozen=True)
class MatchModelParams:
    """Tunable match-model parameters. Frozen so it is immutable and shareable.

    All goal terms live in log-rate space. ``base_goals`` is β₀ (the per-side log
    intercept), ``elo_goal_scale`` is β₁ (log-goal supremacy per Elo point),
    ``host_home_goals`` is the additive log-rate home term, applied only to host
    nations at non-neutral venues. ``host_home_elo`` is the equivalent home boost in
    Elo points for the rating view. ``rho`` is the Dixon-Coles correction;
    ``draw_base``/``draw_decay`` parameterise the Elo-implied draw curve;
    ``blend_weight`` is the fixed weight on the Dixon-Coles outcome.
    """

    base_goals: float
    elo_goal_scale: float
    host_home_goals: float
    host_home_elo: float
    rho: float
    draw_base: float
    draw_decay: float
    blend_weight: float

    @classmethod
    def default(cls) -> "MatchModelParams":
        """Config-seeded parameters; lets the simulator/tests run without a fit."""
        return cls(
            base_goals=math.log(BASE_GOALS / 2.0),
            elo_goal_scale=ELO_GOAL_SCALE_LOG,
            host_home_goals=HOST_HOME_GOALS_LOG,
            host_home_elo=ELO_HOME_ADVANTAGE,
            rho=DC_RHO,
            draw_base=DRAW_BASE,
            draw_decay=DRAW_DECAY,
            blend_weight=BLEND_WEIGHT,
        )


def team_lambdas(params: MatchModelParams, elo_h, elo_a, host_home=False):
    """Return ``(lam_home, lam_away)`` expected goals from Elo.

    Scalars or NumPy arrays. ``host_home`` (bool or bool array) adds the host term to
    the home side only. The Elo difference is split symmetrically around β₀ so the
    neutral total stays ``2·exp(β₀)`` regardless of who is nominally "home".
    """
    diff = np.asarray(elo_h, float) - np.asarray(elo_a, float)
    host = np.asarray(host_home, bool)
    home_term = np.where(host, params.host_home_goals, 0.0)
    lam_h = np.exp(params.base_goals + 0.5 * params.elo_goal_scale * diff + home_term)
    lam_a = np.exp(params.base_goals - 0.5 * params.elo_goal_scale * diff)
    return np.clip(lam_h, _MIN_LAMBDA, None), np.clip(lam_a, _MIN_LAMBDA, None)


def elo_outcome(params: MatchModelParams, elo_h, elo_a, host_home=False):
    """Return Elo-implied ``(p_home, p_draw, p_away)``.

    The logistic expected score ``We`` (with a host Elo boost when ``host_home``)
    fixes ``pH + pD/2 = We``; the draw curve ``pD = draw_base·exp(-|Δ|/draw_decay)``
    sets the draw mass; the rest splits into home/away. Clipped and renormalised so
    the three outcomes are valid probabilities. Vectorised.
    """
    host = np.asarray(host_home, bool)
    boost = np.where(host, params.host_home_elo, 0.0)
    diff = np.asarray(elo_h, float) - np.asarray(elo_a, float) + boost
    we = 1.0 / (10.0 ** (-diff / 400.0) + 1.0)
    p_draw = params.draw_base * np.exp(-np.abs(diff) / params.draw_decay)
    p_home = np.clip(we - p_draw / 2.0, 1e-9, None)
    p_away = np.clip(1.0 - we - p_draw / 2.0, 1e-9, None)
    total = p_home + p_draw + p_away
    return p_home / total, p_draw / total, p_away / total


def blend(p_dc, p_elo, weight: float):
    """Fixed-weight average of two ``(pH, pD, pA)`` triples (decision #8)."""
    return tuple(weight * dc + (1.0 - weight) * elo for dc, elo in zip(p_dc, p_elo))


def predict(params: MatchModelParams, elo_h, elo_a, host_home=False):
    """Blended ``(p_home, p_draw, p_away)`` for a fixture. Vectorised."""
    lam_h, lam_a = team_lambdas(params, elo_h, elo_a, host_home)
    p_dc = outcome_probs(lam_h, lam_a, params.rho)
    p_elo = elo_outcome(params, elo_h, elo_a, host_home)
    return blend(p_dc, p_elo, params.blend_weight)


def scoreline_distribution(
    params: MatchModelParams, elo_h, elo_a, host_home=False, max_goals: int = DC_MAX_GOALS
):
    """Return the Dixon-Coles scoreline matrix for a single (scalar) fixture."""
    lam_h, lam_a = team_lambdas(params, elo_h, elo_a, host_home)
    return scoreline_matrix(float(lam_h), float(lam_a), params.rho, max_goals)


# ---------------------------------------------------------------------------
# Fitting (leak-free, point-in-time Elo)
# ---------------------------------------------------------------------------
def _load_fit_rows(conn: sqlite3.Connection, before: str | None):
    """Pull played matches joined with their pre-match Elo (leak-free).

    Each row carries both teams' ``elo_before`` (from ``ratings_history``), the
    scoreline, the neutral flag, and the date. ``before`` optionally restricts to
    matches strictly before a cutoff date for time-split backtests.
    """
    sql = """
        SELECT m.date AS date, m.result AS result, m.feature_snapshot AS fs,
               rh_h.elo_before AS elo_home, rh_a.elo_before AS elo_away
        FROM matches m
        JOIN ratings_history rh_h ON rh_h.match_id = m.id AND rh_h.team_id = m.home
        JOIN ratings_history rh_a ON rh_a.match_id = m.id AND rh_a.team_id = m.away
        WHERE m.result IS NOT NULL
    """
    params: list = []
    if before is not None:
        sql += " AND m.date < ?"
        params.append(before)
    sql += " ORDER BY m.date, m.id"
    return conn.execute(sql, params).fetchall()


def _decay_weights(dates: list[str], half_life_days: float) -> np.ndarray:
    """Exponential time-decay weights: most-recent match weighs 1, older less."""
    ords = np.array(
        [np.datetime64(d).astype("datetime64[D]").astype(int) for d in dates], float
    )
    age = ords.max() - ords
    return 0.5 ** (age / half_life_days)


def _fit_draw_curve(diff: np.ndarray, is_draw: np.ndarray, w: np.ndarray):
    """Fit ``pD = base·exp(-|Δ|/decay)`` to weighted binned draw rates vs |ΔElo|."""
    from scipy.optimize import curve_fit

    ad = np.abs(diff)
    edges = np.quantile(ad, np.linspace(0, 1, 11))
    edges = np.unique(edges)
    if len(edges) < 3:
        return DRAW_BASE, DRAW_DECAY
    centers, rates = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (ad >= lo) & (ad <= hi)
        if w[m].sum() <= 0:
            continue
        centers.append(np.average(ad[m], weights=w[m]))
        rates.append(np.average(is_draw[m], weights=w[m]))
    centers, rates = np.asarray(centers), np.asarray(rates)
    try:
        (base, decay), _ = curve_fit(
            lambda x, b, d: b * np.exp(-x / d),
            centers, rates, p0=[DRAW_BASE, DRAW_DECAY],
            bounds=([0.05, 50.0], [0.6, 5000.0]), maxfev=10000,
        )
        return float(base), float(decay)
    except (RuntimeError, ValueError):
        return DRAW_BASE, DRAW_DECAY


def fit_match_model(
    conn: sqlite3.Connection,
    *,
    half_life_days: float = DC_FIT_HALF_LIFE_DAYS,
    before: str | None = None,
) -> MatchModelParams:
    """Re-estimate match-model parameters from historical internationals.

    Fits β₀, β₁ and the home term by time-weighted Poisson regression of goals on the
    Elo difference and a home indicator, then ρ (Dixon-Coles) and the draw curve.
    Home advantage is estimated from all historical non-neutral matches but is only
    *applied* in 2026 to host nations (§4.3). Falls back to ``default()`` when data is
    insufficient or the fit fails. Requires ``ratings_history`` to be populated.
    """
    rows = _load_fit_rows(conn, before)
    if len(rows) < _MIN_FIT_ROWS:
        return MatchModelParams.default()

    elo_h = np.array([r["elo_home"] for r in rows], float)
    elo_a = np.array([r["elo_away"] for r in rows], float)
    scores = [_parse_scoreline(r["result"]) for r in rows]
    gh = np.array([s[0] for s in scores], float)
    ga = np.array([s[1] for s in scores], float)
    not_neutral = np.array([0.0 if _is_neutral(r["fs"]) else 1.0 for r in rows])
    w_match = _decay_weights([r["date"] for r in rows], half_life_days)
    diff = elo_h - elo_a

    try:
        import statsmodels.api as sm

        # Stack home and away perspectives into one Poisson GLM.
        # home: goals ~ β₀ + 0.5·β₁·diff + β_home·not_neutral
        # away: goals ~ β₀ − 0.5·β₁·diff
        n = len(rows)
        intercept = np.ones(2 * n)
        half_diff = np.concatenate([0.5 * diff, -0.5 * diff])
        home_flag = np.concatenate([not_neutral, np.zeros(n)])
        exog = np.column_stack([intercept, half_diff, home_flag])
        endog = np.concatenate([gh, ga])
        weights = np.concatenate([w_match, w_match])
        model = sm.GLM(
            endog, exog, family=sm.families.Poisson(), freq_weights=weights
        )
        fit = model.fit()
        b0, b1, b_home = (float(v) for v in fit.params)
    except Exception:
        return MatchModelParams.default()

    seed = MatchModelParams.default()
    lam_h = np.exp(b0 + 0.5 * b1 * diff + b_home * not_neutral)
    lam_a = np.exp(b0 - 0.5 * b1 * diff)
    rho = fit_rho(gh.astype(int), ga.astype(int), lam_h, lam_a, weights=w_match)
    draw_base, draw_decay = _fit_draw_curve(diff, (gh == ga).astype(float), w_match)

    return MatchModelParams(
        base_goals=b0,
        elo_goal_scale=b1,
        host_home_goals=b_home,
        host_home_elo=seed.host_home_elo,
        rho=rho,
        draw_base=draw_base,
        draw_decay=draw_decay,
        blend_weight=BLEND_WEIGHT,
    )
