#!/usr/bin/env python3
"""Refresh raw datasets into datasets/<source>/ (architecture §6).

Usage:
    python scripts/fetch_data.py                # fetch all sources
    python scripts/fetch_data.py --source martj42
    python scripts/fetch_data.py --source eloratings

Network access lives only in forecast.data_sources; this is a thin CLI wrapper.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make ``src/`` importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.config import DATA_SOURCES  # noqa: E402
from forecast.data_sources import fetch_all, fetch_odds_api, fetch_source  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=[*DATA_SOURCES.keys(), "odds_api", "all"],
        default="all",
        help="Which source to fetch (default: all).",
    )
    args = parser.parse_args()

    if args.source == "all":
        fetch_all()
    elif args.source == "odds_api":
        fetch_odds_api()
    else:
        fetch_source(args.source)

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
