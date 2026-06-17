"""Probabilistic scoring rules and a reliability diagram (architecture §4.5).

The project's success metric is **calibration, not winner-calling** (decision #2):
when the forecast says 20%, that outcome should occur about 20% of the time. This
module implements the three standard scoring rules for football's ordered
three-outcome (home / draw / away) forecasts, plus the reliability curve used to read
calibration. Everything is pure and vectorised: predictions are ``(N, 3)`` arrays of
probabilities in home/draw/away order; outcomes are integer codes ``0/1/2``.

* **RPS** (Ranked Probability Score) — the primary metric; the only one that respects
  the *ordering* home < draw < away, so a forecast that misses a draw by predicting
  the neighbouring outcome is penalised less than one that swaps home for away.
* **Brier** and **log-loss** — order-agnostic companions; lower is better for all three.

The reliability diagram lives here too; it uses matplotlib's headless ``Agg`` backend
so it saves a PNG without a display (the all-Python constraint, §3.1).
"""
from __future__ import annotations

import numpy as np

ORDER = ("home", "draw", "away")  # canonical column order for every (N, 3) array
_EPS = 1e-15


def outcome_index(home_goals, away_goals):
    """Map scorelines to outcome codes: ``0`` home win, ``1`` draw, ``2`` away win."""
    gh = np.asarray(home_goals)
    ga = np.asarray(away_goals)
    return np.where(gh > ga, 0, np.where(gh == ga, 1, 2))


def _as_pred_obs(pred, obs):
    pred = np.asarray(pred, float)
    obs = np.asarray(obs, int)
    if pred.ndim != 2 or pred.shape[1] != 3:
        raise ValueError("pred must have shape (N, 3) in home/draw/away order")
    if obs.shape[0] != pred.shape[0]:
        raise ValueError("pred and obs must have the same number of rows")
    return pred, obs


def _one_hot(obs):
    oh = np.zeros((obs.shape[0], 3))
    oh[np.arange(obs.shape[0]), obs] = 1.0
    return oh


def rps(pred, obs) -> float:
    """Mean Ranked Probability Score over ordered three-outcome forecasts.

    ``RPS = mean over matches of (1/(r-1)) · Σ_{i<r} (CDF_pred_i − CDF_obs_i)²`` with
    ``r = 3``. Lower is better; ``0`` is a perfect, certain forecast.
    """
    pred, obs = _as_pred_obs(pred, obs)
    cdf_pred = np.cumsum(pred, axis=1)
    cdf_obs = np.cumsum(_one_hot(obs), axis=1)
    # Only the first r-1 = 2 cumulative terms matter (the last is 1 for both).
    return float(np.mean(np.sum((cdf_pred[:, :2] - cdf_obs[:, :2]) ** 2, axis=1) / 2.0))


def brier(pred, obs) -> float:
    """Multiclass Brier score: mean squared error against the one-hot outcome."""
    pred, obs = _as_pred_obs(pred, obs)
    return float(np.mean(np.sum((pred - _one_hot(obs)) ** 2, axis=1)))


def log_loss(pred, obs) -> float:
    """Multiclass cross-entropy (log-loss); predictions clipped off 0/1 for safety."""
    pred, obs = _as_pred_obs(pred, obs)
    p = np.clip(pred[np.arange(obs.shape[0]), obs], _EPS, 1.0)
    return float(-np.mean(np.log(p)))


def reliability_curve(pred, obs, n_bins: int = 10):
    """Pooled one-vs-rest reliability curve.

    Flattens all (N, 3) predicted probabilities against their one-hot outcomes, bins by
    predicted probability into ``n_bins`` equal-width bins on ``[0, 1]``, and returns
    ``(mean_pred, observed_freq, counts)`` per non-empty bin — the points a reliability
    diagram plots against the diagonal.
    """
    pred, obs = _as_pred_obs(pred, obs)
    p = pred.ravel()
    y = _one_hot(obs).ravel()
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    mean_pred, obs_freq, counts = [], [], []
    for b in range(n_bins):
        m = idx == b
        c = int(m.sum())
        if c == 0:
            continue
        mean_pred.append(float(p[m].mean()))
        obs_freq.append(float(y[m].mean()))
        counts.append(c)
    return np.array(mean_pred), np.array(obs_freq), np.array(counts)


def save_reliability_diagram(curves: dict, path, title: str = "Reliability diagram"):
    """Save a reliability diagram PNG comparing one or more sources to the diagonal.

    ``curves`` maps a label to a ``(mean_pred, observed_freq, counts)`` triple (the
    output of :func:`reliability_curve`). Uses the headless ``Agg`` backend.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], "k--", linewidth=1, label="perfect calibration")
    for label, (mean_pred, obs_freq, _counts) in curves.items():
        ax.plot(mean_pred, obs_freq, marker="o", label=label)
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Observed frequency")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title(title)
    ax.legend(loc="best")
    fig.tight_layout()
    path = str(path)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
