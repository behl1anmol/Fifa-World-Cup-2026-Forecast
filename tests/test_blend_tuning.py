"""Tests for the re-fit fixed blend weight + no-regression gate (Step 8, §4.3, decision #8).

Weight selection is grid-search on held-out RPS (fixed weights, not per-sample stacking).
The committed Step 5 baseline anchors the acceptance check; the full-dataset comparison
runs in scripts/evaluate_calibration.py, while these unit tests use the deterministic
synthetic harness for speed and assert the re-fit never *worsens* calibration.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from forecast.calibration import (
    backtest_blend,
    check_no_regression,
    load_baseline,
    tune_blend_weight,
    tune_blend_weights_n,
)
from forecast.gbm_view import fit_gbm_view, lightgbm_available
from forecast.match_model import MatchModelParams, blend, blend_n, fit_match_model
from forecast.metrics import rps
from test_match_model import _seed_synthetic_history

CUTOFF = "2010-06-01"
BASELINE_PATH = Path(__file__).resolve().parent / "baselines" / "step5_calibration.json"
_needs_lgbm = pytest.mark.skipif(not lightgbm_available(), reason="lightgbm not installed")


# --- blend generalisation ---------------------------------------------------
def test_blend_n_reduces_to_blend():
    a = (np.array([0.6]), np.array([0.25]), np.array([0.15]))
    b = (np.array([0.3]), np.array([0.30]), np.array([0.40]))
    for w in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert np.allclose(np.array(blend(a, b, w)), np.array(blend_n((a, b), (w, 1.0 - w))))


def test_blend_n_normalises_weights():
    a = (np.array([1.0]), np.array([0.0]), np.array([0.0]))
    b = (np.array([0.0]), np.array([1.0]), np.array([0.0]))
    # Unnormalised (2, 2) must behave like (0.5, 0.5).
    out = np.array(blend_n((a, b), (2.0, 2.0)))
    assert np.allclose(out.ravel(), [0.5, 0.5, 0.0])


# --- committed baseline + regression gate -----------------------------------
def test_baseline_file_is_present_and_well_formed():
    base = load_baseline(BASELINE_PATH)
    for k in ("cutoff", "rps", "brier", "log_loss", "n"):
        assert k in base
    assert 0 < base["rps"] < 1


def test_check_no_regression_logic():
    base = {"rps": 0.17, "brier": 0.51, "log_loss": 0.87}
    better = {"rps": 0.169, "brier": 0.50, "log_loss": 0.86}
    worse = {"rps": 0.18, "brier": 0.51, "log_loss": 0.87}
    assert check_no_regression(better, base)["passed"] is True
    assert check_no_regression(worse, base)["passed"] is False
    # Within epsilon counts as no regression.
    assert check_no_regression({"rps": 0.1705, "brier": 0.51, "log_loss": 0.87},
                               base, eps=1e-3)["passed"] is True


# --- weight tuning on synthetic data ----------------------------------------
def test_tune_blend_weight_returns_grid_minimum(conn):
    _seed_synthetic_history(conn, n_matches=1500, seed=2)
    params = fit_match_model(conn, before=CUTOFF)
    best, table = tune_blend_weight(conn, cutoff=CUTOFF, params=params)
    assert table[best] == pytest.approx(min(table.values()))
    assert best in table


def test_tuned_weight_not_worse_than_seed(conn):
    # The re-fit weight must not increase held-out RPS vs the 0.5 seed (no regression).
    _seed_synthetic_history(conn, n_matches=1500, seed=2)
    params = fit_match_model(conn, before=CUTOFF)
    best, table = tune_blend_weight(conn, cutoff=CUTOFF, params=params)
    assert table[best] <= table[0.5] + 1e-12


@_needs_lgbm
def test_three_view_does_not_regress_vs_two_view(conn):
    _seed_synthetic_history(conn, n_matches=1800, seed=2)
    params = fit_match_model(conn, before=CUTOFF)
    gbm = fit_gbm_view(conn, before=CUTOFF)
    assert gbm is not None
    pred2, obs = backtest_blend(conn, cutoff=CUTOFF, params=params)
    best3, table3 = tune_blend_weights_n(conn, cutoff=CUTOFF, params=params,
                                         gbm_view=gbm, grid_step=0.1)
    # The best fixed 3-view weight is no worse than the 2-view blend on held-out RPS.
    assert table3[best3] <= rps(pred2, obs) + 1e-9
