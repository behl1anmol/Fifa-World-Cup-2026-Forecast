"""Tests for the optional LightGBM view (Step 8, §4.3).

The view is strictly optional: training-dependent tests skip cleanly when ``lightgbm``
is absent, and the key guarantee — ``predict3`` with no view equals ``predict`` — is
checked unconditionally so the core's graceful degradation is always covered.
"""
from __future__ import annotations

import numpy as np
import pytest

from forecast import gbm_view
from forecast.config import CALIBRATION_CUTOFF
from forecast.gbm_view import GBMView, fit_gbm_view, lightgbm_available
from forecast.match_model import MatchModelParams, predict, predict3
from test_match_model import _seed_synthetic_history

P = MatchModelParams.default()
_HAS_LGBM = lightgbm_available()
_needs_lgbm = pytest.mark.skipif(not _HAS_LGBM, reason="lightgbm not installed")


def test_predict3_falls_back_to_predict_when_view_none():
    eh = np.array([1500.0, 1800.0, 2000.0])
    ea = np.array([1700.0, 1600.0, 1500.0])
    p2 = predict(P, eh, ea)
    p3 = predict3(P, eh, ea, gbm_view=None)
    assert np.allclose(np.array(p2), np.array(p3))


def test_fit_gbm_view_none_without_lightgbm(conn, monkeypatch):
    monkeypatch.setattr(gbm_view, "lightgbm_available", lambda: False)
    assert gbm_view.fit_gbm_view(conn, before=CALIBRATION_CUTOFF) is None


def test_fit_gbm_view_none_when_data_thin(conn):
    # Empty DB -> fewer than _MIN_FIT_ROWS -> None (graceful, core unaffected).
    assert fit_gbm_view(conn, before=CALIBRATION_CUTOFF) is None


@_needs_lgbm
def test_gbm_view_fits_and_probs_sum_to_one(conn):
    _seed_synthetic_history(conn, n_matches=1500, seed=1)
    view = fit_gbm_view(conn, before=None)
    assert isinstance(view, GBMView)
    eh = np.array([1500.0, 1900.0, 1700.0])
    ea = np.array([1700.0, 1400.0, 1700.0])
    ph, pd, pa = view.predict(eh, ea, np.array([False, False, True]))
    assert np.allclose(ph + pd + pa, 1.0)
    assert np.all((ph >= 0) & (pd >= 0) & (pa >= 0))


@_needs_lgbm
def test_gbm_view_is_elo_monotonic(conn):
    _seed_synthetic_history(conn, n_matches=1500, seed=1)
    view = fit_gbm_view(conn, before=None)
    strong_home = float(view.predict(np.array([2100.0]), np.array([1400.0]), np.array([False]))[0][0])
    weak_home = float(view.predict(np.array([1400.0]), np.array([2100.0]), np.array([False]))[0][0])
    assert strong_home > weak_home


@_needs_lgbm
def test_predict3_with_view_differs_from_two_view(conn):
    _seed_synthetic_history(conn, n_matches=1500, seed=1)
    view = fit_gbm_view(conn, before=None)
    eh, ea = np.array([1800.0]), np.array([1600.0])
    p2 = np.array(predict(P, eh, ea))
    p3 = np.array(predict3(P, eh, ea, gbm_view=view, weights=(0.5, 0.3, 0.2)))
    assert not np.allclose(p2, p3)
