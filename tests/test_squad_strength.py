"""Tests for the optional, cached squad-strength feature (Step 8, §6/§4.3).

The feature is opt-in and must never affect the core when disabled or absent. These
tests assert the parse + z-score maths, graceful no-ops, and that an enabled adjustment
genuinely moves the live forecast.
"""
from __future__ import annotations

import json

import pytest

from forecast.config import SQUAD_SAMPLE_FILE
from forecast.simulator import simulate
from forecast.squad_strength import (
    adjusted_elo_override,
    load_squad_strength,
    resolve_squad_path,
    squad_elo_adjustments,
)
from forecast.tournament import GROUP_LETTERS, load_groups


def _seed_two_teams(conn):
    conn.execute("INSERT INTO teams (name, current_elo) VALUES ('Strong', 1600)")
    conn.execute("INSERT INTO teams (name, current_elo) VALUES ('Weak', 1600)")
    conn.commit()


def test_sample_parses(tmp_path):
    strengths = load_squad_strength(SQUAD_SAMPLE_FILE)
    assert len(strengths) == 48
    assert all(isinstance(v, float) for v in strengths.values())
    # Metadata keys (leading underscore) are ignored.
    assert "_comment" not in strengths


def test_load_tolerates_bare_mapping(tmp_path):
    p = tmp_path / "squad.json"
    p.write_text(json.dumps({"Alpha": 500, "Beta": "nan-ish"}), encoding="utf-8")
    out = load_squad_strength(p)
    assert out == {"Alpha": 500.0}  # non-numeric value dropped


def test_disabled_is_a_noop(conn):
    _seed_two_teams(conn)
    # Default config disables the feature.
    assert squad_elo_adjustments(conn) == {}
    assert adjusted_elo_override(conn) is None


def test_absent_file_is_a_noop(conn, tmp_path):
    _seed_two_teams(conn)
    missing = tmp_path / "nope.json"
    assert squad_elo_adjustments(conn, enabled=True, path=missing) == {}
    assert adjusted_elo_override(conn, enabled=True, path=missing) is None


def test_zscore_adjustment_is_mean_zero_and_signed(conn, tmp_path):
    _seed_two_teams(conn)
    p = tmp_path / "squad.json"
    p.write_text(json.dumps({"teams": {"Strong": 900, "Weak": 100}}), encoding="utf-8")
    adj = squad_elo_adjustments(conn, enabled=True, scale=25.0, path=p)
    assert set(adj) == {"Strong", "Weak"}
    assert adj["Strong"] > 0 > adj["Weak"]
    assert adj["Strong"] + adj["Weak"] == pytest.approx(0.0, abs=1e-9)


def test_override_includes_all_db_teams(conn, tmp_path):
    _seed_two_teams(conn)
    p = tmp_path / "squad.json"
    p.write_text(json.dumps({"teams": {"Strong": 900, "Weak": 100}}), encoding="utf-8")
    override = adjusted_elo_override(conn, enabled=True, scale=25.0, path=p)
    assert set(override) == {"Strong", "Weak"}
    assert override["Strong"] > 1600 > override["Weak"]


def _build_wc_db(conn):
    groups = load_groups()
    elo = 1700.0
    for letter in GROUP_LETTERS:
        for team in groups[letter]:
            conn.execute("INSERT OR IGNORE INTO teams (name, current_elo) VALUES (?, ?)",
                         (team, elo))
    from itertools import combinations
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}
    for letter in GROUP_LETTERS:
        for a, b in combinations(groups[letter], 2):
            conn.execute("INSERT INTO matches (date, stage, home, away, result) VALUES "
                         "('2026-06-20', 'FIFA World Cup', ?, ?, NULL)", (ids[a], ids[b]))
    conn.commit()
    return groups


def test_squad_override_moves_the_forecast(conn, tmp_path):
    # A strong squad value for one team (z-scored across a spread of teams) must raise
    # its title prob via the Elo override. z-score needs >1 distinct value, so seed a
    # range with the target on top.
    groups = _build_wc_db(conn)
    a = groups["A"]
    target = a[0]
    p = tmp_path / "squad.json"
    p.write_text(json.dumps({"teams": {a[0]: 1000, a[1]: 200, a[2]: 100, a[3]: 50}}),
                 encoding="utf-8")
    base = {t["name"]: t["probs"]["title"] for t in simulate(conn, n_sims=1500, seed=4)["teams"]}
    override = adjusted_elo_override(conn, enabled=True, scale=300.0, path=p)
    assert override[target] > 1700  # target's Elo nudged clearly upward
    boosted = {t["name"]: t["probs"]["title"] for t in
               simulate(conn, n_sims=1500, seed=4, elo_override=override)["teams"]}
    assert boosted[target] > base[target]


def test_resolve_falls_back_to_sample():
    # No live cache committed, so resolve returns the committed sample (is_sample True).
    path, is_sample = resolve_squad_path()
    assert path == SQUAD_SAMPLE_FILE and is_sample is True
    # Live-only resolution finds nothing.
    assert resolve_squad_path(allow_sample=False) == (None, False)
