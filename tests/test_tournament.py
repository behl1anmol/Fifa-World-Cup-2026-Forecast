"""Correctness gate for the tournament structure (architecture §4.4).

The Round-of-32 third-placed-team allocation is the highest bug-risk piece in the
app: a silent error corrupts every title probability. These tests validate the
committed FIFA Annex C table (495 combinations) and the bracket topology before any
simulator output is trusted.
"""
from __future__ import annotations

from itertools import combinations

from forecast.tournament import (
    BRACKET,
    GROUP_LETTERS,
    R32_MATCHES,
    THIRD_SLOT_ALLOWED,
    THIRD_SLOT_COLUMN_ORDER,
    derive_groups_from_fixtures,
    load_groups,
    load_third_place_table,
    third_place_assignment,
)

# FIFA's literal row 1: thirds from E,F,G,H,I,J,K,L map to these host slots.
FIFA_ROW1 = {"A": "E", "B": "J", "D": "I", "E": "F", "G": "H", "I": "G", "K": "L", "L": "K"}


def test_third_place_table_has_495_rows():
    assert len(load_third_place_table()) == 495


def test_every_row_is_a_constraint_respecting_bijection():
    for n, row in enumerate(load_third_place_table(), 1):
        slots = row["slots"]
        assert sorted(slots) == sorted(THIRD_SLOT_COLUMN_ORDER), f"row {n}: wrong host slots"
        sources = list(slots.values())
        assert len(set(sources)) == 8, f"row {n}: not a bijection ({sources})"
        # The eight advancing thirds must equal the row's declared set.
        assert set(sources) == set(row["thirds"]), f"row {n}: thirds/slots mismatch"
        for host, src in slots.items():
            assert src in THIRD_SLOT_ALLOWED[host], f"row {n}: {src} illegal for slot {host}"


def test_table_covers_all_495_group_combinations():
    seen = {frozenset(row["thirds"]) for row in load_third_place_table()}
    expected = {frozenset(c) for c in combinations(GROUP_LETTERS, 8)}
    assert seen == expected


def test_spot_check_against_fifa_published_rows():
    # Row 1 (the canonical published example) must match FIFA exactly.
    assert third_place_assignment(frozenset("EFGHIJKL")) == FIFA_ROW1
    # A second, different combination must also resolve to a valid bijection.
    other = third_place_assignment(frozenset("ABCDEFGH"))
    assert set(other.values()) == set("ABCDEFGH")
    assert sorted(other) == sorted(THIRD_SLOT_COLUMN_ORDER)


def test_bracket_topology_is_consistent():
    # R32: 16 matches, 32 distinct qualifier slots (12 W + 12 R + 8 thirds).
    assert len(R32_MATCHES) == 16
    specs = [s for match in R32_MATCHES.values() for s in match]
    w = sorted(g for kind, g in specs if kind == "W")
    r = sorted(g for kind, g in specs if kind == "R")
    thirds = sorted(g for kind, g in specs if kind == "3")
    assert w == list(GROUP_LETTERS)  # every group's winner used once
    assert r == list(GROUP_LETTERS)  # every group's runner-up used once
    assert thirds == sorted(THIRD_SLOT_COLUMN_ORDER)  # the 8 third-host slots
    assert len(specs) == 32

    # Later rounds reference real, earlier match numbers and halve each round.
    assert len(BRACKET) == 8 + 4 + 2 + 1
    known = set(R32_MATCHES)
    for match_no, (a, b) in BRACKET.items():
        assert a in known and b in known, f"match {match_no} references unknown source"
        known.add(match_no)


def test_groups_json_partitions_into_twelve_fours():
    groups = load_groups()
    assert sorted(groups) == list(GROUP_LETTERS)
    flat = [t for v in groups.values() for t in v]
    assert all(len(v) == 4 for v in groups.values())
    assert len(set(flat)) == 48


def test_derive_groups_recovers_groupsjson(conn):
    """Building round-robin fixtures from groups.json and deriving them back must
    reproduce the same 12 groups — validates the fixture-based group recovery used
    to cross-check the labelled data against the repo's own fixtures."""
    groups = load_groups()
    # minimal teams + round-robin group fixtures
    for letter in GROUP_LETTERS:
        for team in groups[letter]:
            conn.execute("INSERT OR IGNORE INTO teams (name) VALUES (?)", (team,))
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}
    for letter in GROUP_LETTERS:
        for a, b in combinations(groups[letter], 2):
            conn.execute(
                "INSERT INTO matches (date, stage, home, away) VALUES "
                "('2026-06-20', 'FIFA World Cup', ?, ?)",
                (ids[a], ids[b]),
            )
    conn.commit()
    derived = {frozenset(c) for c in derive_groups_from_fixtures(conn)}
    expected = {frozenset(v) for v in groups.values()}
    assert derived == expected
