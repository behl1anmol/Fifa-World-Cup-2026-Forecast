"""Load the martj42 international-results CSV into ``teams`` and ``matches``.

The loader reads a *local* file under ``datasets/martj42/`` so it is offline and
deterministic; refreshing the file from upstream is the job of
``data_sources.py`` / ``scripts/fetch_data.py``.

Idempotency (architecture acceptance for Step 1):

* teams   — ``INSERT OR IGNORE`` on the unique name (never clobbers a team's
            ``current_elo`` written by a later step).
* matches — ``UPSERT`` on ``(date, home, away, stage)``: re-running never
            duplicates rows, and a fixture whose score flips from ``NA`` to a
            real result (the live-tournament case) is updated in place.

Faithful §5 encoding: the ``result`` column holds a ``"h:a"`` scoreline string
(``NULL`` when unplayed), and venue context (neutral/city/country/tournament)
goes into ``feature_snapshot`` JSON. No data is lost while keeping the schema
exactly as specified.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from .config import MARTJ42_RESULTS_CSV
from .db import row_count

# martj42 columns we expect in results.csv.
_REQUIRED_COLUMNS = {
    "date", "home_team", "away_team", "home_score", "away_score",
    "tournament", "city", "country", "neutral",
}


def _read_results(csv_path: Path) -> pd.DataFrame:
    """Read results.csv, keeping scores as nullable so ``NA`` survives."""
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=True)
    missing = _REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"{csv_path} is missing expected columns: {sorted(missing)}"
        )
    return df


def _unique_team_names(df: pd.DataFrame) -> list[str]:
    """All distinct team names appearing as home or away, sorted."""
    names = pd.concat([df["home_team"], df["away_team"]], ignore_index=True)
    names = names.dropna().astype(str).str.strip()
    names = names[names != ""]
    return sorted(names.unique().tolist())


def _scoreline(home_score, away_score) -> str | None:
    """Return ``"h:a"`` for a played match, or ``None`` when either score is
    missing/``NA`` (an unplayed fixture)."""
    if pd.isna(home_score) or pd.isna(away_score):
        return None
    hs, as_ = str(home_score).strip(), str(away_score).strip()
    if hs == "" or as_ == "" or hs.upper() == "NA" or as_.upper() == "NA":
        return None
    return f"{int(float(hs))}:{int(float(as_))}"


def _feature_snapshot(row: pd.Series) -> str:
    """JSON blob of venue context kept out of the core columns."""
    neutral_raw = str(row.get("neutral", "")).strip().upper()
    return json.dumps(
        {
            "neutral": neutral_raw in ("TRUE", "1", "YES"),
            "city": (row.get("city") if pd.notna(row.get("city")) else None),
            "country": (row.get("country") if pd.notna(row.get("country")) else None),
            "tournament": (
                row.get("tournament") if pd.notna(row.get("tournament")) else None
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def load_teams(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Insert any new teams. Returns the number of newly inserted rows."""
    names = _unique_team_names(df)
    before = row_count(conn, "teams")
    conn.executemany(
        "INSERT OR IGNORE INTO teams (name) VALUES (?)",
        [(n,) for n in names],
    )
    conn.commit()
    return row_count(conn, "teams") - before


def _team_id_map(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}


def load_matches(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Upsert matches. Returns the number of rows inserted-or-updated."""
    ids = _team_id_map(conn)
    rows: list[tuple] = []
    for _, r in df.iterrows():
        home = str(r["home_team"]).strip()
        away = str(r["away_team"]).strip()
        if home not in ids or away not in ids:
            # Should not happen after load_teams, but guard defensively.
            continue
        stage = (str(r["tournament"]).strip() or "Unknown")
        rows.append(
            (
                str(r["date"]).strip(),
                stage,
                ids[home],
                ids[away],
                _scoreline(r["home_score"], r["away_score"]),
                _feature_snapshot(r),
            )
        )
    conn.executemany(
        """
        INSERT INTO matches (date, stage, home, away, result, feature_snapshot)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (date, home, away, stage) DO UPDATE SET
            result           = excluded.result,
            feature_snapshot = excluded.feature_snapshot
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def load(
    conn: sqlite3.Connection, csv_path: Path | str | None = None
) -> dict[str, int]:
    """Run the full load and return summary counts.

    Returns a dict with ``teams`` and ``matches`` total row counts plus how many
    teams were newly inserted and how many match rows were processed.
    """
    path = Path(csv_path) if csv_path is not None else MARTJ42_RESULTS_CSV
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run scripts/fetch_data.py first (or commit the "
            "datasets)."
        )
    df = _read_results(path)
    new_teams = load_teams(conn, df)
    processed = load_matches(conn, df)
    return {
        "teams": row_count(conn, "teams"),
        "matches": row_count(conn, "matches"),
        "new_teams": new_teams,
        "matches_processed": processed,
    }
