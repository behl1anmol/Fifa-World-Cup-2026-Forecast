# Source provenance — FIFA 2026 bracket structure

- **Last fetched:** 2026-06-17 10:14:05 UTC
- **Groups:** https://en.wikipedia.org/wiki/2026_FIFA_World_Cup (+ per-group subpages for I–L)
- **Third-place table:** https://en.wikipedia.org/wiki/Template:2026_FIFA_World_Cup_third-place_table
  — mirrors FIFA's Annex C (495 combinations of the round of 32).

## Validated invariants (see scripts/fetch_fifa_structure.py)

- 12 groups × 4 teams = 48 distinct teams.
- 495 third-place rows; each a bijection respecting FIFA's per-slot
  allowed groups; all C(12,8)=495 group subsets covered; row 1 exact.
