"""FastAPI read API over the persisted forecast (architecture §4.6) — Step 7.

Thin JSON wrapper around the shared ``service`` layer. Every endpoint is read-only and
opens its own SQLite connection per request (uvicorn serves on a thread pool, and
sqlite connections are not safe to share across threads). The same ``service`` functions
back the Streamlit dashboard, so the API and the UI can never disagree.

Run it with ``scripts/serve_api.py`` (or ``uvicorn forecast.api:app``).
"""
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import service
from .config import MODEL_VERSION
from .db import connect

app = FastAPI(
    title="FIFA World Cup 2026 — Forecast API",
    version=MODEL_VERSION,
    description="Read-only access to live title/stage probabilities and snapshot history.",
)


def get_conn() -> Iterator[sqlite3.Connection]:
    """Per-request SQLite connection (overridable in tests via dependency_overrides)."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok", "model_version": MODEL_VERSION}


@app.get("/api/runs")
def get_runs(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    """Snapshot run history, newest first (baseline excluded)."""
    return service.runs(conn)


@app.get("/api/snapshot/latest")
def get_latest(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """The latest live snapshot: ranked title/stage probabilities for every team."""
    latest = service.latest(conn)
    if latest is None:
        raise HTTPException(status_code=404, detail="no snapshots yet")
    return latest


@app.get("/api/snapshot/{run_id}")
def get_snapshot(run_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """A specific run's snapshot."""
    try:
        return service.snapshot(conn, run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")


@app.get("/api/team/{team_id}")
def get_team(
    team_id: int,
    run_id: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """A single team's stage path (defaults to the latest snapshot)."""
    try:
        return service.team_path(conn, team_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"not found: {exc}")


@app.get("/api/compare")
def get_compare(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Pre-tournament vs now title comparison per team."""
    return service.pre_vs_now(conn)


@app.get("/api/market")
def get_market(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Model-vs-market home-win probabilities for upcoming priced matches."""
    return service.market_comparison(conn)


@app.get("/api/export/{run_id}")
def get_export(run_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> JSONResponse:
    """Shareable, self-contained snapshot export (downloadable JSON)."""
    try:
        payload = service.export_snapshot(conn, run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    headers = {"Content-Disposition": f'attachment; filename="forecast_{run_id}.json"'}
    return JSONResponse(content=payload, headers=headers)
