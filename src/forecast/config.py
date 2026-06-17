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
MODEL_VERSION = "0.4.0-step4-dixoncoles"

# ---------------------------------------------------------------------------
# Monte Carlo simulator (architecture §4.4, §7)
# ---------------------------------------------------------------------------
N_SIMS = 50_000          # remaining-bracket simulations per run (§4.4)
SIM_SEED = 20_260_617    # default RNG seed; reproducible runs (§7)

# ---------------------------------------------------------------------------
# Match-outcome model (architecture §4.3, decision #8) — Step 4
# ---------------------------------------------------------------------------
# Goals follow the published FIFA-tournament form λ = exp(β₀ + β₁·EloDiff), with a
# Dixon-Coles low-score (τ/ρ) correction and a host-only home term layered on top
# (Gilch & Müller 2018). These constants are the *seed* defaults used by
# ``MatchModelParams.default()`` so the simulator and tests can run without a fit;
# ``fit_match_model`` overwrites them from historical data (leak-free, via
# ``ratings_history.elo_before``).
#
# Seed scale: β₀ = ln(BASE_GOALS / 2) puts an even neutral match at BASE_GOALS/2
# goals per side; ELO_GOAL_SCALE_LOG is β₁ in log-rate space per Elo point.
BASE_GOALS = 2.6                 # expected total goals in a neutral, even game
ELO_GOAL_SCALE_LOG = 0.0017      # β₁: log-goal supremacy per Elo point (seed)
HOST_HOME_GOALS_LOG = 0.20       # additive log-rate home term for host nations (seed)

# Dixon-Coles low-score correction and scoreline support.
DC_RHO = -0.05                   # τ correlation parameter (seed; re-fit in Step 4)
DC_MAX_GOALS = 10                # scoreline matrix is (0..DC_MAX_GOALS)²

# Elo-implied outcome: draw curve pD = DRAW_BASE · exp(-|ΔElo| / DRAW_DECAY).
DRAW_BASE = 0.27                 # draw probability for an even matchup (seed)
DRAW_DECAY = 350.0               # Elo-points scale over which draws decay (seed)

# Fixed-weight blend of the Dixon-Coles outcome with the Elo-implied outcome
# (decision #8: fixed weight, not learned stacking). Configurable; Step 5 may tune.
BLEND_WEIGHT = 0.5               # weight on the Dixon-Coles outcome in [0, 1]

# Time-decay half-life (days) for weighting historical matches in the fit, so
# recent international form counts more (Dixon-Coles down-weighting idea).
DC_FIT_HALF_LIFE_DAYS = 365.0 * 8.0  # ~8 years

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
