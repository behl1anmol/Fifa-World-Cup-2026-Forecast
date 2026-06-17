"""Shared pytest fixtures. No network: everything uses local fixtures + temp DB."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make ``src/`` importable for the test session.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.db import connect, create_schema  # noqa: E402

# A tiny, hand-checkable results.csv mirroring the martj42 schema. Six rows,
# five distinct teams, one unplayed (NA) fixture for the upsert test.
FIXTURE_CSV = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2026-06-11,Mexico,South Africa,2,0,FIFA World Cup,Mexico City,Mexico,FALSE
2026-06-12,Canada,Bosnia and Herzegovina,1,1,FIFA World Cup,Toronto,Canada,FALSE
2026-06-13,Brazil,Morocco,1,1,FIFA World Cup,East Rutherford,United States,TRUE
2026-06-14,Mexico,Canada,3,1,FIFA World Cup,Guadalajara,Mexico,FALSE
2026-06-27,Brazil,South Africa,NA,NA,FIFA World Cup,Miami Gardens,United States,TRUE
1990-07-08,Argentina,Germany,0,1,FIFA World Cup,Rome,Italy,TRUE
"""

# Distinct team names appearing above.
EXPECTED_TEAMS = {
    "Mexico", "South Africa", "Canada", "Bosnia and Herzegovina",
    "Brazil", "Morocco", "Argentina", "Germany",
}


@pytest.fixture()
def fixture_csv(tmp_path: Path) -> Path:
    path = tmp_path / "results.csv"
    path.write_text(FIXTURE_CSV, encoding="utf-8")
    return path


@pytest.fixture()
def conn():
    """An in-memory database with the schema applied."""
    c = connect(":memory:")
    create_schema(c)
    yield c
    c.close()
