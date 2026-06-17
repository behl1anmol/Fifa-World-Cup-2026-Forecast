"""Hand-checked unit tests for the pure Elo engine (architecture §4.2 acceptance).

Every expected value below is computed by hand with K=40, home_advantage=100, and
(unless noted) MOV off, so the engine's arithmetic is pinned to known numbers rather
than to its own output.
"""
from __future__ import annotations

import pytest

from forecast.elo import (
    EloConfig,
    expected_score,
    goal_difference_index,
    update_ratings,
)

# Explicit config so the tests don't depend on config.py defaults drifting.
CFG = EloConfig(default_rating=1500.0, k=40.0, home_advantage=100.0, use_mov=False)
CFG_MOV = EloConfig(default_rating=1500.0, k=40.0, home_advantage=100.0, use_mov=True)


def test_expected_score_home_advantage():
    # dr = 100 -> We = 1 / (10^-0.25 + 1) ~= 0.640065
    assert expected_score(1500, 1500, 100, neutral=False) == pytest.approx(
        0.640065, abs=1e-6
    )
    # Neutral venue removes the advantage: equal ratings -> 0.5 exactly.
    assert expected_score(1500, 1500, 100, neutral=True) == pytest.approx(0.5)


def test_hand_checked_sequence():
    # Match 1: A (home, non-neutral) beats B 1-0, both at 1500.
    #   We_A = 0.640065 ; swing = 40 * (1 - 0.640065) = 14.40
    m1 = update_ratings(1500, 1500, 1, 0, neutral=False, config=CFG)
    assert m1.home_before == 1500.0
    assert m1.home_after == pytest.approx(1514.40, abs=1e-2)
    assert m1.away_before == 1500.0
    assert m1.away_after == pytest.approx(1485.60, abs=1e-2)

    # Match 2: B (home, non-neutral, now 1485.60) draws C (new, 1500) 1-1.
    #   dr = 85.60 -> We_B ~= 0.62075 ; swing = 40 * (0.5 - 0.62075) = -4.83
    m2 = update_ratings(m1.away_after, 1500, 1, 1, neutral=False, config=CFG)
    assert m2.home_before == pytest.approx(1485.60, abs=1e-2)
    assert m2.home_after == pytest.approx(1480.77, abs=1e-2)
    assert m2.away_before == 1500.0
    assert m2.away_after == pytest.approx(1504.83, abs=1e-2)


def test_home_advantage_only_when_not_neutral():
    non_neutral = update_ratings(1500, 1500, 1, 0, neutral=False, config=CFG)
    neutral = update_ratings(1500, 1500, 1, 0, neutral=True, config=CFG)
    # At a neutral venue, equal ratings -> We=0.5 -> swing = 40 * 0.5 = 20.
    assert neutral.home_after == pytest.approx(1520.00, abs=1e-2)
    assert neutral.away_after == pytest.approx(1480.00, abs=1e-2)
    # And it must differ from the non-neutral result (1514.40).
    assert neutral.home_after != pytest.approx(non_neutral.home_after, abs=1e-2)


def test_goal_difference_index():
    assert goal_difference_index(0, use_mov=True) == 1.0
    assert goal_difference_index(1, use_mov=True) == 1.0
    assert goal_difference_index(2, use_mov=True) == 1.5
    assert goal_difference_index(-2, use_mov=True) == 1.5  # uses absolute margin
    assert goal_difference_index(4, use_mov=True) == 1.875  # (11+4)/8
    # Disabled -> always 1.0 regardless of margin.
    assert goal_difference_index(4, use_mov=False) == 1.0


def test_mov_scales_the_update():
    # 3-1 (gd=2 -> G=1.5): base swing 14.40 * 1.5 = 21.60.
    m = update_ratings(1500, 1500, 3, 1, neutral=False, config=CFG_MOV)
    assert m.home_after == pytest.approx(1521.60, abs=1e-2)
    assert m.away_after == pytest.approx(1478.40, abs=1e-2)
    # 5-1 (gd=4 -> G=1.875): 14.40 * 1.875 = 27.00.
    m2 = update_ratings(1500, 1500, 5, 1, neutral=False, config=CFG_MOV)
    assert m2.home_after == pytest.approx(1527.00, abs=1e-2)


def test_zero_sum_symmetry():
    # Whatever one side gains, the other loses exactly — for any single match.
    for hs, as_, neutral in [(1, 0, False), (1, 1, True), (0, 3, False)]:
        m = update_ratings(1620, 1480, hs, as_, neutral=neutral, config=CFG_MOV)
        assert (m.home_after - m.home_before) == pytest.approx(
            -(m.away_after - m.away_before), abs=1e-9
        )
