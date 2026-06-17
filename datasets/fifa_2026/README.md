# FIFA World Cup 2026 fixtures & live results

**Status:** sourced from martj42 — no separate fetch needed.

The architecture lists "FIFA 2026 fixtures + live results" as a source (§6) to
condition the simulator and drive the update loop. In practice the martj42
dataset (`datasets/martj42/results.csv`) **already contains all 72 WC2026 group
fixtures**, with completed matches scored and not-yet-played fixtures marked
`NA`. As matches are played, martj42 fills in the scores and the idempotent
loader updates them in place.

Scraping the FIFA website is therefore unnecessary (and the site is JS-heavy).
This folder is reserved for any future FIFA-specific artifacts (e.g. the official
Round-of-32 third-place combination table used in Step 3), kept separate so
sources never get interlinked.
