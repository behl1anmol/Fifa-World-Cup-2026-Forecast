"""Streamlit dashboard for the live forecast (architecture §4.6) — Step 7.

Clean and interactive, no animation (§4.6). Reads straight from the shared ``service``
layer (no running API server required), so the page reflects whatever the update loop
has written to the database. Launch with ``scripts/dashboard.py`` or directly:

    streamlit run src/forecast/dashboard.py

The page degrades gracefully when state is missing (no snapshot yet, no pre-tournament
baseline, no odds file), telling the operator which build step to run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make ``src/`` importable when Streamlit runs this file as a standalone script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from forecast import service  # noqa: E402
from forecast.config import DB_PATH  # noqa: E402
from forecast.db import connect  # noqa: E402

STAGE_LABELS = {
    "r32": "Round of 32", "r16": "Round of 16", "qf": "Quarterfinal",
    "sf": "Semifinal", "final": "Final", "title": "Champion",
}


@st.cache_resource
def _conn():
    """One cached read connection for the session (Streamlit reruns are single-thread)."""
    return connect(DB_PATH)


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def main() -> None:
    st.set_page_config(page_title="WC2026 Forecast", layout="wide")
    conn = _conn()

    st.title("🏆 FIFA World Cup 2026 — Live Forecast")
    st.caption(
        "Probabilities, not prophecy. Success is **calibration** — when we say 20%, it "
        "should happen about 20% of the time. The market is a reference to match, not beat."
    )

    latest = service.latest(conn)
    if latest is None:
        st.warning(
            "No forecast snapshot yet. Run `python scripts/run_simulation.py` "
            "(and `python scripts/build_baseline.py` for the pre-tournament baseline)."
        )
        return

    runs = service.runs(conn)
    c1, c2, c3 = st.columns(3)
    c1.metric("Last updated (UTC)", latest["timestamp"][:19].replace("T", " "))
    c2.metric("Model version", latest["model_version"])
    c3.metric("Snapshots in history", len(runs))

    tab_odds, tab_team, tab_market = st.tabs(
        ["Title odds", "Team path", "Market comparison"]
    )

    # --- Ranked live title odds + pre-vs-now toggle -------------------------
    with tab_odds:
        show_delta = st.toggle("Show pre-tournament vs now", value=False)
        if show_delta:
            comp = service.pre_vs_now(conn)
            if not comp["has_baseline"]:
                st.info(
                    "Pre-tournament baseline not generated. "
                    "Run `python scripts/build_baseline.py`."
                )
            df = pd.DataFrame(comp["rows"])
            df = df.rename(columns={
                "name": "Team", "baseline_title": "Pre-tournament",
                "now_title": "Now", "delta": "Δ",
            })
            df.insert(0, "Rank", range(1, len(df) + 1))
            view = df[["Rank", "Team", "Pre-tournament", "Now", "Δ"]].copy()
            for col in ("Pre-tournament", "Now", "Δ"):
                view[col] = view[col].map(_pct)
            st.dataframe(view, hide_index=True, width="stretch", height=560)
        else:
            teams = latest["teams"]
            df = pd.DataFrame(
                {
                    "Rank": range(1, len(teams) + 1),
                    "Team": [t["name"] for t in teams],
                    "Title %": [t["title_prob"] * 100 for t in teams],
                }
            )
            st.bar_chart(
                df.set_index("Team")["Title %"].head(15), horizontal=True, height=420
            )
            disp = df.copy()
            disp["Title %"] = disp["Title %"].map(lambda x: f"{x:.1f}%")
            st.dataframe(disp, hide_index=True, width="stretch", height=400)

        st.download_button(
            "⬇️ Export this snapshot (JSON)",
            data=json.dumps(service.export_snapshot(conn, latest["run_id"]), indent=2),
            file_name=f"forecast_{latest['run_id']}.json",
            mime="application/json",
        )

    # --- Per-team path to the final -----------------------------------------
    with tab_team:
        teams = latest["teams"]
        by_name = {t["name"]: t for t in teams}
        pick = st.selectbox("Team", list(by_name))
        chosen = by_name[pick]
        path = service.team_path(conn, chosen["team_id"], latest["run_id"])
        sp = path["stage_probabilities"]
        chart = pd.DataFrame(
            {
                "Stage": [STAGE_LABELS[s] for s in service.STAGE_ORDER],
                "Probability %": [sp[s] * 100 for s in service.STAGE_ORDER],
            }
        ).set_index("Stage")
        st.bar_chart(chart, height=360)

        base = service.baseline(conn)
        if base is not None:
            base_team = next(
                (t for t in base["teams"] if t["team_id"] == chosen["team_id"]), None
            )
            if base_team is not None:
                bsp = base_team["stage_probabilities"]
                cmp_df = pd.DataFrame(
                    {
                        "Stage": [STAGE_LABELS[s] for s in service.STAGE_ORDER],
                        "Pre-tournament": [_pct(bsp[s]) for s in service.STAGE_ORDER],
                        "Now": [_pct(sp[s]) for s in service.STAGE_ORDER],
                    }
                )
                st.caption("Pre-tournament vs now")
                st.dataframe(cmp_df, hide_index=True, width="stretch")

    # --- Market comparison ---------------------------------------------------
    with tab_market:
        mc = service.market_comparison(conn)
        if not mc["has_odds"]:
            st.info("No odds file present. See the Odds API setup in the README.")
        elif not mc["rows"]:
            st.info("No upcoming priced matches map to the current fixtures.")
        else:
            if mc["is_sample"]:
                st.warning("Using the committed **SAMPLE** odds — illustrative only.")
            rows = mc["rows"]
            mdf = pd.DataFrame(
                {
                    "Date": [r["date"] for r in rows],
                    "Home": [r["home"] for r in rows],
                    "Away": [r["away"] for r in rows],
                    "Model home win": [_pct(r["model_home"]) for r in rows],
                    "Market home win": [_pct(r["market_home"]) for r in rows],
                    "Bullishness": [f"{r['bullish_home'] * 100:+.1f}%" for r in rows],
                    "Result": [r["result"] or "—" for r in rows],
                }
            )
            st.dataframe(mdf, hide_index=True, width="stretch")
            st.caption(
                "Bullishness = model − market on the home win. The goal is to *match* "
                "the market's calibration, not beat it (decision #6)."
            )


# Streamlit executes this file top-to-bottom on every rerun, so render unconditionally.
main()
