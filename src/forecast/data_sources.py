"""Fetch raw datasets (architecture §6) into per-source folders under datasets/.

Each source downloads into its own subdirectory so the raw data never gets
interlinked. After a successful fetch a ``SOURCE.md`` records provenance (URL,
license, fetch timestamp) for the audit trail (§7).

Network access lives *only* here; the loader and tests read local files, so they
stay offline and deterministic.

The Odds API and Transfermarkt are deliberately deferred (architecture §6, §8):
the Odds API needs a key and isn't used until the calibration step; Transfermarkt
requires scraping and is optional. Both keep placeholder READMEs and the Odds API
fetch skips gracefully when no key is present.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import DATA_SOURCES, ODDS_API_DIR

# Retry schedule for transient network errors (architecture's git/network policy
# mirrored for data fetches): 2s, 4s, 8s, 16s.
_BACKOFF_SECONDS = (2, 4, 8, 16)
_TIMEOUT = 60


def _download(url: str, dest: Path) -> int:
    """Download ``url`` to ``dest`` with exponential-backoff retries.

    Returns the number of bytes written. Raises the last error if all attempts
    fail.
    """
    last_err: Exception | None = None
    for attempt in range(len(_BACKOFF_SECONDS) + 1):
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            return len(resp.content)
        except Exception as err:  # noqa: BLE001 - retry on any transient failure
            last_err = err
            if attempt < len(_BACKOFF_SECONDS):
                wait = _BACKOFF_SECONDS[attempt]
                print(f"  ! {url} failed ({err}); retrying in {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"Failed to download {url}: {last_err}")


def _write_source_md(folder: Path, homepage: str, license_: str, files: list[str]) -> None:
    """Record provenance next to the downloaded files."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# Source provenance",
        "",
        f"- **Homepage:** {homepage}",
        f"- **License:** {license_}",
        f"- **Last fetched:** {stamp}",
        "",
        "## Files",
        "",
    ]
    lines += [f"- `{name}`" for name in sorted(files)]
    lines.append("")
    (folder / "SOURCE.md").write_text("\n".join(lines), encoding="utf-8")


def fetch_source(name: str) -> dict:
    """Fetch one named source from DATA_SOURCES. Returns per-file byte counts."""
    if name not in DATA_SOURCES:
        raise KeyError(f"Unknown source '{name}'. Known: {sorted(DATA_SOURCES)}")
    spec = DATA_SOURCES[name]
    folder: Path = spec["dir"]
    folder.mkdir(parents=True, exist_ok=True)
    print(f"[{name}] -> {folder}")
    results: dict[str, int] = {}
    for filename, url in spec["files"].items():
        size = _download(url, folder / filename)
        results[filename] = size
        print(f"  ok {filename} ({size:,} bytes)")
    _write_source_md(folder, spec["homepage"], spec["license"], list(spec["files"]))
    return results


def fetch_odds_api() -> bool:
    """Deferred Odds API fetch (architecture §6).

    Reads ``ODDS_API_KEY`` from the environment. If absent, prints a skip notice
    and returns ``False`` without failing — the core app never depends on odds.
    """
    key = os.environ.get("ODDS_API_KEY", "").strip()  # tolerate stray surrounding whitespace
    ODDS_API_DIR.mkdir(parents=True, exist_ok=True)
    if not key:
        print(
            "[odds_api] skipped: no ODDS_API_KEY set. Odds are a Step 5 reference "
            "feature; the core forecast does not require them."
        )
        return False
    url = (
        "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds/"
        f"?regions=eu&markets=h2h&oddsFormat=decimal&apiKey={key}"
    )
    size = _download(url, ODDS_API_DIR / "wc2026_h2h_odds.json")
    print(f"[odds_api] ok wc2026_h2h_odds.json ({size:,} bytes)")
    return True


def fetch_all() -> dict[str, dict]:
    """Fetch every direct-download source plus the deferred Odds API attempt."""
    summary: dict[str, dict] = {}
    for name in DATA_SOURCES:
        summary[name] = fetch_source(name)
    summary["odds_api"] = {"fetched": fetch_odds_api()}
    return summary
