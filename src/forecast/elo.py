"""Custom Elo rating engine (architecture §4.2).

This module is **pure**: no database, no I/O, no global state — just deterministic
float arithmetic over the World Football Elo formula family (eloratings.net). That
purity is what makes the hand-checked unit tests exact and keeps the leakage guard
honest: the engine never sees match history, so it cannot accidentally use future
information. The leak-free *ordering* lives in ``ratings.py``; the math lives here.

Faithful to §4.2's enumerated knobs and nothing more:

* configurable **K-factor** (update step size),
* configurable **home advantage** (applied only at non-neutral venues),
* optional **margin-of-victory** multiplier.

Deliberately *no* tournament-importance weighting — a single K, per §4.2.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from .config import (
    ELO_DEFAULT_RATING,
    ELO_HOME_ADVANTAGE,
    ELO_K,
    ELO_USE_MOV,
)


@dataclass(frozen=True)
class EloConfig:
    """Tunable Elo parameters. Frozen so it is immutable and side-effect-free.

    Defaults come from ``config`` so the engine, the DB replay, and the CLI all
    agree; tests construct an explicit config to stay independent of config drift.
    """

    default_rating: float = ELO_DEFAULT_RATING
    k: float = ELO_K
    home_advantage: float = ELO_HOME_ADVANTAGE
    use_mov: bool = ELO_USE_MOV


class MatchUpdate(NamedTuple):
    """Both teams' ratings before and after a single match."""

    home_before: float
    home_after: float
    away_before: float
    away_after: float


def expected_score(
    rating_home: float,
    rating_away: float,
    home_advantage: float,
    neutral: bool,
) -> float:
    """Return the home team's expected score ``We`` in ``[0, 1]``.

    The neutral/home-advantage decision lives here so it has a single source of
    truth: at a neutral venue no advantage is added, otherwise the home side's
    rating is boosted by ``home_advantage`` before comparing.
    """
    dr = (rating_home + (0.0 if neutral else home_advantage)) - rating_away
    return 1.0 / (10.0 ** (-dr / 400.0) + 1.0)


def goal_difference_index(goal_diff: int, use_mov: bool) -> float:
    """Margin-of-victory multiplier ``G`` (eloratings.net).

    ``1.0`` for a one-goal margin or a draw, ``1.5`` for two goals, and
    ``(11 + |gd|) / 8`` for three or more. Returns ``1.0`` when MOV is disabled.
    """
    if not use_mov:
        return 1.0
    gd = abs(goal_diff)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11.0 + gd) / 8.0


def update_ratings(
    rating_home: float,
    rating_away: float,
    home_score: int,
    away_score: int,
    neutral: bool,
    config: EloConfig,
) -> MatchUpdate:
    """Apply one match result and return both teams' before/after ratings.

    ``R_new = R_old + K * G * (W - We)`` with ``W`` the actual result
    (``1`` win, ``0.5`` draw, ``0`` loss). The away side's expected score is the
    zero-sum complement ``1 - We``, which keeps the two updates symmetric. No
    clamping or rounding — purity makes the hand-checked tests exact.
    """
    we_home = expected_score(
        rating_home, rating_away, config.home_advantage, neutral
    )
    if home_score > away_score:
        w_home = 1.0
    elif home_score == away_score:
        w_home = 0.5
    else:
        w_home = 0.0

    g = goal_difference_index(home_score - away_score, config.use_mov)
    swing = config.k * g
    home_after = rating_home + swing * (w_home - we_home)
    away_after = rating_away + swing * ((1.0 - w_home) - (1.0 - we_home))
    return MatchUpdate(
        home_before=rating_home,
        home_after=home_after,
        away_before=rating_away,
        away_after=away_after,
    )
