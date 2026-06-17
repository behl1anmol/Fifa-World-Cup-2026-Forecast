"""Unit tests for the scoring rules and reliability curve (Step 5, §4.5).

Hand-derived expected values; pure functions, no DB. Predictions are (N, 3) arrays in
home/draw/away order; outcomes are 0/1/2 codes.
"""
from __future__ import annotations

import numpy as np
import pytest

from forecast.metrics import (
    brier,
    log_loss,
    outcome_index,
    reliability_curve,
    rps,
)


def test_outcome_index():
    assert list(outcome_index([2, 1, 0], [0, 1, 3])) == [0, 1, 2]


def test_perfect_forecast_scores_zero():
    pred = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    obs = np.array([0, 1, 2])
    assert rps(pred, obs) == pytest.approx(0.0)
    assert brier(pred, obs) == pytest.approx(0.0)
    assert log_loss(pred, obs) == pytest.approx(0.0)


def test_brier_and_logloss_known_values():
    pred = np.array([[0.7, 0.2, 0.1]])
    obs = np.array([0])
    # (0.7-1)^2 + 0.2^2 + 0.1^2 = 0.09 + 0.04 + 0.01 = 0.14
    assert brier(pred, obs) == pytest.approx(0.14)
    assert log_loss(pred, obs) == pytest.approx(-np.log(0.7))


def test_rps_respects_outcome_ordering():
    # Outcome is an away win (2). Putting mass on the adjacent draw should beat
    # putting it on the far home outcome — RPS is order-aware, Brier/log-loss are not.
    obs = np.array([2])
    near = np.array([[0.0, 1.0, 0.0]])  # all on draw (adjacent to away)
    far = np.array([[1.0, 0.0, 0.0]])   # all on home (far from away)
    assert rps(near, obs) == pytest.approx(0.5)
    assert rps(far, obs) == pytest.approx(1.0)
    assert rps(near, obs) < rps(far, obs)
    # Brier cannot tell them apart (both miss the one-hot by the same magnitude).
    assert brier(near, obs) == pytest.approx(brier(far, obs))


def test_rps_matches_explicit_loop():
    rng = np.random.default_rng(0)
    pred = rng.dirichlet([2, 2, 2], size=50)
    obs = rng.integers(0, 3, size=50)
    cum = np.cumsum(pred, axis=1)
    oh = np.zeros_like(pred)
    oh[np.arange(50), obs] = 1.0
    cum_obs = np.cumsum(oh, axis=1)
    manual = np.mean([(np.sum((cum[i, :2] - cum_obs[i, :2]) ** 2)) / 2.0 for i in range(50)])
    assert rps(pred, obs) == pytest.approx(manual)


def test_reliability_curve_on_perfectly_calibrated_set():
    # Half the matches certain-home (and home happens), half certain-away (away
    # happens). Pooled one-vs-rest puts mass at p=1 (obs 1) and p=0 (obs 0).
    pred = np.array([[1.0, 0.0, 0.0]] * 10 + [[0.0, 0.0, 1.0]] * 10)
    obs = np.array([0] * 10 + [2] * 10)
    mean_pred, obs_freq, counts = reliability_curve(pred, obs, n_bins=10)
    assert np.allclose(mean_pred, obs_freq, atol=1e-9)  # on the diagonal
    assert counts.sum() == pred.size  # every probability binned


def test_pred_shape_validation():
    with pytest.raises(ValueError):
        rps(np.array([[0.5, 0.5]]), np.array([0]))
