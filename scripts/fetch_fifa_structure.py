#!/usr/bin/env python3
"""Fetch + validate the FIFA 2026 bracket structure into committed data artifacts.

Writes two files under ``datasets/fifa_2026/`` plus a ``SOURCE.md``:

* ``groups.json``                       — {group_letter: [4 team names]}
* ``r32_third_place_combinations.json`` — FIFA's 495-row Annex C table

Sources are Wikipedia's machine-readable wikitext (``?action=raw``), which mirrors
FIFA's published regulations (Annex C). This is the *only* network path for Step 3;
the simulator and tests read the committed JSON and stay offline.

The third-place table is the app's highest bug-risk input (architecture §4.4), so
this script validates hard before writing: 12 groups of 4; 495 rows; every row a
bijection that respects FIFA's per-slot allowed groups; all C(12,8)=495 group
subsets covered; and FIFA's row 1 matches its known literal values.

Usage:
    python scripts/fetch_fifa_structure.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.config import FIFA_2026_DIR  # noqa: E402
from forecast.tournament import (  # noqa: E402
    GROUP_LETTERS,
    THIRD_SLOT_ALLOWED,
    THIRD_SLOT_COLUMN_ORDER,
)

_UA = {"User-Agent": "fifa-wc2026-forecast/0.3 (architecture step 3; contact: repo owner)"}
_BACKOFF = (2, 4, 8, 16)
_TEAM_LINK = re.compile(r"\[\[[^\]|]*national [^\]|]*team\|([^\]]+)\]\]")

# Known-correct FIFA row 1 (third groups E,F,G,H,I,J,K,L) -> host-slot assignment.
_ROW1_CHECK = {"A": "E", "B": "J", "D": "I", "E": "F", "G": "H", "I": "G", "K": "L", "L": "K"}


def _fetch_raw(title: str) -> str:
    """Fetch a Wikipedia page's raw wikitext with exponential-backoff retries."""
    url = f"https://en.wikipedia.org/wiki/{title}?action=raw"
    last: Exception | None = None
    for attempt in range(len(_BACKOFF) + 1):
        try:
            resp = requests.get(url, headers=_UA, timeout=30)
            resp.raise_for_status()
            return resp.text
        except Exception as err:  # noqa: BLE001 - retry any transient failure
            last = err
            if attempt < len(_BACKOFF):
                print(f"  ! {url} failed ({err}); retry in {_BACKOFF[attempt]}s")
                time.sleep(_BACKOFF[attempt])
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def _first_four(text: str) -> list[str]:
    """First four distinct national-team names linked in a chunk of wikitext."""
    out: list[str] = []
    for name in _TEAM_LINK.findall(text):
        if name not in out:
            out.append(name)
        if len(out) == 4:
            break
    return out


def parse_groups() -> dict[str, list[str]]:
    """Extract the 12 group rosters. A–H come from the main article's group
    sections; I–L are stubs there mid-tournament, so they come from group subpages.
    """
    main = _fetch_raw("2026_FIFA_World_Cup")
    headers = {g: main.find(f"===Group {g}===") for g in GROUP_LETTERS}
    end = main.find("==Knockout stage==")
    groups: dict[str, list[str]] = {}
    for i, g in enumerate(GROUP_LETTERS):
        start = headers[g]
        nxt = headers[GROUP_LETTERS[i + 1]] if i + 1 < len(GROUP_LETTERS) else end
        teams = _first_four(main[start:nxt]) if start >= 0 else []
        if len(teams) != 4:
            teams = _first_four(_fetch_raw(f"2026_FIFA_World_Cup_Group_{g}"))
        if len(teams) != 4:
            raise RuntimeError(f"Group {g}: expected 4 teams, parsed {teams}")
        groups[g] = teams
    return groups


def parse_third_place_table() -> list[dict]:
    """Parse FIFA's 495-row third-place combination table from its Wikipedia
    template into rows of {"thirds": [...], "slots": {host: source}}."""
    text = _fetch_raw("Template:2026_FIFA_World_Cup_third-place_table")
    rows: list[dict] = []
    for seg in re.split(r'!\s*scope="row"\s*\|', text)[1:]:
        cell = seg.split("|-")[0]
        assigns = re.findall(r"3([A-L])\b", cell)
        if len(assigns) < 8:
            continue
        sources = assigns[-8:]  # the 8 assignment cells, in column order
        slots = {THIRD_SLOT_COLUMN_ORDER[i]: sources[i] for i in range(8)}
        rows.append({"thirds": sorted(set(sources)), "slots": slots})
    return rows


def validate(groups: dict[str, list[str]], table: list[dict]) -> None:
    """Fail loudly on any structural problem before anything is written."""
    # Groups: 12 × 4 distinct teams.
    flat = [t for v in groups.values() for t in v]
    assert all(len(v) == 4 for v in groups.values()), "a group lacks 4 teams"
    assert len(set(flat)) == 48, f"expected 48 distinct teams, got {len(set(flat))}"

    # Third-place table: 495 rows, each a constraint-respecting bijection.
    assert len(table) == 495, f"expected 495 rows, got {len(table)}"
    seen = set()
    for n, row in enumerate(table, 1):
        slots = row["slots"]
        srcs = list(slots.values())
        assert sorted(slots) == sorted(THIRD_SLOT_COLUMN_ORDER), f"row {n} bad slots"
        assert len(set(srcs)) == 8, f"row {n} not a bijection: {srcs}"
        for host, src in slots.items():
            assert src in THIRD_SLOT_ALLOWED[host], f"row {n}: {src} illegal for slot {host}"
        seen.add(frozenset(srcs))
    expected = {frozenset(c) for c in combinations(GROUP_LETTERS, 8)}
    assert seen == expected, "table does not cover all 495 group combinations"
    assert table[0]["slots"] == _ROW1_CHECK, f"row 1 mismatch: {table[0]['slots']}"


def _write_source_md(folder: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    folder.joinpath("SOURCE.md").write_text(
        "\n".join(
            [
                "# Source provenance — FIFA 2026 bracket structure",
                "",
                f"- **Last fetched:** {stamp}",
                "- **Groups:** https://en.wikipedia.org/wiki/2026_FIFA_World_Cup "
                "(+ per-group subpages for I–L)",
                "- **Third-place table:** "
                "https://en.wikipedia.org/wiki/Template:2026_FIFA_World_Cup_third-place_table",
                "  — mirrors FIFA's Annex C (495 combinations of the round of 32).",
                "",
                "## Validated invariants (see scripts/fetch_fifa_structure.py)",
                "",
                "- 12 groups × 4 teams = 48 distinct teams.",
                "- 495 third-place rows; each a bijection respecting FIFA's per-slot",
                "  allowed groups; all C(12,8)=495 group subsets covered; row 1 exact.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    FIFA_2026_DIR.mkdir(parents=True, exist_ok=True)
    print("Fetching group rosters ...")
    groups = parse_groups()
    print("Fetching FIFA third-place combination table ...")
    table = parse_third_place_table()

    print("Validating ...")
    validate(groups, table)

    (FIFA_2026_DIR / "groups.json").write_text(
        json.dumps(groups, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (FIFA_2026_DIR / "r32_third_place_combinations.json").write_text(
        json.dumps(table, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_source_md(FIFA_2026_DIR)

    print("-" * 50)
    for g in GROUP_LETTERS:
        print(f"  Group {g}: {', '.join(groups[g])}")
    print(f"  third-place combinations: {len(table)} (validated)")
    print(f"Wrote artifacts to {FIFA_2026_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
