"""Tests for the FastAPI read API (Step 7), via TestClient + dependency override."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from forecast.api import app, get_conn
from forecast.config import BASELINE_RUN_ID
from forecast.db import connect


@pytest.fixture()
def client(served_db_path):
    """TestClient that opens a fresh connection per request against the prepared DB."""
    def _override():
        conn = connect(served_db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_conn] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_runs(client):
    r = client.get("/api/runs")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["n_teams"] == 48
    assert body[0]["run_id"] != BASELINE_RUN_ID


def test_latest_snapshot(client):
    r = client.get("/api/snapshot/latest")
    assert r.status_code == 200
    teams = r.json()["teams"]
    assert len(teams) == 48
    titles = [t["title_prob"] for t in teams]
    assert titles == sorted(titles, reverse=True)


def test_snapshot_unknown_404(client):
    assert client.get("/api/snapshot/nope").status_code == 404


def test_team_path(client):
    latest = client.get("/api/snapshot/latest").json()
    team_id = latest["teams"][0]["team_id"]
    r = client.get(f"/api/team/{team_id}")
    assert r.status_code == 200
    assert set(r.json()["stage_probabilities"]) == {"r32", "r16", "qf", "sf", "final", "title"}


def test_team_unknown_404(client):
    assert client.get("/api/team/999999").status_code == 404


def test_compare(client):
    r = client.get("/api/compare")
    assert r.status_code == 200
    body = r.json()
    assert body["has_baseline"] is True and len(body["rows"]) == 48


def test_market(client):
    r = client.get("/api/market")
    assert r.status_code == 200
    assert set(r.json()) == {"has_odds", "is_sample", "rows"}


def test_export(client):
    latest = client.get("/api/snapshot/latest").json()
    run_id = latest["run_id"]
    r = client.get(f"/api/export/{run_id}")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.json()["run_id"] == run_id


def test_export_unknown_404(client):
    assert client.get("/api/export/nope").status_code == 404
