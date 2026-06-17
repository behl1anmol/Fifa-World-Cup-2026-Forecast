"""Schema tests: the four §5 tables exist with the expected columns."""
from __future__ import annotations

from forecast.db import EXPECTED_COLUMNS, column_names, table_names


def test_all_tables_created(conn):
    assert set(table_names(conn)) == set(EXPECTED_COLUMNS.keys())


def test_table_columns_match_spec(conn):
    for table, expected in EXPECTED_COLUMNS.items():
        assert column_names(conn, table) == expected, f"columns differ for {table}"


def test_create_schema_is_idempotent(conn):
    # Re-applying the schema must not error or change the table set.
    from forecast.db import create_schema

    create_schema(conn)
    create_schema(conn)
    assert set(table_names(conn)) == set(EXPECTED_COLUMNS.keys())


def test_foreign_keys_enabled(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
