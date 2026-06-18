"""Tests for the shared service layer (Step 7)."""
from __future__ import annotations

import json

import pytest

from forecast import service
from forecast.config import BASELINE_RUN_ID


def test_latest_and_runs_shape(served_conn):
    latest = service.latest(served_conn)
    assert latest is not None
    assert latest["run_id"] != BASELINE_RUN_ID  # baseline never surfaces as "now"
    assert len(latest["teams"]) == 48
    titles = [t["title_prob"] for t in latest["teams"]]
    assert titles == sorted(titles, reverse=True)

    runs = service.runs(served_conn)
    assert len(runs) == 1  # baseline excluded from the live history
    assert all(r["run_id"] != BASELINE_RUN_ID for r in runs)


def test_snapshot_unknown_raises(served_conn):
    with pytest.raises(KeyError):
        service.snapshot(served_conn, "does-not-exist")


def test_team_path_returns_stage_map(served_conn):
    latest = service.latest(served_conn)
    team = latest["teams"][0]
    path = service.team_path(served_conn, team["team_id"])
    assert path["name"] == team["name"]
    assert set(path["stage_probabilities"]) == set(service.STAGE_ORDER)


def test_team_path_unknown_team_raises(served_conn):
    with pytest.raises(KeyError):
        service.team_path(served_conn, 999999)


def test_baseline_present_and_distinct(served_conn):
    base = service.baseline(served_conn)
    assert base is not None
    assert base["run_id"] == BASELINE_RUN_ID
    assert len(base["teams"]) == 48


def test_pre_vs_now_has_deltas(served_conn):
    comp = service.pre_vs_now(served_conn)
    assert comp["has_baseline"] is True
    assert len(comp["rows"]) == 48
    for r in comp["rows"]:
        assert r["delta"] == pytest.approx(r["now_title"] - r["baseline_title"])
    # Sorted by current title prob.
    nows = [r["now_title"] for r in comp["rows"]]
    assert nows == sorted(nows, reverse=True)


def test_export_snapshot_is_json_serialisable(served_conn):
    latest = service.latest(served_conn)
    payload = service.export_snapshot(served_conn, latest["run_id"])
    blob = json.dumps(payload)  # must not raise
    assert json.loads(blob)["run_id"] == latest["run_id"]
    assert len(payload["teams"]) == 48
    assert payload["model_version"] and payload["generated_at"]


def test_market_comparison_well_formed(served_conn):
    # The committed SAMPLE odds may or may not align with the synthetic fixtures; either
    # way the result must be well-formed (the market math itself is covered elsewhere).
    mc = service.market_comparison(served_conn)
    assert set(mc) == {"has_odds", "is_sample", "rows"}
    assert isinstance(mc["rows"], list)
