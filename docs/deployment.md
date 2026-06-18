# Deployment

How this documentation site is published, and how to deploy the live Streamlit dashboard.

← Back to the [documentation index](README.md).

## Overview

| Target | Tooling | How it's triggered |
|--------|---------|--------------------|
| **Documentation site** (this site) | MkDocs + Material → GitHub Pages | **Build** runs automatically on PRs/merges to `main`; **deploy** is a manual workflow. |
| **Streamlit dashboard** | Streamlit Community Cloud | One-time manual connect, then auto-redeploys on push. No workflow file (none is possible — see below). |

## Documentation site (GitHub Pages)

The docs in this folder are built into a static site with
[MkDocs](https://www.mkdocs.org/) and the
[Material theme](https://squidfunk.github.io/mkdocs-material/), configured in
[`mkdocs.yml`](https://github.com/behl1anmol/Fifa-World-Cup-2026-Forecast/blob/main/mkdocs.yml).
Material renders the Mermaid diagrams natively, so no extra plugin is needed.

### Build it locally

```bash
pip install -r requirements-docs.txt
mkdocs serve          # live preview at http://127.0.0.1:8000
mkdocs build --strict # one-off build into ./site (fails on broken links / bad diagrams)
```

> **Note on source links.** The Markdown links to source files (e.g. `../src/forecast/elo.py`)
> are relative so they work when browsing the repo on GitHub. A build-time hook
> (`hooks/fix_repo_links.py`) rewrites them to absolute GitHub URLs when the site is
> built, so they also work on the published site. You don't need to do anything — it's automatic.

### CI workflows

| Workflow | File | Trigger | What it does |
|----------|------|---------|--------------|
| **Docs Build** | `.github/workflows/docs-build.yml` | **Automatic** — PRs to `main` and pushes/merges to `main` (scoped to docs-related paths) | Runs `mkdocs build --strict` to validate the site (catches broken links and invalid Mermaid). Uploads the built site as a CI artifact. |
| **Docs Deploy (GitHub Pages)** | `.github/workflows/docs-deploy.yml` | **Manual** — `workflow_dispatch` only | Builds the site and publishes it to GitHub Pages. |

This matches the intended policy: only the build runs on PR/merge; everything else is manual.

### One-time setup to publish

1. In the repo on GitHub: **Settings → Pages → Build and deployment → Source = "GitHub
   Actions"**. (This cannot be done from a workflow file; it's a one-time repository setting.)
2. Go to **Actions → "Docs Deploy (GitHub Pages)" → Run workflow** (select `main`).
3. When it finishes, the site is live at
   **https://behl1anmol.github.io/Fifa-World-Cup-2026-Forecast/**
   (the `site_url` set in `mkdocs.yml`).

Re-run the **Docs Deploy** workflow whenever you want to publish the latest docs. The **Docs
Build** workflow will already have validated them on the PR/merge.

## Streamlit dashboard (Streamlit Community Cloud)

> **Why there is no deployment workflow.** Streamlit Community Cloud has **no public API, CLI,
> or GitHub Action** for deploying an app. You connect a GitHub repository once through its web
> UI, and from then on it **auto-redeploys whenever you push** to the tracked branch. Because
> there is nothing to script, no deployment `.yml` is provided (per the project's decision to
> skip impossible automation). The steps below are the manual one-time setup.

### One-time setup

1. Sign in at **[share.streamlit.io](https://share.streamlit.io)** with your GitHub account and
   authorize access to this repository.
2. Click **Create app → Deploy a public app from GitHub** and fill in:
   - **Repository:** `behl1anmol/Fifa-World-Cup-2026-Forecast`
   - **Branch:** `main`
   - **Main file path:** `src/forecast/dashboard.py`
3. (Optional) Under **Advanced settings → Secrets**, add any secrets the app uses, e.g.:
   ```toml
   ODDS_API_KEY = "your_key"
   ```
   The dashboard runs fine without this — odds are an optional feature (see
   [Operations](operations.md#live-market-odds-optional)).
4. Click **Deploy**. After the first deploy, every push to `main` triggers an automatic
   redeploy — no further action needed.

### Notes & caveats

- **The app is designed to run directly.** `dashboard.py` adds the repo's `src/` to the import
  path and calls `main()` at import time, so `streamlit run src/forecast/dashboard.py` works as
  the app entry point Community Cloud expects.
- **The database is built at runtime, not committed.** `forecast.db` is git-ignored and
  rebuildable from `datasets/`. On a fresh Community Cloud container the dashboard will show its
  "no snapshot yet" state until the data/forecast steps have produced a snapshot. For a
  zero-touch hosted demo you would need to generate a snapshot as part of startup (e.g. run the
  [first-run steps](getting-started.md#4-first-run-produce-a-forecast) in a small bootstrap), or
  commit a pre-built database. This is out of scope for the manual setup above.
- **Dependencies** come from [`requirements.txt`](https://github.com/behl1anmol/Fifa-World-Cup-2026-Forecast/blob/main/requirements.txt),
  which Community Cloud installs automatically.

### If you later want fully-automated hosting

Streamlit Community Cloud can't be scripted, but the app is a normal Streamlit process, so an
automatable host (e.g. a container platform, Hugging Face Spaces, or Render) could be driven by
a manual-trigger workflow. That isn't set up here — ask if you'd like it added.
