"""Central configuration: filesystem paths, data-source URLs, DB location.

Keeping these in one place means the loader, fetcher, tests, and later steps all
agree on where data lives without hard-coding paths throughout the codebase.
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Project root = two levels up from this file (src/forecast/config.py).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATASETS_DIR = PROJECT_ROOT / "datasets"
MARTJ42_DIR = DATASETS_DIR / "martj42"
ELORATINGS_DIR = DATASETS_DIR / "eloratings"
ODDS_API_DIR = DATASETS_DIR / "odds_api"
TRANSFERMARKT_DIR = DATASETS_DIR / "transfermarkt"
FIFA_2026_DIR = DATASETS_DIR / "fifa_2026"

# Default on-disk SQLite database (git-ignored; rebuildable from datasets/).
DB_PATH = Path(os.environ.get("FORECAST_DB", PROJECT_ROOT / "forecast.db"))

# The single martj42 file the Step 1 loader consumes.
MARTJ42_RESULTS_CSV = MARTJ42_DIR / "results.csv"

# ---------------------------------------------------------------------------
# Model bookkeeping
# ---------------------------------------------------------------------------
# Stamped onto prediction snapshots (architecture §5, §7). Bumped as the model
# evolves across build steps.
MODEL_VERSION = "0.1.0-step1-data-layer"

# ---------------------------------------------------------------------------
# Data sources (architecture §6). Each entry is fetched into its own subfolder
# under datasets/ so sources never get interlinked.
# ---------------------------------------------------------------------------
MARTJ42_BASE = "https://raw.githubusercontent.com/martj42/international_results/master"
ELORATINGS_BASE = "https://www.eloratings.net"

# source name -> {dir, files: {local_filename: url}, license, homepage}
DATA_SOURCES: dict[str, dict] = {
    "martj42": {
        "dir": MARTJ42_DIR,
        "homepage": "https://github.com/martj42/international_results",
        "license": "CC0-1.0 (public domain)",
        "files": {
            "results.csv": f"{MARTJ42_BASE}/results.csv",
            "shootouts.csv": f"{MARTJ42_BASE}/shootouts.csv",
            "goalscorers.csv": f"{MARTJ42_BASE}/goalscorers.csv",
            "former_names.csv": f"{MARTJ42_BASE}/former_names.csv",
        },
    },
    "eloratings": {
        "dir": ELORATINGS_DIR,
        "homepage": "https://www.eloratings.net",
        "license": "Free for non-commercial use; see site terms.",
        "files": {
            "2026.tsv": f"{ELORATINGS_BASE}/2026.tsv",
            "en.teams.tsv": f"{ELORATINGS_BASE}/en.teams.tsv",
        },
    },
}

# Hosts of WC2026 — the only teams with genuine home advantage (architecture
# §4.3). Used from Step 4; defined here so the value has one home.
HOST_NATIONS = ("United States", "Canada", "Mexico")
