"""Unit tests for the Dixon-Coles scoreline math (Step 4, architecture §4.3).

Pure functions, no DB. Expected values are hand-derived from the model formulas, not
read back from the code, so the tests pin the math rather than the implementation.
"""
from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import poisson

from forecast.dixon_coles import fit_rho, outcome_probs, scoreline_matrix, tau

LAM, MU, RHO = 1.5, 1.2, -0.05


def test_tau_low_score_cells_match_formula():
    assert tau(0, 0, LAM, MU, RHO) == pytest.approx(1.0 - LAM * MU * RHO)
    assert tau(0, 1, LAM, MU, RHO) == pytest.approx(1.0 + LAM * RHO)
    assert tau(1, 0, LAM, MU, RHO) == pytest.approx(1.0 + MU * RHO)
    assert tau(1, 1, LAM, MU, RHO) == pytest.approx(1.0 - RHO)
    # Every other cell is untouched.
    assert tau(2, 1, LAM, MU, RHO) == pytest.approx(1.0)
    assert tau(0, 3, LAM, MU, RHO) == pytest.approx(1.0)


def test_negative_rho_shifts_mass_onto_draws():
    """ρ < 0 raises 0-0 and 1-1 relative to independent Poisson; lowers 1-0 / 0-1."""
    indep = scoreline_matrix(LAM, MU, 0.0)
    dc = scoreline_matrix(LAM, MU, RHO)
    assert dc[0, 0] > indep[0, 0]
    assert dc[1, 1] > indep[1, 1]
    assert dc[1, 0] < indep[1, 0]
    assert dc[0, 1] < indep[0, 1]


def test_rho_zero_factorizes_into_independent_poisson():
    matrix = scoreline_matrix(LAM, MU, 0.0, max_goals=12)
    ph = poisson.pmf(np.arange(13), LAM)
    pa = poisson.pmf(np.arange(13), MU)
    expected = np.outer(ph, pa)
    expected /= expected.sum()
    assert np.allclose(matrix, expected, atol=1e-12)


def test_scoreline_matrix_sums_to_one():
    assert scoreline_matrix(LAM, MU, RHO).sum() == pytest.approx(1.0)


def test_outcome_probs_sum_to_one_and_match_matrix_regions():
    p_home, p_draw, p_away = outcome_probs(LAM, MU, RHO)
    assert float(p_home + p_draw + p_away) == pytest.approx(1.0)

    matrix = scoreline_matrix(LAM, MU, RHO)
    xs = np.arange(matrix.shape[0])
    home = matrix[xs[:, None] > xs[None, :]].sum()
    draw = np.trace(matrix)
    away = matrix[xs[:, None] < xs[None, :]].sum()
    assert float(p_home) == pytest.approx(home, abs=1e-12)
    assert float(p_draw) == pytest.approx(draw, abs=1e-12)
    assert float(p_away) == pytest.approx(away, abs=1e-12)


def test_outcome_probs_vectorized_matches_scalar():
    lam = np.array([0.8, 1.5, 2.3])
    mu = np.array([1.1, 1.2, 0.7])
    vec = np.stack(outcome_probs(lam, mu, RHO), axis=1)  # (3, 3)
    for i in range(3):
        scalar = np.array([float(x) for x in outcome_probs(lam[i], mu[i], RHO)])
        assert np.allclose(vec[i], scalar, atol=1e-12)


def test_fit_rho_recovers_planted_value():
    rng = np.random.default_rng(0)
    rho_true, lam, mu = -0.08, 1.4, 1.1
    matrix = scoreline_matrix(lam, mu, rho_true)
    g = matrix.shape[0]
    cells = rng.choice(g * g, size=200_000, p=matrix.ravel())
    x, y = cells // g, cells % g
    lam_arr = np.full(x.shape, lam)
    mu_arr = np.full(x.shape, mu)
    assert fit_rho(x, y, lam_arr, mu_arr) == pytest.approx(rho_true, abs=0.02)
