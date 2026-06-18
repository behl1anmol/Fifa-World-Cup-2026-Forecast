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

from .config import (
    BASELINE_RUN_ID,
    BLEND_WEIGHT,
    BLEND_WEIGHTS_3,
    MARKET_BLEND_WEIGHT,
    MODEL_VERSION,
    N_SIMS,
    SIM_SEED,
)
from .loader import _scoreline, _team_id_map
from .market import (
    load_odds_json,
    map_odds_to_matches,
    market_probs_by_match_id,
    resolve_odds_path,
)
from .match_model import fit_match_model
from .ratings import pretournament_elos, replay_history
from .simulator import simulate, write_predictions
from .squad_strength import adjusted_elo_override, squad_elo_adjustments

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
def _played_matches_digest(conn: sqlite3.Connection) -> str:
    """SHA-256 over *every* played match's ``(id, result)``, in id order.

    ``run_update`` rebuilds Elo and refits the model from every played match — not just
    completed WC2026 games — so the forecast moves whenever any played result changes:
    a newly-scored friendly or qualifier picked up by ``--reload``, or a historical
    correction. Hashing the full played-match set (rather than only WC2026 results)
    makes the fingerprint reflect the model's real input state, so such changes correctly
    yield a new ``run_id`` and a new history entry instead of silently overwriting the
    previous snapshot. Cheap relative to the Elo replay the update already performs.
    """
    rows = conn.execute(
        "SELECT id, result FROM matches WHERE result IS NOT NULL ORDER BY id"
    ).fetchall()
    blob = json.dumps([(r["id"], r["result"]) for r in rows],
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _market_digest(market_probs: dict | None):
    """Stable, rounded representation of the active market feature for the fingerprint."""
    if not market_probs:
        return None
    return sorted(
        (int(mid), round(p[0], 6), round(p[1], 6), round(p[2], 6))
        for mid, p in market_probs.items()
    )


def _squad_digest(squad_adj: dict | None):
    """Stable, rounded representation of the active squad-strength nudges."""
    if not squad_adj:
        return None
    return sorted((name, round(delta, 4)) for name, delta in squad_adj.items())


def state_fingerprint(
    conn: sqlite3.Connection,
    n_sims: int = N_SIMS,
    seed: int = SIM_SEED,
    market_probs: dict | None = None,
    squad_adj: dict | None = None,
) -> str:
    """Return a stable hex digest of everything that determines the forecast.

    Hash material is the model version, ``n_sims``, ``seed``, a digest of **all played
    matches** (the model's leak-free fit input), the fixed blend configuration, and any
    active optional inputs — the de-vigged market feature and the squad-strength nudges.
    Two runs produce the same ``run_id`` iff they would produce the same forecast; any
    change to a played result, the odds, the squad data, or a blend weight/version forces
    a new fingerprint (and thus a new history snapshot). First 16 hex chars of SHA-256.
    """
    payload = {
        "model_version": MODEL_VERSION,
        "n_sims": n_sims,
        "seed": seed,
        "matches": _played_matches_digest(conn),
        "blend": {
            "blend_weight": BLEND_WEIGHT,
            "blend_weights_3": list(BLEND_WEIGHTS_3),
            "market_blend_weight": MARKET_BLEND_WEIGHT,
        },
        "market": _market_digest(market_probs),
        "squad": _squad_digest(squad_adj),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def compute_run_id(
    conn: sqlite3.Connection,
    n_sims: int = N_SIMS,
    seed: int = SIM_SEED,
    market_probs: dict | None = None,
    squad_adj: dict | None = None,
) -> str:
    """The ``run_id`` for the current state — identical state ⇒ identical id."""
    return state_fingerprint(conn, n_sims, seed, market_probs, squad_adj)


def live_market_probs(conn: sqlite3.Connection) -> dict | None:
    """De-vigged ``{match_id: (pH,pD,pA)}`` for upcoming priced fixtures, or ``None``.

    Uses **live** odds only (never the committed illustrative sample), so the production
    forecast is market-aware only when real odds have been fetched; with no live odds
    file the live path is byte-identical to before Step 8.
    """
    odds_path, _is_sample = resolve_odds_path(allow_sample=False)
    if odds_path is None:
        return None
    matched = map_odds_to_matches(conn, load_odds_json(odds_path))
    probs = market_probs_by_match_id(matched)
    return probs or None


# ---------------------------------------------------------------------------
# The update loop
# ---------------------------------------------------------------------------
def run_update(
    conn: sqlite3.Connection, n_sims: int = N_SIMS, seed: int = SIM_SEED
) -> dict:
    """Refresh the forecast and persist one snapshot for the current DB state.

    Steps (all deterministic): rebuild point-in-time Elo, refit the blended match model,
    gather any optional Step 8 inputs (de-vigged live market odds as an input-only
    feature; opt-in squad-strength Elo nudges), re-simulate the remaining bracket, and
    upsert one prediction row per team under the deterministic ``run_id``. The optional
    inputs fold into the ``run_id`` fingerprint, so changing odds or squad data yields a
    new history snapshot rather than overwriting the prior one. With no live odds and the
    squad feature disabled (the defaults), behaviour is identical to before Step 8.
    Returns a summary dict with the ``run_id``, the simulator ``result``, the run
    parameters, the replay summary, and which optional inputs were active.
    """
    replayed = replay_history(conn)
    params = fit_match_model(conn)
    market_probs = live_market_probs(conn)
    squad_adj = squad_elo_adjustments(conn)
    elo_override = adjusted_elo_override(conn)
    run_id = compute_run_id(conn, n_sims, seed, market_probs=market_probs, squad_adj=squad_adj)
    result = simulate(
        conn,
        n_sims=n_sims,
        seed=seed,
        params=params,
        elo_override=elo_override,
        market_probs=market_probs,
    )
    write_predictions(conn, result, run_id=run_id)
    return {
        "run_id": run_id,
        "result": result,
        "n_sims": n_sims,
        "seed": seed,
        "replayed": replayed,
        "market_matches": len(market_probs) if market_probs else 0,
        "squad_teams": len(squad_adj) if squad_adj else 0,
    }


def write_baseline_snapshot(
    conn: sqlite3.Connection, n_sims: int = N_SIMS, seed: int = SIM_SEED
) -> dict:
    """Persist the reconstructed **pre-tournament** baseline forecast (architecture §7).

    Simulates the bracket from scratch — ignoring completed 2026 results and using
    each team's reconstructed pre-tournament Elo (``ratings.pretournament_elos``) — and
    writes it under the reserved ``run_id = BASELINE_RUN_ID``. This is the fixed "pre"
    side of the dashboard's "pre-tourney vs now" toggle.

    The match-model *params* are the current leak-free fit (their drift from the
    completed group games is negligible); the snapshot's pre-tournament character comes
    from the pre-tournament Elo plus simulating every group fixture. Idempotent: the
    reserved run_id + ``write_predictions`` upsert mean re-running overwrites in place.
    """
    params = fit_match_model(conn)
    elos = pretournament_elos(conn)
    result = simulate(
        conn,
        n_sims=n_sims,
        seed=seed,
        params=params,
        condition_on_results=False,
        elo_override=elos,
    )
    write_predictions(conn, result, run_id=BASELINE_RUN_ID)
    return {"run_id": BASELINE_RUN_ID, "result": result, "n_sims": n_sims, "seed": seed}


# ---------------------------------------------------------------------------
# Snapshot history (read side; consumed by Step 7)
# ---------------------------------------------------------------------------
def list_runs(conn: sqlite3.Connection) -> list[dict]:
    """Live snapshot runs, newest first: ``[{run_id, model_version, timestamp, n_teams}]``.

    The reserved pre-tournament baseline (``BASELINE_RUN_ID``) is excluded — it is a
    fixed reference, not part of the live forecast history, and must never be mistaken
    for "now" (fetch it explicitly via ``get_snapshot(conn, BASELINE_RUN_ID)``).
    """
    rows = conn.execute(
        """
        SELECT run_id, model_version, MAX(timestamp) AS timestamp, COUNT(*) AS n_teams
        FROM predictions
        WHERE run_id != ?
        GROUP BY run_id
        ORDER BY timestamp DESC, run_id
        """,
        (BASELINE_RUN_ID,),
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
