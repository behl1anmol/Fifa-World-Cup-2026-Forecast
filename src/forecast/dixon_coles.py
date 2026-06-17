"""Dixon-Coles scoreline model — the goal-process core (architecture §4.3).

This module is **pure**: no database, no I/O, no global state — just deterministic
NumPy/SciPy arithmetic over the Dixon-Coles (1997) bivariate goal model. It takes a
pair of Poisson scoring rates ``(lam, mu)`` — the home and away expected goals — and
returns a full scoreline distribution and win/draw/loss probabilities, with the
Dixon-Coles **low-score correction** that fixes independent Poisson's well-known
under-prediction of 0-0 and 1-1 draws.

Where the rates come from (Elo, host advantage, the blend) lives in ``match_model``;
the goal math lives here, so the hand-checked unit tests can pin exact numbers.

The low-score correction multiplies the four lowest joint cells by τ:

    τ(0,0) = 1 − λ·μ·ρ      τ(0,1) = 1 + λ·ρ
    τ(1,0) = 1 + μ·ρ        τ(1,1) = 1 − ρ        (τ = 1 elsewhere)

With ρ < 0 the model shifts mass onto 0-0 and 1-1 (more draws than independent
Poisson) — the empirically observed effect. ρ is **re-fit** for international play
by :func:`fit_rho`, not taken from Dixon & Coles' original league value (§4.3).

Everything is vectorised: ``lam``/``mu`` may be scalars or NumPy arrays (the
simulator passes one rate per simulation for a knockout tie), and the outputs
broadcast accordingly.
"""
from __future__ import annotations

import numpy as np
from scipy import optimize
from scipy.stats import poisson

from .config import DC_MAX_GOALS


def tau(x, y, lam, mu, rho):
    """Dixon-Coles low-score correction for cell ``(x, y)``.

    Accepts scalars or NumPy arrays (broadcast together). Returns ``1`` for every
    cell except the four lowest, which are scaled per the formulas above.
    """
    x, y, lam, mu = np.broadcast_arrays(
        np.asarray(x), np.asarray(y), np.asarray(lam, float), np.asarray(mu, float)
    )
    conds = [
        (x == 0) & (y == 0),
        (x == 0) & (y == 1),
        (x == 1) & (y == 0),
        (x == 1) & (y == 1),
    ]
    choices = [
        1.0 - lam * mu * rho,
        1.0 + lam * rho,
        1.0 + mu * rho,
        np.full(lam.shape, 1.0 - rho),
    ]
    return np.select(conds, choices, default=1.0)


def _poisson_grid(rate, max_goals):
    """Return Poisson pmf over ``0..max_goals`` along a new trailing axis.

    ``rate`` may be a scalar or array of shape ``S``; result has shape
    ``S + (max_goals + 1,)``.
    """
    rate = np.asarray(rate, float)
    ks = np.arange(max_goals + 1)
    return poisson.pmf(ks, rate[..., None])


def scoreline_matrix(lam, mu, rho, max_goals: int = DC_MAX_GOALS):
    """Return the normalised scoreline pmf for a single fixture.

    ``lam``/``mu`` are scalars. The result is a ``(max_goals+1, max_goals+1)`` array
    where ``M[x, y] = P(home scores x, away scores y)``, with the Dixon-Coles
    correction applied and the (truncated) distribution renormalised to sum to 1.
    """
    ph = poisson.pmf(np.arange(max_goals + 1), float(lam))
    pa = poisson.pmf(np.arange(max_goals + 1), float(mu))
    matrix = np.outer(ph, pa)
    # Apply τ to the 2×2 low-score corner only.
    xs = np.array([0, 0, 1, 1])
    ys = np.array([0, 1, 0, 1])
    matrix[xs, ys] *= tau(xs, ys, lam, mu, rho)
    return matrix / matrix.sum()


def outcome_probs(lam, mu, rho, max_goals: int = DC_MAX_GOALS):
    """Return ``(p_home, p_draw, p_away)`` for the Dixon-Coles model.

    Vectorised: ``lam``/``mu`` may be scalars or arrays of a common shape ``S``;
    each returned array has shape ``S``. Built from the full joint pmf so it is
    exactly consistent with :func:`scoreline_matrix`.
    """
    lam = np.asarray(lam, float)
    mu = np.asarray(mu, float)
    ph = _poisson_grid(lam, max_goals)  # S + (G+1,)
    pa = _poisson_grid(mu, max_goals)
    joint = ph[..., :, None] * pa[..., None, :]  # S + (G+1, G+1)

    # τ correction on the four low cells (broadcast over the leading shape S).
    joint[..., 0, 0] *= 1.0 - lam * mu * rho
    joint[..., 0, 1] *= 1.0 + lam * rho
    joint[..., 1, 0] *= 1.0 + mu * rho
    joint[..., 1, 1] *= 1.0 - rho

    total = joint.sum(axis=(-2, -1))
    xs = np.arange(max_goals + 1)
    home_mask = xs[:, None] > xs[None, :]
    away_mask = xs[:, None] < xs[None, :]
    draw_mask = xs[:, None] == xs[None, :]
    p_home = (joint * home_mask).sum(axis=(-2, -1)) / total
    p_draw = (joint * draw_mask).sum(axis=(-2, -1)) / total
    p_away = (joint * away_mask).sum(axis=(-2, -1)) / total
    return p_home, p_draw, p_away


def fit_rho(home_goals, away_goals, lam, mu, weights=None, bounds=(-0.2, 0.2)):
    """Estimate ρ by maximising the Dixon-Coles likelihood with λ,μ held fixed.

    Only the τ factor depends on ρ, so this maximises ``Σ wᵢ·log τ(xᵢ,yᵢ)`` over the
    observed scorelines — a stable 1-D bounded optimisation. ``lam``/``mu`` are the
    per-match rates (from point-in-time Elo, so the fit stays leak-free). Returns the
    scalar ρ̂.
    """
    x = np.asarray(home_goals, int)
    y = np.asarray(away_goals, int)
    lam = np.asarray(lam, float)
    mu = np.asarray(mu, float)
    w = np.ones_like(lam) if weights is None else np.asarray(weights, float)

    def neg_log_lik(rho):
        t = tau(x, y, lam, mu, rho)
        if np.any(t <= 0):
            return np.inf  # ρ pushed a corrected cell non-positive — reject
        return -np.sum(w * np.log(t))

    res = optimize.minimize_scalar(neg_log_lik, bounds=bounds, method="bounded")
    return float(res.x)
