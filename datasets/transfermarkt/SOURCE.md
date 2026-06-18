# Source provenance

- **Homepage:** https://www.transfermarkt.com
- **License / terms:** Transfermarkt content is subject to the site's terms of use;
  scraping carries ToS exposure. This project does **not** scrape it.
- **What is committed here:** only `squad_strength.sample.json`, a hand-authored
  **illustrative** sample (not derived from Transfermarkt). A real cached extract, if
  used, lives in the git-ignored `squad_strength.json`.
- **Role:** optional, cached, opt-in squad-strength feature (architecture §6, §4.3,
  "Could-have"). The core forecast never depends on it.
