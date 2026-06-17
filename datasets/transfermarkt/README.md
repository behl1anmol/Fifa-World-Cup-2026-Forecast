# Transfermarkt / squad-strength (deferred)

**Status:** placeholder — not fetched, not scraped in Step 1.

Transfermarkt squad values and player ratings are an **optional** squad-strength
feature (architecture §6, §4.3). They are explicitly last in line (Step 8,
"Could-have") and the core app **never depends on them**.

## Why deferred and not scraped
- Acquisition requires scraping, which carries terms-of-service exposure
  (architecture §7, Compliance).
- The feature is optional and must be **cached** so the core never relies on a
  live scrape.

When implemented in Step 8, cached extracts will live here, kept separate from
every other source so datasets never get interlinked.
