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
MODEL_VERSION = "0.3.0-step3-simulator"

# ---------------------------------------------------------------------------
# Monte Carlo simulator (architecture §4.4, §7)
# ---------------------------------------------------------------------------
N_SIMS = 50_000          # remaining-bracket simulations per run (§4.4)
SIM_SEED = 20_260_617    # default RNG seed; reproducible runs (§7)

# Step-3 placeholder goal model: map Elo to a pair of Poisson scoring rates so a
# single process yields win/draw/loss *and* scorelines (needed for group
# tiebreakers and proportional extra time). Step 4 replaces this with Dixon-Coles;
# BASE_GOALS / ELO_GOAL_SCALE are not yet calibrated (that is Step 5).
BASE_GOALS = 2.6         # expected total goals in a neutral, evenly-matched game
ELO_GOAL_SCALE = 0.0035  # goal supremacy per Elo point of difference

# ---------------------------------------------------------------------------
# Elo model parameters (architecture §4.2)
# ---------------------------------------------------------------------------
# World Football Elo family defaults. The engine exposes exactly the knobs the
# architecture enumerates — K, home advantage, optional margin-of-victory — and
# deliberately no tournament-importance weighting (single K). EloConfig pulls its
# defaults from here so the script, engine, and DB replay all agree.
ELO_DEFAULT_RATING = 1500.0   # every team's rating before its first match
ELO_K = 40.0                  # update step size
ELO_HOME_ADVANTAGE = 100.0    # rating points added to a non-neutral home side
ELO_USE_MOV = True            # scale updates by goal-difference index

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
