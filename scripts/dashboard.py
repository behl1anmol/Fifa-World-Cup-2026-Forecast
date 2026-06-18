#!/usr/bin/env python3
"""Launch the Streamlit dashboard (architecture §4.6) — Step 7.

Thin wrapper that hands the dashboard module to Streamlit (which owns the process), so
launching matches the repo's script convention. Equivalent to:

    streamlit run src/forecast/dashboard.py --server.port <PORT>

Usage:
    python scripts/dashboard.py
    python scripts/dashboard.py --port 8600
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from forecast.config import DASHBOARD_PORT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the WC2026 dashboard.")
    parser.add_argument("--port", type=int, default=DASHBOARD_PORT)
    args = parser.parse_args()

    app_path = ROOT / "src" / "forecast" / "dashboard.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.port", str(args.port),
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
