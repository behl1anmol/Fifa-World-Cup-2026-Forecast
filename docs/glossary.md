# Glossary & FAQ

Quick, plain-language definitions of the terms used across this project, and answers to common
questions.

← Back to the [documentation index](README.md).

## Glossary

**Backtest** — Grading a model on *past* data it wasn't trained on, to estimate how well it
will do in future. Here: fit on matches before a cutoff date, score on the matches after it.

**Blend (fixed-weight)** — Combining two or more separate forecasts of the same match by a
weighted average, where the weights are **fixed** (chosen once), not learned per match. See
[Concepts §5](concepts.md#5-combining-opinions-the-fixed-weight-blend).

**Brier score** — A scoring rule: the squared error between predicted probabilities and what
actually happened. Lower is better.

**Calibration** — The property that predicted probabilities match real frequencies: things the
app calls "20%" happen ~20% of the time. This is the project's definition of success.

**Confederation** — A regional football body (UEFA, CONMEBOL, etc.). A column in the `teams`
table; not central to the forecast.

**De-vig / overround / vig** — Bookmaker odds imply probabilities that sum to *more* than 100%;
the excess is the bookmaker's margin ("vig" or "overround"). **De-vigging** removes it by
normalising the implied probabilities back to 100%. See
[Concepts §9](concepts.md#9-the-betting-market-as-a-yardstick-odds-and-de-vigging).

**Dixon-Coles** — A football scoring model (Dixon & Coles, 1997): two Poisson goal counts with
a small correction (the **ρ** parameter) that fixes the under-prediction of low-scoring draws.

**Elo** — A rating system (from chess) that gives each team a single strength number, updated
after every match. See [Concepts §2](concepts.md#2-rating-team-strength-elo).

**Expected goals (λ, "lambda")** — How many goals a team is *expected* to score in a match, fed
into the Poisson distribution to get the chance of each actual scoreline.

**Feature** — An input the model uses to make a prediction (e.g. the Elo gap, the home flag,
de-vigged market odds).

**Idempotent** — An operation that has the same effect whether you run it once or many times
(e.g. re-loading the data, or re-running an update on unchanged state).

**Leakage (data leakage)** — Accidentally letting a model use information it wouldn't have had
at prediction time (e.g. the match result, or future matches). The app prevents this with
**point-in-time** ratings. See [Concepts §10](concepts.md#10-two-ideas-that-keep-it-honest).

**LightGBM** — A general-purpose gradient-boosted decision-tree machine-learning library, used
as an *optional* third match-forecast view.

**Log-loss** — A scoring rule (cross-entropy) that punishes confident wrong predictions very
harshly. Lower is better.

**Margin of victory (MOV)** — The goal difference of a result. The Elo engine optionally lets
big wins move ratings a little more.

**Monte Carlo** — Estimating a probability by running a random process many times and counting
outcomes. Here: play the whole bracket 50,000 times, count titles. See
[Concepts §6](concepts.md#6-playing-the-tournament-50000-times-monte-carlo).

**Point-in-time** — A value as it stood *before* a given match — e.g. `elo_before`. Using only
point-in-time inputs is what makes the model leak-free.

**Poisson distribution** — The standard probability model for counting independent, rare-ish
events in a fixed window — a natural fit for goals in a match.

**RPS (Ranked Probability Score)** — The project's **primary** scoring rule. Like Brier, but it
respects the order home → draw → away, so a "near miss" is penalised less. Lower is better.

**Reliability diagram** — A chart of predicted vs actually-observed frequency; a perfectly
calibrated model sits on the diagonal.

**`run_id`** — The id of a saved forecast snapshot. It's a fingerprint of all the inputs, so
identical inputs reuse the same id (no duplicates) and any change creates a new one. The
reserved id `"pretournament"` is the baseline. See
[How It Works](how-it-works.md#reproducibility-and-the-run_id).

**Seed** — A fixed number that makes the "random" simulation reproducible: same seed → identical
results.

**Snapshot** — One saved forecast (one `predictions` row per team, sharing a `run_id`).

**Stage probabilities** — Per team, the chance of *reaching* each round: `r32, r16, qf, sf,
final, title`.

**τ (tau)** — The Dixon-Coles factor that adjusts the four lowest scorelines (controlled by ρ).

## FAQ

**Does the app predict who will win?**
Not as a single name. It outputs each team's *probability* of winning. The "favourite" is just
the team with the highest probability — which is usually still well under 50%.

**Why do I get the exact same numbers every time I run it?**
By design. The simulation uses a fixed random **seed**, so the same database state always
produces identical probabilities. This makes the forecast auditable and lets the "pre-vs-now"
comparison be meaningful. Change `--seed` (or feed in a new result) to get different numbers.

**Do I need an API key or internet access?**
No. All required data is committed in `datasets/`, and the app runs fully offline. An
`ODDS_API_KEY` is only needed if you want *live* bookmaker odds as an optional feature
([Operations](operations.md#live-market-odds-optional)).

**The market tab shows a "SAMPLE" warning — is something broken?**
No. Offline, the app uses a committed illustrative odds sample, clearly flagged as `[SAMPLE]`.
Set up live odds to replace it.

**How do I update the forecast when a match finishes?**
Run `python scripts/update_loop.py --date … --home … --away … --score …`. See
[Operations](operations.md#during-the-tournament-the-update-loop).

**Is "beating the bookmakers" the goal?**
No. The market is used as a **reference to match**, not a target to beat — beating it isn't
provable on a few dozen matches. The goal is to be as well *calibrated* as the market
([decision #6](architecture-overview.md#2-locked-decisions)).

**Where's the database, and can I delete it?**
It's `forecast.db` in the project root, git-ignored and fully rebuildable from `datasets/`.
Delete it anytime and re-run the [first-run steps](getting-started.md#4-first-run-produce-a-forecast).

**How accurate is the model?**
On a leak-free historical backtest it scores RPS ≈ 0.170 (lower is better), competitive with the
betting market on the matches where both can be compared. "Accuracy" here means *calibration*,
not naming winners — see [Concepts §8](concepts.md#8-judging-the-forecast-calibration-and-scoring-rules).

---

That's the full guide. For the original design rationale, see the
[architecture overview](architecture-overview.md); for the quick command cheat-sheet, the
[top-level README](../README.md).
