"""Startup-built search index for reference content.

`_search_content` used to walk `reference/content/**/*.md` and read every file
on every request — fine at today's volume, but it scales linearly with content
size. This module owns a single in-memory dict, built once at app startup
(see `reference/apps.py`), and rebuilt opportunistically under DEBUG so
edits during `runserver` show up without a restart.

The index is allowlisted against `reference.nav.all_nav_paths` at build time,
the same nav-membership source of truth `ReferenceView` uses to decide what's
servable. A `content/**/*.md` file that exists but isn't wired into
`REFERENCE_NAV` (an unpublished draft, a proposal doc) is excluded from the
index entirely, so it can never surface in search results even though its
own page 404s (issue #140).

The DM-only filter stays at query time so the index is shared across users;
`all_nav_paths` includes dm_only paths (it's the maximal, DM's-eye view of
the nav) so that filter still has DM content to show DMs.

The DM Rules section of the nav comes from the canon's ``nav.json`` (issue
#918), so the DEBUG fingerprint covers the canon's JSON files as well as
``nav.py``. Both are read at import; ``reference.apps`` has ``runserver``
restart when either changes, and the fingerprint then rebuilds the index.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from django.conf import settings

from reference.paths import (
    CANON_JSON_FILE_NAMES,
    CONTENT_DIR,
    DM_RULES_DIR,
    iter_content_markdown,
)

logger = logging.getLogger(__name__)

NAV_FILE = Path(__file__).parent / "nav.py"

# path_str (e.g. "dm-rules/encounter-design") -> (title, normalized_content).
# normalized_content is lower-cased once at build time so per-query filtering
# is a simple substring check.
_INDEX: dict[str, tuple[str, str]] = {}
_CONTENT_SNAPSHOT: dict[str, str] = {}
_LAST_BUILD_FINGERPRINT: tuple[tuple[str, float], ...] | None = None


def _extract_title(content: str, path_str: str) -> str:
    """First H1 wins; otherwise humanize the last path segment."""
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    slug = path_str.split("/")[-1]
    return slug.replace("-", " ").title()


def _fingerprint() -> tuple[tuple[str, float], ...]:
    """Cheap change-detection signal: (path, mtime) for every .md file, plus
    nav.py itself.

    Index membership (`all_nav_paths`) and the DM-only gate both derive from
    REFERENCE_NAV, so a nav.py edit under `runserver` - a page added, moved,
    or re-flagged dm_only - must also trigger a rebuild, not just .md edits.
    """
    entries = [
        (str(md_file), md_file.stat().st_mtime)
        for _relative_path, md_file in iter_content_markdown()
    ]
    try:
        entries.append((str(NAV_FILE), NAV_FILE.stat().st_mtime))
        for json_file in (DM_RULES_DIR / name for name in CANON_JSON_FILE_NAMES):
            if json_file.is_file():
                entries.append((str(json_file), json_file.stat().st_mtime))
    except OSError:  # pragma: no cover - defensive; nav.py ships with repo
        logger.exception("Failed to stat nav.py for fingerprinting")
    return tuple(sorted(entries))


def build_index() -> dict[str, tuple[str, str]]:
    """Walk the content tree once and return path -> (title, lower_content)."""
    global _INDEX, _CONTENT_SNAPSHOT, _LAST_BUILD_FINGERPRINT

    new_index: dict[str, tuple[str, str]] = {}
    new_content: dict[str, str] = {}
    if (
        not CONTENT_DIR.exists()
    ):  # pragma: no cover - defensive; content dir ships with repo
        _INDEX = new_index
        _CONTENT_SNAPSHOT = new_content
        _LAST_BUILD_FINGERPRINT = ()
        return _INDEX

    from .nav import REFERENCE_NAV, all_nav_paths

    allowed_paths = all_nav_paths(REFERENCE_NAV)

    # The dm-rules/ subtree comes from the canon directory, which may live
    # outside CONTENT_DIR (issue #917); its pages keep their dm-rules/ paths.
    for rel_path, md_file in iter_content_markdown():
        path_str = str(rel_path.with_suffix(""))
        if path_str not in allowed_paths:
            # Not published in REFERENCE_NAV - e.g. an unreferenced draft under
            # content/proposals/. ReferenceView 404s these; keeping them out of
            # the index means search can't leak their title/snippet either.
            continue
        try:
            content = md_file.read_text()
        except OSError:  # pragma: no cover - filesystem race / permission issue
            logger.exception("Failed to load reference content: %s", md_file)
            continue
        title = _extract_title(content, path_str)
        new_index[path_str] = (title, content.lower())
        new_content[path_str] = content

    _INDEX = new_index
    _CONTENT_SNAPSHOT = new_content
    _LAST_BUILD_FINGERPRINT = _fingerprint()
    return _INDEX


def get_index() -> dict[str, tuple[str, str]]:
    """Return the cached index, rebuilding lazily.

    In DEBUG mode we rebuild whenever any .md mtime changes so that `runserver`
    picks up edits without a restart. In production the index is built once
    at app startup; deploys restart the process.
    """
    if not _INDEX:
        return build_index()
    if settings.DEBUG and _fingerprint() != _LAST_BUILD_FINGERPRINT:
        return build_index()
    return _INDEX


def get_raw_content(path_str: str) -> str | None:
    """Original markdown body for a given path (snippet rendering)."""
    if not _CONTENT_SNAPSHOT:
        build_index()
    return _CONTENT_SNAPSHOT.get(path_str)
