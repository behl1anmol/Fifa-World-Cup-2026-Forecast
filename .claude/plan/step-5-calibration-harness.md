# Step 5 — Calibration / evaluation harness

## Context

Steps 1–4 deliver a leak-free fundamentals match model (blended Dixon-Coles + Elo)
and a Monte Carlo simulator. The project's success metric is **calibration, not
winner-calling** (decision #2): when the app says 20%, that should happen ~20% of the
time. Step 5 (architecture §4.5, build item 3) builds the harness that *measures* that
honesty, guards against leakage, and adds the **market as a reference** — framing the
goal as *matching* the market, not beating it (decision #6).

Decisions taken with the user:
- **Minimal market-blend model** — three distinct compared rows: (1) **odds-free** =
  fundamentals model, (2) **market** = de-vigged Odds API price, (3) **model** =
  fundamentals fixed-weight-blended with the price (reusing `match_model.blend`). This
  answers "do fundamentals add signal beyond the price?" and pulls a small odds blend
  forward from Step 8.
- **Odds source** — live via the user's `ODDS_API_KEY` (read from env by the existing
  `data_sources.fetch_odds_api`). Because the free tier serves only *upcoming* odds, a
  committed **clearly-labelled SAMPLE** odds file makes the three-way comparison,
  reliability plot, and report reproducible offline and in tests today.

## What was built

### New `src/forecast/metrics.py` (pure, vectorized)
`outcome_index`, `rps` (primary, order-aware), `brier`, `log_loss`,
`reliability_curve` (pooled one-vs-rest), `save_reliability_diagram` (matplotlib Agg).

### New `src/forecast/market.py`
`decimal_to_implied`, `devig` (proportional margin removal), `load_odds_json` (parse
The Odds API `h2h`, average de-vigged probs across books), `map_odds_to_matches` (join
to DB by names + a small Odds-API→martj42 alias map + near date), `resolve_odds_path`
(live → sample → none).

### New `src/forecast/calibration.py`
`build_backtest` (leak-free time-split, reuses `match_model._load_fit_rows` + `predict`),
`evaluate`, `market_blend` (wraps `match_model.blend`), `_completed_2026_elo`,
`three_way` (scored model/market/odds-free), `live_comparison` (upcoming priced
matches), `build_report` (markdown).

### New `scripts/evaluate_calibration.py`
Bootstraps DB+ratings, runs the historical backtest, the market three-way (live/sample/
skip), saves `reports/reliability_step5.png` + `reports/step5_calibration_report.md`,
prints a summary. Args `--cutoff`, `--market-weight`, `--bins`.

### New `datasets/odds_api/wc2026_h2h_odds.sample.json`
8 completed WC2026 group matches in real Odds API format, two books each (margin
included), DB-exact team names. Documented as SAMPLE in the folder README.

### Modified
`config.py` (`CALIBRATION_CUTOFF`, `RELIABILITY_BINS`, `MARKET_BLEND_WEIGHT`,
`REPORTS_DIR`, `ODDS_LIVE_FILE`/`ODDS_SAMPLE_FILE`; `MODEL_VERSION="0.5.0-step5-calibration"`),
`requirements.txt` (matplotlib), `.gitignore` (`reports/`, live odds json), README,
odds_api README.

### Tests
`tests/test_metrics.py` (hand-checked metrics incl. RPS ordering + reliability),
`tests/test_market.py` (de-vig, sample parse, alias, mapping),
`tests/test_calibration.py` (backtest, market_blend extremes, three_way, report).

## Acceptance — verified
- `pytest -q` → **68 passed**.
- `scripts/evaluate_calibration.py` (offline, [SAMPLE]):
  - Historical backtest (odds-free, < 2018-01-01, n=8,127): **RPS 0.1700**, Brier
    0.5122, log-loss 0.8714.
  - Three-way on 8 priced+completed sample matches: market RPS 0.1761, model (blend)
    0.1905, odds-free 0.2161 — market best on this tiny set, as expected.
  - Saved `reports/reliability_step5.png` and `reports/step5_calibration_report.md`.

## Notes
- Historical backtest has no odds, so "model" (blend) equals "odds-free" there; the
  three distinct rows are exercised on the odds subset. The report states this and the
  honesty caveat (match, not beat; converges as fixtures complete).
- No API key is committed; the key is read only from `ODDS_API_KEY`.
