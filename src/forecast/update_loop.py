"""In-tournament update loop and prediction snapshots (architecture §3.3, §4, §5, §7).

This is the operational heart of the live app. As each WC2026 match finishes, the
forecast must refresh and a dated snapshot must be written so the product can show
"pre-tourney vs now", a per-team history, and a shareable, auditable trail.

The loop is pure orchestration over already-tested components — it adds no model
logic:

    ingest_result  → flip an existing fixture from NULL to a "h:a" score
    run_update     → replay Elo → fit model → simulate → write one snapshot

Two cross-cutting guarantees from §7 are enforced here:

* **Idempotency.** Every step is deterministic: ``replay_history`` is a full leak-free
  rebuild, ``fit_match_model`` is a deterministic fit, ``simulate`` is seeded, and
  ``write_predictions`` upserts on ``(run_id, team_id)``. The ``run_id`` itself is a
  *deterministic fingerprint of the tournament state* (``state_fingerprint``), so
  re-running on the same state overwrites the same snapshot rather than piling up
  duplicates, while a newly completed result yields a new ``run_id`` and a new history
  entry.
* **Reproducibility.** Same DB state + same seed ⇒ byte-identical probabilities and the
  same ``run_id``.

The snapshot *read* helpers (``list_runs`` / ``get_snapshot`` / ``latest_snapshot``)
live here for now; Step 7's serving layer is their first consumer and may relocate them
to a dedicated module if the API surface grows.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .config import MODEL_VERSION, N_SIMS, SIM_SEED
from .loader import _scoreline, _team_id_map
from .match_model import fit_match_model
from .ratings import replay_history
from .simulator import simulate, write_predictions

# WC2026 fixtures are tagged with this stage and fall on/after this date — the same
# predicate the simulator uses to find the live bracket (§4.4).
WC_STAGE = "FIFA World Cup"
WC_DATE_FLOOR = "2026-01-01"


# ---------------------------------------------------------------------------
# Ingest a newly completed result
# ---------------------------------------------------------------------------
def ingest_result(
    conn: sqlite3.Connection,
    date: str,
    home: str,
    away: str,
    home_score: int,
    away_score: int,
) -> bool:
    """Flip an existing WC2026 fixture from NULL to a ``"h:a"`` score, in place.

    The fixture is located by ``(date, home, away, stage='FIFA World Cup')`` — the row
    already exists, loaded with ``result = NULL`` (the future-fixture case). Only the
    ``result`` column changes; ``feature_snapshot`` (neutral/venue) was set at load and
    is left intact. The write is idempotent: re-ingesting the same score is a no-op.

    Returns ``True`` when a matching fixture was updated, ``False`` when no such fixture
    exists (so a typo cannot silently create a phantom match). Raises ``ValueError`` if
    either team name is unknown.
    """
    ids = _team_id_map(conn)
    if home not in ids:
        raise ValueError(f"Unknown home team: {home!r}")
    if away not in ids:
        raise ValueError(f"Unknown away team: {away!r}")

    score = _scoreline(home_score, away_score)
    if score is None:
        raise ValueError(f"Invalid score: {home_score!r}:{away_score!r}")

    cur = conn.execute(
        """
        UPDATE matches SET result = ?
        WHERE date = ? AND home = ? AND away = ? AND stage = ?
        """,
        (score, date, ids[home], ids[away], WC_STAGE),
    )
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Deterministic, state-fingerprinted run_id
# ---------------------------------------------------------------------------
def _completed_wc_results(conn: sqlite3.Connection) -> list[tuple]:
    """The played WC2026 results, in a stable order. The only moving part of state."""
    rows = conn.execute(
        """
        SELECT date, home, away, result
        FROM matches
        WHERE stage = ? AND date >= ? AND result IS NOT NULL
        ORDER BY date, home, away
        """,
        (WC_STAGE, WC_DATE_FLOOR),
    ).fetchall()
    return [(r["date"], r["home"], r["away"], r["result"]) for r in rows]


def state_fingerprint(
    conn: sqlite3.Connection, n_sims: int = N_SIMS, seed: int = SIM_SEED
) -> str:
    """Return a stable hex digest of everything that determines the forecast.

    Hash material is the model version, ``n_sims``, ``seed``, and the sorted list of
    completed WC2026 results. Historical (pre-2026) matches are static, so those
    completed results are the only thing that changes during the tournament — hashing
    them captures "the same state". A model-version or seed change correctly forces a
    new fingerprint. Returns the first 16 hex chars of a SHA-256 digest.
    """
    payload = {
        "model_version": MODEL_VERSION,
        "n_sims": n_sims,
        "seed": seed,
        "results": _completed_wc_results(conn),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def compute_run_id(
    conn: sqlite3.Connection, n_sims: int = N_SIMS, seed: int = SIM_SEED
) -> str:
    """The ``run_id`` for the current state — identical state ⇒ identical id."""
    return state_fingerprint(conn, n_sims, seed)


# ---------------------------------------------------------------------------
# The update loop
# ---------------------------------------------------------------------------
def run_update(
    conn: sqlite3.Connection, n_sims: int = N_SIMS, seed: int = SIM_SEED
) -> dict:
    """Refresh the forecast and persist one snapshot for the current DB state.

    Steps (all deterministic): rebuild point-in-time Elo, refit the blended match model,
    re-simulate the remaining bracket, and upsert one prediction row per team under the
    deterministic ``run_id``. Returns a summary dict with the ``run_id``, the simulator
    ``result``, the run parameters, and the replay summary.
    """
    replayed = replay_history(conn)
    params = fit_match_model(conn)
    run_id = compute_run_id(conn, n_sims, seed)
    result = simulate(conn, n_sims=n_sims, seed=seed, params=params)
    write_predictions(conn, result, run_id=run_id)
    return {
        "run_id": run_id,
        "result": result,
        "n_sims": n_sims,
        "seed": seed,
        "replayed": replayed,
    }


# ---------------------------------------------------------------------------
# Snapshot history (read side; consumed by Step 7)
# ---------------------------------------------------------------------------
def list_runs(conn: sqlite3.Connection) -> list[dict]:
    """All snapshot runs, newest first: ``[{run_id, model_version, timestamp, n_teams}]``."""
    rows = conn.execute(
        """
        SELECT run_id, model_version, MAX(timestamp) AS timestamp, COUNT(*) AS n_teams
        FROM predictions
        GROUP BY run_id
        ORDER BY timestamp DESC, run_id
        """
    ).fetchall()
    return [
        {
            "run_id": r["run_id"],
            "model_version": r["model_version"],
            "timestamp": r["timestamp"],
            "n_teams": r["n_teams"],
        }
        for r in rows
    ]


def get_snapshot(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """One run's per-team rows, sorted by title probability descending.

    Returns ``[{team_id, name, title_prob, stage_probabilities}]`` with the JSON stage
    map parsed back into a dict.
    """
    rows = conn.execute(
        """
        SELECT p.team_id, t.name, p.title_prob, p.stage_probabilities
        FROM predictions p
        JOIN teams t ON t.id = p.team_id
        WHERE p.run_id = ?
        ORDER BY p.title_prob DESC, t.name
        """,
        (run_id,),
    ).fetchall()
    return [
        {
            "team_id": r["team_id"],
            "name": r["name"],
            "title_prob": r["title_prob"],
            "stage_probabilities": json.loads(r["stage_probabilities"]),
        }
        for r in rows
    ]


def latest_snapshot(conn: sqlite3.Connection) -> dict | None:
    """The most recent run as ``{run_id, model_version, timestamp, teams}``; ``None`` if empty."""
    runs = list_runs(conn)
    if not runs:
        return None
    head = runs[0]
    return {
        "run_id": head["run_id"],
        "model_version": head["model_version"],
        "timestamp": head["timestamp"],
        "teams": get_snapshot(conn, head["run_id"]),
    }
