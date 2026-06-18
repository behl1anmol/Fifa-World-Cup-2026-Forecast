"""Unit tests for odds de-vig, parsing, and match mapping (Step 5, §6/§4.5)."""
from __future__ import annotations

import json

import pytest

from forecast.config import ODDS_SAMPLE_FILE
from forecast.market import (
    decimal_to_implied,
    devig,
    load_odds_json,
    map_odds_to_matches,
    market_probs_by_match_id,
)


def test_decimal_to_implied():
    assert decimal_to_implied(2.0) == pytest.approx(0.5)
    assert decimal_to_implied(4.0) == pytest.approx(0.25)


def test_devig_normalises_and_strips_overround():
    # Implied probs from 1.9 / 3.5 / 4.0 sum to > 1 (the bookmaker margin).
    ph, pd, pa = (decimal_to_implied(x) for x in (1.9, 3.5, 4.0))
    assert ph + pd + pa > 1.0
    h, d, a = devig(ph, pd, pa)
    assert h + d + a == pytest.approx(1.0)
    assert h > d and h > a  # 1.9 is the favourite


def test_load_sample_json_parses_and_maps_names():
    rows = load_odds_json(ODDS_SAMPLE_FILE)
    assert len(rows) == 8
    for r in rows:
        assert r["pH"] + r["pD"] + r["pA"] == pytest.approx(1.0)
        assert r["n_books"] == 2
    homes = {r["home"] for r in rows}
    assert "United States" in homes  # common name kept as-is


def test_alias_maps_oddsapi_name_to_db_name(tmp_path):
    data = [{
        "home_team": "USA", "away_team": "Paraguay",
        "commence_time": "2026-06-12T20:00:00Z",
        "bookmakers": [{"key": "x", "markets": [{"key": "h2h", "outcomes": [
            {"name": "USA", "price": 1.9},
            {"name": "Paraguay", "price": 4.2},
            {"name": "Draw", "price": 3.4}]}]}],
    }]
    p = tmp_path / "odds.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    rows = load_odds_json(p)
    assert rows[0]["home"] == "United States"  # "USA" → DB name


def test_map_odds_to_matches(conn):
    conn.execute("INSERT INTO teams (name) VALUES ('Mexico')")
    conn.execute("INSERT INTO teams (name) VALUES ('South Africa')")
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}
    conn.execute(
        "INSERT INTO matches (date, stage, home, away, result) VALUES "
        "('2026-06-11', 'FIFA World Cup', ?, ?, '2:0')",
        (ids["Mexico"], ids["South Africa"]),
    )
    conn.commit()
    odds = [{"home": "Mexico", "away": "South Africa", "date": "2026-06-11",
             "commence_time": "2026-06-11T18:00:00Z", "pH": 0.6, "pD": 0.25,
             "pA": 0.15, "n_books": 1}]
    matched = map_odds_to_matches(conn, odds)
    assert len(matched) == 1
    assert matched[0]["result"] == "2:0"
    assert matched[0]["match_id"] is not None


def test_map_odds_skips_unknown_fixture(conn):
    odds = [{"home": "Narnia", "away": "Gondor", "date": "2026-06-11",
             "commence_time": "2026-06-11T18:00:00Z", "pH": 0.5, "pD": 0.3,
             "pA": 0.2, "n_books": 1}]
    assert map_odds_to_matches(conn, odds) == []


def test_market_probs_by_match_id_filters_completed():
    # Only upcoming (result is None) priced fixtures become live-forecast overrides;
    # a completed fixture is already conditioned on its real score and must be excluded.
    matched = [
        {"match_id": 11, "pH": 0.6, "pD": 0.25, "pA": 0.15, "result": None},
        {"match_id": 22, "pH": 0.4, "pD": 0.30, "pA": 0.30, "result": "2:1"},
        {"match_id": None, "pH": 0.5, "pD": 0.30, "pA": 0.20, "result": None},
    ]
    out = market_probs_by_match_id(matched)
    assert out == {11: (0.6, 0.25, 0.15)}
