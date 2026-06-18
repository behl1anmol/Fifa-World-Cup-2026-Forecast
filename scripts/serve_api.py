#!/usr/bin/env python3
"""Serve the FastAPI forecast API with uvicorn (architecture §4.6) — Step 7.

Read-only endpoints over the persisted forecast: latest probabilities, a single team's
stage path, the snapshot history, the pre-vs-now comparison, the market comparison, and
a shareable export. The DB is whatever ``FORECAST_DB`` points at (default project DB).

Usage:
    python scripts/serve_api.py
    python scripts/serve_api.py --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uvicorn  # noqa: E402

from forecast.api import app  # noqa: E402
from forecast.config import API_HOST, API_PORT, DB_PATH  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the WC2026 forecast API.")
    parser.add_argument("--host", default=API_HOST)
    parser.add_argument("--port", type=int, default=API_PORT)
    args = parser.parse_args()

    print(f"Database : {DB_PATH}")
    print(f"API      : http://{args.host}:{args.port}  (docs at /docs)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
