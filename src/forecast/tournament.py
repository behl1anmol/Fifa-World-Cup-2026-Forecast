"""2026 World Cup tournament structure (architecture §4.4).

Static bracket topology + loaders for the committed FIFA data artifacts. None of
this is sampled — it is the fixed scaffolding the Monte Carlo simulator pours
results through. The data lives in ``datasets/fifa_2026/`` (fetched/validated by
``scripts/fetch_fifa_structure.py``) so the simulator and tests stay offline.

The Round-of-32 third-placed-team assignment encoded here is, per §4.4, the highest
bug-risk piece in the app: it uses FIFA's *literal* 495-combination table (Annex C).
The matching is provably non-unique (combinations admit 3–214 valid matchings), so
no algorithm can stand in for FIFA's published choices — the table is loaded as data
and unit-tested as a correctness gate.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import FIFA_2026_DIR

GROUP_LETTERS = "ABCDEFGHIJKL"

# ---------------------------------------------------------------------------
# Round of 32 — 16 matches. Each slot is a (kind, group) pair:
#   ("W", X) winner of group X · ("R", X) runner-up of X ·
#   ("3", H) the third-placed team allocated to the slot *hosted by winner H*.
# Verified against FIFA's published bracket: 12 winners + 12 runners-up + 8 thirds
# = 32 distinct qualifiers.
# ---------------------------------------------------------------------------
R32_MATCHES: dict[int, tuple[tuple[str, str], tuple[str, str]]] = {
    73: (("R", "A"), ("R", "B")),
    74: (("W", "E"), ("3", "E")),
    75: (("W", "F"), ("R", "C")),
    76: (("W", "C"), ("R", "F")),
    77: (("W", "I"), ("3", "I")),
    78: (("R", "E"), ("R", "I")),
    79: (("W", "A"), ("3", "A")),
    80: (("W", "L"), ("3", "L")),
    81: (("W", "D"), ("3", "D")),
    82: (("W", "G"), ("3", "G")),
    83: (("R", "K"), ("R", "L")),
    84: (("W", "H"), ("R", "J")),
    85: (("W", "B"), ("3", "B")),
    86: (("W", "J"), ("R", "H")),
    87: (("W", "K"), ("3", "K")),
    88: (("R", "D"), ("R", "G")),
}

# Later rounds reference the winners of two earlier matches.
BRACKET: dict[int, tuple[int, int]] = {
    # Round of 16
    89: (74, 77), 90: (73, 75), 91: (76, 78), 92: (79, 80),
    93: (83, 84), 94: (81, 82), 95: (86, 88), 96: (85, 87),
    # Quarterfinals
    97: (89, 90), 98: (93, 94), 99: (91, 92), 100: (95, 96),
    # Semifinals
    101: (97, 98), 102: (99, 100),
    # Final
    104: (101, 102),
}

R32_MATCH_NOS = tuple(R32_MATCHES)              # 73..88
R16_MATCH_NOS = (89, 90, 91, 92, 93, 94, 95, 96)
QF_MATCH_NOS = (97, 98, 99, 100)
SF_MATCH_NOS = (101, 102)
FINAL_MATCH_NO = 104

# The eight R32 slots that take a third-placed team, keyed by the *host winner's*
# group letter, with the third-place groups FIFA permits in each (used to validate
# the loaded combination table). These match the column order of FIFA's table.
THIRD_SLOT_COLUMN_ORDER = ("A", "B", "D", "E", "G", "I", "K", "L")
THIRD_SLOT_ALLOWED: dict[str, frozenset[str]] = {
    "A": frozenset("CEFHI"),
    "B": frozenset("EFGIJ"),
    "D": frozenset("BEFIJ"),
    "E": frozenset("ABCDF"),
    "G": frozenset("AEHIJ"),
    "I": frozenset("CDFGH"),
    "K": frozenset("DEIJL"),
    "L": frozenset("EHIJK"),
}

GROUPS_PATH = FIFA_2026_DIR / "groups.json"
THIRD_PLACE_PATH = FIFA_2026_DIR / "r32_third_place_combinations.json"


def load_groups() -> dict[str, list[str]]:
    """Return ``{group_letter: [4 team names]}`` from the committed artifact."""
    data = json.loads(GROUPS_PATH.read_text(encoding="utf-8"))
    return {letter: list(data[letter]) for letter in GROUP_LETTERS}


def load_third_place_table() -> list[dict]:
    """Return FIFA's 495 third-place combinations as a list of rows.

    Each row is ``{"thirds": [8 group letters], "slots": {host_letter:
    source_group_letter}}`` — the eight advancing third-placed groups and which R32
    host-slot each is allocated to.
    """
    return json.loads(THIRD_PLACE_PATH.read_text(encoding="utf-8"))


def third_place_assignment(qualifying: frozenset[str]) -> dict[str, str]:
    """Look up FIFA's slot assignment for a set of 8 qualifying third-place groups.

    Returns ``{host_letter: source_group_letter}``. Raises ``KeyError`` if the set
    is not one of the 495 valid combinations. Scalar reference used by tests; the
    simulator builds a vectorized lookup from the same table.
    """
    for row in load_third_place_table():
        if frozenset(row["thirds"]) == qualifying:
            return dict(row["slots"])
    raise KeyError(f"No third-place combination for {sorted(qualifying)}")


def derive_groups_from_fixtures(conn) -> list[frozenset[str]]:
    """Recover the 12 groups as connected components of the WC2026 group fixtures.

    The group stage is a round-robin within each group, so teams that play each
    other in the group stage form 12 disjoint 4-cliques. Used to validate that the
    labelled ``groups.json`` matches the repo's own fixtures (no knockout fixtures
    exist in the data yet, so components are not merged).
    """
    rows = conn.execute(
        """
        SELECT h.name AS home, a.name AS away
        FROM matches m
        JOIN teams h ON h.id = m.home
        JOIN teams a ON a.id = m.away
        WHERE m.stage = 'FIFA World Cup' AND m.date >= '2026-01-01'
        """
    ).fetchall()
    adj: dict[str, set[str]] = {}
    for r in rows:
        adj.setdefault(r["home"], set()).add(r["away"])
        adj.setdefault(r["away"], set()).add(r["home"])

    seen: set[str] = set()
    components: list[frozenset[str]] = []
    for team in adj:
        if team in seen:
            continue
        stack, comp = [team], set()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            comp.add(node)
            stack.extend(adj[node] - seen)
        components.append(frozenset(comp))
    return components
