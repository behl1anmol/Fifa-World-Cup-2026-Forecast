"""Shared read/service layer for the serving tier (architecture §4.6) — Step 7.

A single, framework-agnostic place that turns the persisted forecast into JSON-ready
dicts. Both the FastAPI app (``api.py``) and the Streamlit dashboard (``dashboard.py``)
import these functions directly, so the two front-ends never drift and the dashboard
works without a running API server.

Everything here is **read-only** over a ``sqlite3`` connection. The heavy lifting lives
in the Step 6 helpers (``update_loop.list_runs`` / ``get_snapshot`` / ``latest_snapshot``)
and the market plumbing (``market`` + ``calibration.live_comparison``); this module just
composes and shapes them.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .config import BASELINE_RUN_ID, HOST_NATIONS
from .market import load_odds_json, map_odds_to_matches, resolve_odds_path
from .match_model import MatchModelParams, fit_match_model, predict
from .update_loop import get_snapshot, latest_snapshot, list_runs

# Stage keys in bracket order, re-exported for front-ends building path charts.
STAGE_ORDER = ("r32", "r16", "qf", "sf", "final", "title")


# ---------------------------------------------------------------------------
# Snapshot reads
# ---------------------------------------------------------------------------
def runs(conn: sqlite3.Connection) -> list[dict]:
    """Live snapshot runs, newest first (baseline excluded)."""
    return list_runs(conn)


def latest(conn: sqlite3.Connection) -> dict | None:
    """The most recent live snapshot, or ``None`` if no run exists yet."""
    return latest_snapshot(conn)


def snapshot(conn: sqlite3.Connection, run_id: str) -> dict:
    """One run as ``{run_id, teams:[...]}``. Raises ``KeyError`` if the run is unknown."""
    teams = get_snapshot(conn, run_id)
    if not teams:
        raise KeyError(run_id)
    return {"run_id": run_id, "teams": teams}


def team_path(
    conn: sqlite3.Connection, team_id: int, run_id: str | None = None
) -> dict:
    """One team's stage path for a run (default: the latest live snapshot).

    Returns ``{team_id, name, run_id, timestamp, stage_probabilities}``. Raises
    ``KeyError`` if there is no snapshot, or the team is absent from it.
    """
    if run_id is None:
        head = latest_snapshot(conn)
        if head is None:
            raise KeyError("no snapshots")
        run_id, timestamp = head["run_id"], head["timestamp"]
        teams = head["teams"]
    else:
        timestamp = _run_timestamp(conn, run_id)
        teams = get_snapshot(conn, run_id)
    row = next((t for t in teams if t["team_id"] == team_id), None)
    if row is None:
        raise KeyError(team_id)
    return {
        "team_id": team_id,
        "name": row["name"],
        "run_id": run_id,
        "timestamp": timestamp,
        "stage_probabilities": row["stage_probabilities"],
    }


def _run_timestamp(conn: sqlite3.Connection, run_id: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(timestamp) AS ts FROM predictions WHERE run_id = ?", (run_id,)
    ).fetchone()
    return row["ts"] if row else None


# ---------------------------------------------------------------------------
# Pre-tournament baseline & comparison
# ---------------------------------------------------------------------------
def baseline(conn: sqlite3.Connection) -> dict | None:
    """The reserved pre-tournament baseline snapshot, or ``None`` if not generated."""
    teams = get_snapshot(conn, BASELINE_RUN_ID)
    if not teams:
        return None
    return {
        "run_id": BASELINE_RUN_ID,
        "timestamp": _run_timestamp(conn, BASELINE_RUN_ID),
        "teams": teams,
    }


def pre_vs_now(conn: sqlite3.Connection) -> dict:
    """Per-team pre-tournament-vs-now title comparison for the dashboard toggle.

    Returns ``{has_baseline, latest_run_id, rows:[{team_id, name, baseline_title,
    now_title, delta}]}`` sorted by ``now_title`` descending. When the baseline is
    missing, ``baseline_title``/``delta`` are ``None`` and ``has_baseline`` is False so
    the UI can degrade gracefully.
    """
    now = latest_snapshot(conn)
    if now is None:
        return {"has_baseline": False, "latest_run_id": None, "rows": []}
    base = baseline(conn)
    base_by_id = (
        {t["team_id"]: t["title_prob"] for t in base["teams"]} if base else {}
    )
    rows = []
    for t in now["teams"]:
        b = base_by_id.get(t["team_id"])
        rows.append(
            {
                "team_id": t["team_id"],
                "name": t["name"],
                "baseline_title": b,
                "now_title": t["title_prob"],
                "delta": (t["title_prob"] - b) if b is not None else None,
            }
        )
    rows.sort(key=lambda r: r["now_title"], reverse=True)
    return {
        "has_baseline": base is not None,
        "latest_run_id": now["run_id"],
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Market comparison
# ---------------------------------------------------------------------------
def market_comparison(
    conn: sqlite3.Connection, params: MatchModelParams | None = None
) -> dict:
    """Model-vs-market home-win probabilities for every priced WC match we can map.

    Returns ``{has_odds, is_sample, rows:[...]}`` where each row carries the model's and
    market's home-win prob, the model's bullishness (model − market), the fixture date,
    and the stored ``result`` (``"h:a"`` or ``None`` for upcoming). Both completed and
    upcoming priced matches are included so the comparison is visible even when only the
    sample odds (which cover early group games) are present. ``is_sample`` flags the
    committed sample so the UI can caveat it. Never raises: no odds file → empty result.
    """
    path, is_sample = resolve_odds_path()
    if path is None:
        return {"has_odds": False, "is_sample": False, "rows": []}
    if params is None:
        params = fit_match_model(conn)
    elo = {r["name"]: r["current_elo"] for r in
           conn.execute("SELECT name, current_elo FROM teams")}
    hosts = set(HOST_NATIONS)
    rows = []
    for od in map_odds_to_matches(conn, load_odds_json(path)):
        eh, ea = elo.get(od["home"]), elo.get(od["away"])
        if eh is None or ea is None:
            continue
        pf = predict(params, eh, ea, od["home"] in hosts)
        rows.append({
            "home": od["home"],
            "away": od["away"],
            "date": od["date"],
            "model_home": float(pf[0]),
            "market_home": od["pH"],
            "bullish_home": float(pf[0]) - od["pH"],
            "result": od["result"],
        })
    rows.sort(key=lambda r: r["date"])
    return {"has_odds": True, "is_sample": is_sample, "rows": rows}


# ---------------------------------------------------------------------------
# Shareable export
# ---------------------------------------------------------------------------
def export_snapshot(conn: sqlite3.Connection, run_id: str) -> dict:
    """A self-contained, JSON-serialisable snapshot for the shareable export.

    Returns ``{run_id, model_version, timestamp, generated_at, teams:[...]}``. Raises
    ``KeyError`` for an unknown run_id.
    """
    teams = get_snapshot(conn, run_id)
    if not teams:
        raise KeyError(run_id)
    meta = conn.execute(
        "SELECT model_version, MAX(timestamp) AS ts FROM predictions WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return {
        "run_id": run_id,
        "model_version": meta["model_version"],
        "timestamp": meta["ts"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "teams": teams,
    }
