"""Leak-free, point-in-time Elo replay over the match history (architecture §4.2).

This is the DB-driven counterpart to the pure engine in ``elo.py``. It walks every
played match in strict chronological order, keeping an in-memory ``team_id -> rating``
dict. For each match it reads ``elo_before`` from that dict — which by construction
holds only the results of *strictly earlier* matches — *before* computing and writing
back ``elo_after``. That ordering is the leakage guard from §4.2: the rating applied
to match *i* never depends on match *i* or anything after it.

The replay is deterministic and re-runnable: it clears ``ratings_history`` and rebuilds
from scratch, so running it twice on the same data yields byte-identical rows.

``load_reference_elo`` reads the committed eloratings.net snapshot purely for the
sanity-check print (§4.2: "feature and sanity check; self-computed Elo is the
backbone"). It is optional and never fatal.
"""
from __future__ import annotations

import json
import sqlite3

from .config import ELORATINGS_DIR
from .elo import EloConfig, update_ratings


def _parse_scoreline(result: str) -> tuple[int, int]:
    """Split a stored ``"h:a"`` scoreline into integer goals.

    Only ever called for non-NULL results (played matches), per the replay query.
    """
    home, away = result.split(":")
    return int(home), int(away)


def _is_neutral(feature_snapshot: str | None) -> bool:
    """Read the ``neutral`` flag from a match's ``feature_snapshot`` JSON.

    Defaults to ``False`` if the blob is missing or lacks the key — the loader
    always writes it, but guard defensively.
    """
    if not feature_snapshot:
        return False
    try:
        return bool(json.loads(feature_snapshot).get("neutral", False))
    except (json.JSONDecodeError, AttributeError):
        return False


def replay_history(
    conn: sqlite3.Connection, config: EloConfig | None = None
) -> dict:
    """Replay all played matches and populate point-in-time ratings.

    Writes one ``ratings_history`` row per team per match (``elo_before`` /
    ``elo_after`` / ``timestamp`` = match date) and updates ``teams.current_elo``
    to each team's latest rating. Returns a summary dict.
    """
    config = config or EloConfig()

    # Idempotent rebuild: clear prior results so a re-run is a deterministic
    # rebuild, not an append. Done in the same transaction as the rebuild.
    conn.execute("DELETE FROM ratings_history")
    conn.execute("UPDATE teams SET current_elo = NULL")

    # WHERE result IS NOT NULL skips unplayed fixtures; ORDER BY date, id gives a
    # total, deterministic, leak-free order (a team never plays twice on one date).
    rows = conn.execute(
        """
        SELECT id, date, home, away, result, feature_snapshot
        FROM matches
        WHERE result IS NOT NULL
        ORDER BY date, id
        """
    ).fetchall()

    ratings: dict[int, float] = {}
    history_rows: list[tuple] = []

    for row in rows:
        home_id, away_id = row["home"], row["away"]
        # .get(..., default_rating) is the single place a new team is initialized.
        home_before = ratings.get(home_id, config.default_rating)
        away_before = ratings.get(away_id, config.default_rating)

        home_score, away_score = _parse_scoreline(row["result"])
        neutral = _is_neutral(row["feature_snapshot"])

        upd = update_ratings(
            home_before, away_before, home_score, away_score, neutral, config
        )
        ratings[home_id] = upd.home_after
        ratings[away_id] = upd.away_after

        match_id, date = row["id"], row["date"]
        history_rows.append(
            (home_id, match_id, upd.home_before, upd.home_after, date)
        )
        history_rows.append(
            (away_id, match_id, upd.away_before, upd.away_after, date)
        )

    conn.executemany(
        """
        INSERT INTO ratings_history
            (team_id, match_id, elo_before, elo_after, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        history_rows,
    )
    conn.executemany(
        "UPDATE teams SET current_elo = ? WHERE id = ?",
        [(elo, team_id) for team_id, elo in ratings.items()],
    )
    conn.commit()

    return {
        "matches_replayed": len(rows),
        "teams_rated": len(ratings),
        "history_rows": len(history_rows),
    }


def load_reference_elo() -> dict[str, float]:
    """Return ``{team_name: elo}`` from the committed eloratings.net snapshot.

    Reads ``en.teams.tsv`` (col 0 = 2-letter code, col 1 = name) to map codes to
    names, then ``2026.tsv`` (col 2 = code, col 3 = current Elo). Used only for the
    sanity-check print; returns an empty dict if either file is missing.
    """
    teams_path = ELORATINGS_DIR / "en.teams.tsv"
    ratings_path = ELORATINGS_DIR / "2026.tsv"
    if not teams_path.exists() or not ratings_path.exists():
        return {}

    code_to_name: dict[str, str] = {}
    for line in teams_path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip():
            code_to_name[parts[0].strip()] = parts[1].strip()

    reference: dict[str, float] = {}
    for line in ratings_path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) <= 3:
            continue
        code, elo_cell = parts[2].strip(), parts[3].strip()
        name = code_to_name.get(code)
        if not name:
            continue
        try:
            reference[name] = float(elo_cell)
        except ValueError:
            continue
    return reference
