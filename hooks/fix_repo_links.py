"""MkDocs build hook: rewrite repo-relative links so the built site works.

The Markdown docs are written to be browsed inside the GitHub repo, so they link to
source files with paths that *escape* the ``docs/`` tree, e.g. ``](../src/forecast/elo.py)``
or ``](../README.md)``. Those targets are not part of the MkDocs site, so on a published
GitHub Pages site they would 404 — and ``mkdocs build --strict`` rejects them outright.

Rather than edit the committed Markdown (which would spoil the nice relative links when the
files are read on github.com), we rewrite the escaping links to absolute GitHub URLs **at
build time only**, via the ``on_page_markdown`` event:

    ](../<path>)  ->  ](https://github.com/<owner>/<repo>/blob/<branch>/<path>)

Directory targets (paths ending in ``/``, e.g. ``../datasets/``) use ``/tree/`` instead of
``/blob/`` so GitHub renders the folder listing rather than 404-ing.

Intra-``docs`` links (``concepts.md``, ``#anchors`` …) are left untouched — MkDocs resolves
those itself. Only links beginning with ``](../`` are rewritten.

This file lives in the repo-root ``hooks/`` directory (outside ``docs_dir``) so it is not
copied into the published site. It is wired in via the ``hooks:`` key in ``mkdocs.yml``.
"""
from __future__ import annotations

import re

# Keep these in sync with the repository the Pages site is published from.
_OWNER = "behl1anmol"
_REPO = "Fifa-World-Cup-2026-Forecast"
_BRANCH = "main"
_BASE = f"https://github.com/{_OWNER}/{_REPO}"

# Matches Markdown links whose target starts with ``../`` (i.e. escapes docs/), capturing the
# path after the ``../``. Stops at the closing paren; titles are not used in these docs.
_ESCAPING_LINK = re.compile(r"\]\(\.\./([^)]+)\)")


def _replace(match: "re.Match[str]") -> str:
    path = match.group(1)
    kind = "tree" if path.endswith("/") else "blob"
    return f"]({_BASE}/{kind}/{_BRANCH}/{path})"


def on_page_markdown(markdown: str, **kwargs) -> str:
    """Rewrite repo-relative ``../`` links to absolute GitHub URLs (build-time only)."""
    return _ESCAPING_LINK.sub(_replace, markdown)
