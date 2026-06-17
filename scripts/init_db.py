#!/usr/bin/env python3
"""Create the SQLite schema (architecture §5).

Usage:
    python scripts/init_db.py                 # uses default forecast.db
    FORECAST_DB=/tmp/x.db python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.config import DB_PATH  # noqa: E402
from forecast.db import connect, create_schema, table_names  # noqa: E402


def main() -> int:
    conn = connect()
    create_schema(conn)
    tables = table_names(conn)
    conn.close()
    print(f"Schema created at {DB_PATH}")
    print(f"Tables: {', '.join(tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
