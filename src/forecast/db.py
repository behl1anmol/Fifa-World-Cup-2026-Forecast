"""SQLite schema and connection helpers (architecture §5).

Stdlib ``sqlite3`` is used deliberately over an ORM: the data model is four
small tables and the project values "fewest moving parts" (§3.1).

The four tables mirror §5 exactly:

* ``teams``           — one row per national team
* ``matches``         — historical + WC2026 fixtures
* ``ratings_history`` — point-in-time Elo (populated in Step 2)
* ``predictions``     — simulation snapshots (populated from Step 3/6)

Only ``teams`` and ``matches`` are populated in Step 1; the other two are
created now so the schema is complete and stable.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import DB_PATH

# ---------------------------------------------------------------------------
# Schema. Kept faithful to architecture §5. See loader.py for how the §5
# ``result`` field encodes a scoreline losslessly.
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    confederation TEXT,
    current_elo   REAL
);

CREATE TABLE IF NOT EXISTS matches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT NOT NULL,
    stage            TEXT NOT NULL,
    home             INTEGER NOT NULL REFERENCES teams(id),
    away             INTEGER NOT NULL REFERENCES teams(id),
    result           TEXT,             -- "h:a" scoreline; NULL if not yet played
    feature_snapshot TEXT,             -- JSON: neutral, city, country, tournament
    UNIQUE (date, home, away, stage)
);

CREATE TABLE IF NOT EXISTS ratings_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id    INTEGER NOT NULL REFERENCES teams(id),
    match_id   INTEGER NOT NULL REFERENCES matches(id),
    elo_before REAL NOT NULL,
    elo_after  REAL NOT NULL,
    timestamp  TEXT NOT NULL,
    UNIQUE (team_id, match_id)
);

CREATE TABLE IF NOT EXISTS predictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL,
    model_version       TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    team_id             INTEGER NOT NULL REFERENCES teams(id),
    stage_probabilities TEXT,          -- JSON: {stage: prob}
    title_prob          REAL,
    UNIQUE (run_id, team_id)
);

-- Indexes that matter for the update loop and per-team queries.
CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date);
CREATE INDEX IF NOT EXISTS idx_ratings_team ON ratings_history(team_id);
CREATE INDEX IF NOT EXISTS idx_predictions_run ON predictions(run_id);
"""

# Table -> ordered column names, used by create_schema's verification and tests.
EXPECTED_COLUMNS = {
    "teams": ["id", "name", "confederation", "current_elo"],
    "matches": [
        "id", "date", "stage", "home", "away", "result", "feature_snapshot",
    ],
    "ratings_history": [
        "id", "team_id", "match_id", "elo_before", "elo_after", "timestamp",
    ],
    "predictions": [
        "id", "run_id", "model_version", "timestamp", "team_id",
        "stage_probabilities", "title_prob",
    ],
}


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with sensible pragmas.

    Foreign keys are enabled (off by default in sqlite3); row factory is set so
    callers can use column names.
    """
    path = Path(db_path) if db_path is not None else DB_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all four tables (idempotent via ``IF NOT EXISTS``)."""
    conn.executescript(SCHEMA)
    conn.commit()


def table_names(conn: sqlite3.Connection) -> list[str]:
    """Return the user tables present in the database."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return the column names of ``table`` in declared order."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def row_count(conn: sqlite3.Connection, table: str) -> int:
    """Return the number of rows in ``table``."""
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
