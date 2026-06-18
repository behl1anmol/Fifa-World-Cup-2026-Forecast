"""Market odds as a calibration reference (architecture §6, §4.5, decision #6).

The Odds API serves head-to-head (``h2h``) decimal odds for World Cup fixtures. This
module turns those prices into de-vigged win/draw/away probabilities and maps them to
matches in our database, so the calibration harness can compare the model to the
market. The framing is **matching** the market's calibration, not beating it: sharp
closing lines are well calibrated, and "beat the market" is unprovable on ~32 knockout
matches (decision #6).

De-vig method: convert each bookmaker's three decimal prices to implied probabilities
(``1/price``), strip that book's margin by normalising them to sum to 1, then average
the de-vigged probabilities across books. Simple proportional de-vig is the standard,
defensible MVP choice.

The functions never raise on messy input — unparseable events and unmatched fixtures
are skipped (and counted) so a partial odds feed still yields a usable reference.
"""
from __future__ import annotations

import json
import sqlite3

from .config import ODDS_LIVE_FILE, ODDS_SAMPLE_FILE

# The Odds API team name → martj42 (our DB) name. martj42 uses common anglicised
# names ("South Korea", "Czech Republic", "Turkey", "Ivory Coast", "United States"),
# so we only remap the FIFA-official / abbreviated variants the Odds API might emit.
# Keys are never themselves DB names, so a feed that already uses common names matches
# directly; unmapped names fall through unchanged and simply fail to match (counted,
# never fatal).
ODDS_NAME_ALIASES = {
    "USA": "United States",
    "Korea Republic": "South Korea",
    "Czechia": "Czech Republic",
    "Türkiye": "Turkey",
    "Côte d'Ivoire": "Ivory Coast",
    "Cabo Verde": "Cape Verde",
    "IR Iran": "Iran",
}


def decimal_to_implied(odds: float) -> float:
    """Implied probability of a decimal price (``1 / odds``)."""
    return 1.0 / float(odds)


def devig(p_home: float, p_draw: float, p_away: float):
    """Remove the bookmaker margin by normalising the three implied probs to sum 1."""
    total = p_home + p_draw + p_away
    if total <= 0:
        raise ValueError("implied probabilities must be positive")
    return p_home / total, p_draw / total, p_away / total


def _event_probs(event: dict):
    """De-vigged ``(pH, pD, pA)`` for one Odds API event, averaged across books.

    Returns ``None`` if no bookmaker exposes a complete h2h market for the event.
    """
    home, away = event.get("home_team"), event.get("away_team")
    if not home or not away:
        return None
    sums = [0.0, 0.0, 0.0]
    n_books = 0
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            prices = {o.get("name"): o.get("price") for o in market.get("outcomes", [])}
            ph, pa, pd = prices.get(home), prices.get(away), prices.get("Draw")
            if not (ph and pa and pd):
                continue
            h, d, a = devig(
                decimal_to_implied(ph), decimal_to_implied(pd), decimal_to_implied(pa)
            )
            sums[0] += h
            sums[1] += d
            sums[2] += a
            n_books += 1
    if n_books == 0:
        return None
    return sums[0] / n_books, sums[1] / n_books, sums[2] / n_books, n_books


def load_odds_json(path):
    """Parse an Odds API ``h2h`` JSON file into de-vigged per-match rows.

    Each returned dict: ``{home, away, commence_time, date, pH, pD, pA, n_books}``
    with names already mapped to our DB convention. Malformed events are skipped.
    """
    data = json.loads(open(path, encoding="utf-8").read())
    rows = []
    for event in data:
        probs = _event_probs(event)
        if probs is None:
            continue
        ph, pd, pa, n_books = probs
        commence = event.get("commence_time", "")
        rows.append(
            {
                "home": ODDS_NAME_ALIASES.get(event["home_team"], event["home_team"]),
                "away": ODDS_NAME_ALIASES.get(event["away_team"], event["away_team"]),
                "commence_time": commence,
                "date": commence[:10],  # ISO date prefix
                "pH": ph,
                "pD": pd,
                "pA": pa,
                "n_books": n_books,
            }
        )
    return rows


def _date_close(a: str, b: str, tol_days: int = 1) -> bool:
    """True if two ISO dates are within ``tol_days`` (timezone slack)."""
    if not a or not b:
        return False
    import datetime as _dt

    try:
        da = _dt.date.fromisoformat(a[:10])
        db = _dt.date.fromisoformat(b[:10])
    except ValueError:
        return False
    return abs((da - db).days) <= tol_days


def map_odds_to_matches(conn: sqlite3.Connection, odds_rows: list[dict]) -> list[dict]:
    """Attach DB match id + Elo-relevant fields to each odds row that we can locate.

    Joins by (home, away) team names and a near date (±1 day). Each returned row adds
    ``match_id`` and ``result`` (the stored ``"h:a"`` or ``None`` for an upcoming
    match). Odds events with no DB counterpart are dropped (counted by the caller).
    """
    db_rows = conn.execute(
        """
        SELECT m.id AS id, m.date AS date, m.result AS result,
               h.name AS home, a.name AS away
        FROM matches m
        JOIN teams h ON h.id = m.home
        JOIN teams a ON a.id = m.away
        WHERE m.date >= '2026-01-01'
        """
    ).fetchall()
    by_pair: dict[tuple, list] = {}
    for r in db_rows:
        by_pair.setdefault((r["home"], r["away"]), []).append(r)

    matched = []
    for od in odds_rows:
        candidates = by_pair.get((od["home"], od["away"]), [])
        hit = next((c for c in candidates if _date_close(c["date"], od["date"])), None)
        if hit is None and candidates:
            hit = candidates[0]  # name match but date drift — accept the pairing
        if hit is None:
            continue
        matched.append({**od, "match_id": hit["id"], "result": hit["result"]})
    return matched


def market_probs_by_match_id(matched_rows: list[dict]) -> dict:
    """``{match_id: (pH, pD, pA)}`` for *upcoming* priced fixtures (``result is None``).

    The live forecast blends these de-vigged market probabilities into the simulator as
    an input-only feature (decision #6 — match the market, never "beat" it). Completed
    fixtures are excluded: the simulator already conditions on their real scoreline, so
    their odds are irrelevant and must never override a known result.
    """
    return {
        r["match_id"]: (r["pH"], r["pD"], r["pA"])
        for r in matched_rows
        if r.get("result") is None and r.get("match_id") is not None
    }


def resolve_odds_path(allow_sample: bool = True):
    """Return ``(path, is_sample)``: the live odds file if present, else the sample.

    ``(None, False)`` when neither exists, so the harness can skip the market leg. The
    calibration harness uses the sample as an offline fallback (``allow_sample=True``);
    the live forecast passes ``allow_sample=False`` so it is market-aware only on real
    fetched odds, never the illustrative sample.
    """
    if ODDS_LIVE_FILE.exists():
        return ODDS_LIVE_FILE, False
    if allow_sample and ODDS_SAMPLE_FILE.exists():
        return ODDS_SAMPLE_FILE, True
    return None, False
