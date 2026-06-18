"""Optional squad-strength feature (architecture §6, §4.3, "Could-have") — Step 8.

Transfermarkt squad values carry terms-of-service exposure, so this feature is
**optional and cached** and the core forecast **never depends on it** (Compliance, §7).
Nothing here scrapes: it reads a cached JSON extract (a git-ignored live cache if
present, else the committed ``*.sample.json`` illustrative file) and turns per-team
squad strength into a small, leak-safe Elo nudge for the *live* 2026 simulation only.

Why a live-only Elo nudge (and not a historical model feature): we only have a *current*
squad snapshot. Applying today's squad values to historical matches would be
anachronistic and leak future information into the backtest, so squad strength is kept
out of the calibration harness entirely — which also guarantees it cannot regress the
Step 5 baseline. The nudge is z-scored across the participating teams and scaled by
``SQUAD_STRENGTH_ELO_SCALE`` (Elo points at +1σ), deliberately small so Elo stays the
strength backbone (§4.2).

Everything degrades gracefully: disabled by config default, a no-op when no cache file
exists, and unmatched team names are simply skipped.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from .config import (
    SQUAD_LIVE_FILE,
    SQUAD_SAMPLE_FILE,
    SQUAD_STRENGTH_ELO_SCALE,
    SQUAD_STRENGTH_ENABLED,
)


def resolve_squad_path(allow_sample: bool = True):
    """Return ``(path, is_sample)``: the cached live file if present, else the sample.

    ``(None, False)`` when neither exists (or ``allow_sample=False`` and no live file),
    so callers can skip the feature. Mirrors :func:`market.resolve_odds_path`.
    """
    if SQUAD_LIVE_FILE.exists():
        return SQUAD_LIVE_FILE, False
    if allow_sample and SQUAD_SAMPLE_FILE.exists():
        return SQUAD_SAMPLE_FILE, True
    return None, False


def load_squad_strength(path) -> dict:
    """Parse a squad-strength JSON cache into ``{team_name: strength}`` (floats).

    Accepts the documented schema ``{"teams": {name: value}}``; a bare ``{name: value}``
    mapping is tolerated too. Non-numeric or metadata keys (``_comment``, ``as_of`` …)
    are ignored. Never raises on a missing team — absence is handled by the caller.
    """
    data = json.loads(open(path, encoding="utf-8").read())
    raw = data.get("teams", data) if isinstance(data, dict) else {}
    out = {}
    for name, value in raw.items():
        if name.startswith("_"):
            continue
        try:
            out[str(name)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _zscore(values: dict) -> dict:
    """Z-score a ``{name: value}`` map; returns all-zeros if there is no spread."""
    xs = list(values.values())
    n = len(xs)
    if n == 0:
        return {}
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    sd = math.sqrt(var)
    if sd <= 0:
        return {k: 0.0 for k in values}
    return {k: (v - mean) / sd for k, v in values.items()}


def squad_elo_adjustments(
    conn: sqlite3.Connection,
    *,
    scale: float = SQUAD_STRENGTH_ELO_SCALE,
    enabled: bool = SQUAD_STRENGTH_ENABLED,
    path=None,
    allow_sample: bool = True,
) -> dict:
    """Return ``{team_name: delta_elo}`` for teams present in both the DB and the cache.

    The z-scored squad strength (over the teams found in the cache) is multiplied by
    ``scale`` to give an additive Elo nudge. Returns ``{}`` when the feature is disabled,
    no cache file exists, or no team name matches — i.e. a clean no-op (the core never
    depends on this). Only names that exist in ``teams`` are returned.
    """
    if not enabled:
        return {}
    if path is None:
        path, _is_sample = resolve_squad_path(allow_sample=allow_sample)
    if path is None or not Path(path).exists():
        return {}
    strengths = load_squad_strength(path)
    if not strengths:
        return {}
    db_names = {r["name"] for r in conn.execute("SELECT name FROM teams")}
    z = _zscore(strengths)
    return {name: scale * zv for name, zv in z.items() if name in db_names}


def adjusted_elo_override(
    conn: sqlite3.Connection,
    *,
    scale: float = SQUAD_STRENGTH_ELO_SCALE,
    enabled: bool = SQUAD_STRENGTH_ENABLED,
    path=None,
    allow_sample: bool = True,
) -> dict | None:
    """Full ``{team_name: current_elo + squad_delta}`` override for the live simulator.

    Reads every team's ``current_elo`` and applies the squad Elo nudge on top. Returns
    ``None`` when the feature contributes nothing (disabled / no cache / no match), so
    the simulator falls back to plain ``current_elo``. Teams without a squad value keep
    their Elo unchanged.
    """
    deltas = squad_elo_adjustments(
        conn, scale=scale, enabled=enabled, path=path, allow_sample=allow_sample
    )
    if not deltas:
        return None
    override = {}
    for r in conn.execute("SELECT name, current_elo FROM teams"):
        if r["current_elo"] is None:
            continue
        override[r["name"]] = float(r["current_elo"]) + deltas.get(r["name"], 0.0)
    return override or None
