"""Unit tests for the match-outcome model: Elo goals, blend, and fit (Step 4).

The pure-prediction tests use explicit ``MatchModelParams`` (default seeds) so they
do not depend on a fit. One integration test seeds a synthetic match history with a
known data-generating model and checks that ``fit_match_model`` recovers a draw rate
close to the empirical one — the unit-level mirror of the Step 4 acceptance check.
"""
from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest

from forecast.dixon_coles import outcome_probs
from forecast.match_model import (
    MatchModelParams,
    _load_fit_rows,
    elo_outcome,
    fit_match_model,
    predict,
    scoreline_distribution,
    team_lambdas,
)
from forecast.ratings import _is_neutral, _parse_scoreline

P = MatchModelParams.default()


def test_blend_weight_extremes_select_a_single_model():
    eh, ea = 1800.0, 1600.0
    lam_h, lam_a = team_lambdas(P, eh, ea)
    dc = tuple(float(x) for x in outcome_probs(lam_h, lam_a, P.rho))
    elo = tuple(float(x) for x in elo_outcome(P, eh, ea))

    dc_only = dataclasses.replace(P, blend_weight=1.0)
    elo_only = dataclasses.replace(P, blend_weight=0.0)
    assert tuple(float(x) for x in predict(dc_only, eh, ea)) == pytest.approx(dc)
    assert tuple(float(x) for x in predict(elo_only, eh, ea)) == pytest.approx(elo)


def test_stronger_team_has_higher_and_monotone_win_prob():
    elos = np.array([1500.0, 1700.0, 1900.0, 2100.0])
    ph = np.array([float(predict(P, e, 1600.0)[0]) for e in elos])
    assert np.all(np.diff(ph) > 0)
    p_home, _, p_away = (float(x) for x in predict(P, 2100.0, 1500.0))
    assert p_home > p_away


def test_host_home_advantage_helps_the_host():
    lam_h0, _ = team_lambdas(P, 1700.0, 1700.0, host_home=False)
    lam_h1, _ = team_lambdas(P, 1700.0, 1700.0, host_home=True)
    assert float(lam_h1) > float(lam_h0)
    assert float(predict(P, 1700.0, 1700.0, host_home=True)[0]) > float(
        predict(P, 1700.0, 1700.0, host_home=False)[0]
    )


def test_predicted_probs_sum_to_one_vectorized():
    eh = np.array([1500.0, 1800.0, 2000.0])
    ea = np.array([1700.0, 1600.0, 1500.0])
    p_home, p_draw, p_away = predict(P, eh, ea)
    assert np.allclose(p_home + p_draw + p_away, 1.0)


def test_even_match_has_more_draws_than_a_mismatch():
    even = float(elo_outcome(P, 1700.0, 1700.0)[1])
    mismatch = float(elo_outcome(P, 2100.0, 1400.0)[1])
    assert even > mismatch


# --- Fit pipeline (synthetic, leak-free join) --------------------------------
def _seed_synthetic_history(conn, n_matches=1500, seed=1):
    """Insert teams, matches, and point-in-time Elo drawn from the default model."""
    rng = np.random.default_rng(seed)
    n_teams = 24
    elos = np.linspace(1400.0, 2100.0, n_teams)
    for i in range(n_teams):
        conn.execute(
            "INSERT INTO teams (name, current_elo) VALUES (?, ?)",
            (f"T{i}", float(elos[i])),
        )
    ids = [r["id"] for r in conn.execute("SELECT id FROM teams ORDER BY id")]

    hist = []
    base = np.datetime64("2008-01-01")
    for k in range(n_matches):
        i, j = (int(x) for x in rng.choice(n_teams, size=2, replace=False))
        eh, ea = float(elos[i]), float(elos[j])
        neutral = bool(rng.random() < 0.5)
        matrix = scoreline_distribution(P, eh, ea, host_home=not neutral)
        g = matrix.shape[0]
        cell = int(rng.choice(g * g, p=matrix.ravel()))
        hs, as_ = cell // g, cell % g
        date = str(base + k)  # unique date per match → no UNIQUE collisions
        cur = conn.execute(
            "INSERT INTO matches (date, stage, home, away, result, feature_snapshot) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date, "Friendly", ids[i], ids[j], f"{hs}:{as_}",
             json.dumps({"neutral": neutral})),
        )
        mid = cur.lastrowid
        hist.append((ids[i], mid, eh, eh, date))
        hist.append((ids[j], mid, ea, ea, date))
    conn.executemany(
        "INSERT INTO ratings_history (team_id, match_id, elo_before, elo_after, "
        "timestamp) VALUES (?, ?, ?, ?, ?)",
        hist,
    )
    conn.commit()


def test_fit_recovers_draw_rate_on_synthetic_data(conn):
    _seed_synthetic_history(conn, n_matches=1500, seed=1)
    fitted = fit_match_model(conn)
    # The fit actually ran (did not fall back to the config defaults).
    assert fitted.base_goals != MatchModelParams.default().base_goals

    rows = _load_fit_rows(conn, before=None)
    elo_h = np.array([r["elo_home"] for r in rows])
    elo_a = np.array([r["elo_away"] for r in rows])
    scores = [_parse_scoreline(r["result"]) for r in rows]
    gh = np.array([s[0] for s in scores])
    ga = np.array([s[1] for s in scores])
    host = np.array([not _is_neutral(r["fs"]) for r in rows])

    _, p_draw, _ = predict(fitted, elo_h, elo_a, host)
    empirical = float(np.mean(gh == ga))
    assert float(np.mean(p_draw)) == pytest.approx(empirical, abs=0.05)


def test_fit_falls_back_to_default_without_history(conn):
    """No ratings_history → fit returns config defaults rather than erroring."""
    assert fit_match_model(conn) == MatchModelParams.default()
