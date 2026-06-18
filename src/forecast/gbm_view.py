"""Optional LightGBM view for the match-outcome blend (architecture §4.3) — Step 8.

A LightGBM classifier is the architecture's explicitly *optional, last-in-line* third
view (decision: "overfit-prone on small samples, so it is not part of the core"). This
module keeps it entirely isolated: ``match_model`` never imports ``lightgbm``, and every
entry point degrades gracefully — if the dependency is missing or the data is too thin,
:func:`fit_gbm_view` returns ``None`` and the model falls back to the two-view
Dixon-Coles + Elo blend. The core therefore never depends on LightGBM.

Leak-free by construction: training uses the same point-in-time Elo join as the rest of
the model (``match_model._load_fit_rows`` on ``ratings_history.elo_before``), the same
time-decay weighting (``match_model._decay_weights``), and a strict ``before`` cutoff.
Predictions are returned in the canonical home/draw/away order. Determinism is pinned
(single thread, fixed seed) so the reproducibility guarantee (§7) holds.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np

from .config import DC_FIT_HALF_LIFE_DAYS
from .match_model import _MIN_FIT_ROWS, _decay_weights, _load_fit_rows
from .metrics import outcome_index
from .ratings import _is_neutral, _parse_scoreline

# Feature columns, in a fixed order. ``squad_diff`` is an optional slot kept for when a
# *historical* squad-strength series exists; today it is absent (only a current snapshot
# is available, which would leak), so the trained view uses elo_diff + host_home.
BASE_FEATURES = ("elo_diff", "host_home")


def lightgbm_available() -> bool:
    """True when the optional ``lightgbm`` dependency can be imported."""
    try:
        import lightgbm  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - any import failure means "unavailable"
        return False


def _features(elo_h, elo_a, host_home, squad_diff=None):
    """Stack the engineered feature matrix ``(N, k)`` for the view.

    ``elo_diff`` and a 0/1 ``host_home`` flag are always present; ``squad_diff`` (the
    home−away squad-strength gap) is appended only when supplied.
    """
    elo_h = np.atleast_1d(np.asarray(elo_h, float))
    elo_a = np.atleast_1d(np.asarray(elo_a, float))
    host = np.atleast_1d(np.asarray(host_home)).astype(float)
    host = np.broadcast_to(host, elo_h.shape)
    cols = [elo_h - elo_a, host]
    if squad_diff is not None:
        cols.append(np.broadcast_to(np.atleast_1d(np.asarray(squad_diff, float)), elo_h.shape))
    return np.column_stack(cols)


@dataclass(frozen=True)
class GBMView:
    """A fitted LightGBM W/D/L view. ``predict`` returns home/draw/away probabilities."""

    booster: object
    feature_names: tuple

    def predict(self, elo_h, elo_a, host_home=False, squad_diff=None):
        """Vectorised ``(pH, pD, pA)`` from the classifier, reordered to home/draw/away."""
        import warnings

        squad = squad_diff if "squad_diff" in self.feature_names else None
        x = _features(elo_h, elo_a, host_home, squad)
        with warnings.catch_warnings():
            # Trained on a bare ndarray; predicting on one is intentional. Silence the
            # benign sklearn "X does not have valid feature names" notice.
            warnings.simplefilter("ignore", category=UserWarning)
            proba = self.booster.predict_proba(x)
        # Map classifier columns (its ``classes_``) onto home=0/draw=1/away=2.
        classes = list(self.booster.classes_)
        idx = [classes.index(c) for c in (0, 1, 2)]
        ordered = proba[:, idx]
        return ordered[:, 0], ordered[:, 1], ordered[:, 2]


def fit_gbm_view(
    conn: sqlite3.Connection,
    *,
    before: str | None = None,
    half_life_days: float = DC_FIT_HALF_LIFE_DAYS,
    random_state: int = 20260618,
) -> GBMView | None:
    """Fit the optional LightGBM W/D/L view leak-free, or return ``None``.

    Returns ``None`` (so the caller falls back to the two-view blend) when ``lightgbm``
    is unavailable, the fit window has fewer than ``_MIN_FIT_ROWS`` played matches, or
    training fails. Uses ``elo_before`` (point-in-time Elo) and a strict ``before``
    cutoff, with the project's standard exponential time-decay weighting.
    """
    if not lightgbm_available():
        return None
    rows = _load_fit_rows(conn, before)
    if len(rows) < _MIN_FIT_ROWS:
        return None
    try:
        from lightgbm import LGBMClassifier

        elo_h = np.array([r["elo_home"] for r in rows], float)
        elo_a = np.array([r["elo_away"] for r in rows], float)
        host = np.array([0.0 if _is_neutral(r["fs"]) else 1.0 for r in rows])
        scores = [_parse_scoreline(r["result"]) for r in rows]
        gh = np.array([s[0] for s in scores])
        ga = np.array([s[1] for s in scores])
        y = np.asarray(outcome_index(gh, ga), int)
        x = _features(elo_h, elo_a, host)
        w = _decay_weights([r["date"] for r in rows], half_life_days)

        clf = LGBMClassifier(
            objective="multiclass",
            num_class=3,
            n_estimators=200,
            learning_rate=0.05,
            num_leaves=31,
            min_child_samples=100,
            subsample=0.8,
            colsample_bytree=0.9,
            random_state=random_state,
            n_jobs=1,
            deterministic=True,
            force_row_wise=True,
            verbose=-1,
        )
        clf.fit(x, y, sample_weight=w)
    except Exception:  # noqa: BLE001 - any training failure => view absent, core unaffected
        return None
    return GBMView(booster=clf, feature_names=BASE_FEATURES)
