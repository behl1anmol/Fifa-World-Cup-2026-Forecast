"""Unit tests for the calibration harness core (Step 5, §4.5)."""
from __future__ import annotations

import numpy as np
import pytest

from forecast.calibration import (
    build_backtest,
    build_report,
    evaluate,
    market_blend,
    three_way,
)
from forecast.match_model import MatchModelParams
from test_match_model import _seed_synthetic_history  # reuse the Step 4 seeding helper


def test_build_backtest_returns_finite_metrics(conn):
    _seed_synthetic_history(conn, n_matches=1500, seed=2)
    pred, obs, params = build_backtest(conn, cutoff="2010-06-01")
    assert pred.shape[0] == obs.shape[0] > 0
    assert pred.shape[1] == 3
    assert np.allclose(pred.sum(axis=1), 1.0, atol=1e-9)
    m = evaluate(pred, obs)
    for k in ("rps", "brier", "log_loss"):
        assert np.isfinite(m[k])
    # A genuine fit ran (params differ from the config defaults).
    assert params.base_goals != MatchModelParams.default().base_goals


def test_market_blend_weight_extremes():
    fund = np.array([[0.6, 0.25, 0.15]])
    market = np.array([[0.3, 0.30, 0.40]])
    assert np.allclose(market_blend(fund, market, 0.0), fund)
    assert np.allclose(market_blend(fund, market, 1.0), market)
    mid = market_blend(fund, market, 0.5)
    assert np.allclose(mid, 0.5 * fund + 0.5 * market)


def _seed_one_scored_2026(conn):
    conn.execute("INSERT INTO teams (name, current_elo) VALUES ('Alpha', 1800)")
    conn.execute("INSERT INTO teams (name, current_elo) VALUES ('Beta', 1600)")
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}
    cur = conn.execute(
        "INSERT INTO matches (date, stage, home, away, result, feature_snapshot) "
        "VALUES ('2026-06-20', 'FIFA World Cup', ?, ?, '2:1', '{\"neutral\": true}')",
        (ids["Alpha"], ids["Beta"]),
    )
    mid = cur.lastrowid
    conn.executemany(
        "INSERT INTO ratings_history (team_id, match_id, elo_before, elo_after, "
        "timestamp) VALUES (?, ?, ?, ?, ?)",
        [(ids["Alpha"], mid, 1800, 1810, "2026-06-20"),
         (ids["Beta"], mid, 1600, 1590, "2026-06-20")],
    )
    conn.commit()
    return mid


def test_three_way_scores_all_sources(conn):
    mid = _seed_one_scored_2026(conn)
    matched = [{"home": "Alpha", "away": "Beta", "date": "2026-06-20",
                "commence_time": "2026-06-20T18:00:00Z", "pH": 0.6, "pD": 0.25,
                "pA": 0.15, "n_books": 1, "match_id": mid, "result": "2:1"}]
    tw = three_way(conn, matched, MatchModelParams.default(), weight=0.5)
    assert tw["n"] == 1
    assert set(tw["metrics"]) == {"model", "market", "odds-free"}
    for label in tw["metrics"]:
        assert np.isfinite(tw["metrics"][label]["rps"])


def test_three_way_empty_when_no_scored_matches(conn):
    tw = three_way(conn, [], MatchModelParams.default())
    assert tw["n"] == 0


def test_build_report_mentions_all_sources(conn):
    mid = _seed_one_scored_2026(conn)
    matched = [{"home": "Alpha", "away": "Beta", "date": "2026-06-20",
                "commence_time": "2026-06-20T18:00:00Z", "pH": 0.6, "pD": 0.25,
                "pA": 0.15, "n_books": 1, "match_id": mid, "result": "2:1"}]
    tw = three_way(conn, matched, MatchModelParams.default(), weight=0.5)
    hist = {"n": 5000, "rps": 0.19, "brier": 0.55, "log_loss": 0.95}
    report = build_report(hist, tw, cutoff="2018-01-01", is_sample=True, weight=0.5)
    for token in ("model", "market", "odds-free", "SAMPLE", "RPS"):
        assert token in report
