"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

import copy
from unittest.mock import patch

from django.test import TestCase

from common.test_helpers import BaseTestCase


def _mark_dm_only(nav_items, slug_path):
    """Deep-copy `nav_items` with the node at `slug_path` flagged dm_only.

    Used to construct a dm_only page outside dm-rules/ for tests, since no
    such page exists in the real REFERENCE_NAV (issue #166).
    """
    if not slug_path:
        raise ValueError("slug_path must not be empty")
    nav_copy = copy.deepcopy(nav_items)
    node_list = nav_copy
    node: dict = {}
    for slug in slug_path:
        node = next(item for item in node_list if item["slug"] == slug)
        node_list = node.get("children", [])
    node["dm_only"] = True
    return nav_copy


class ReferenceSearchHelpersTest(TestCase):
    """Tests for reference search helper functions."""

    def test_extract_title_from_h1(self):
        """_extract_title extracts H1 from markdown."""
        from reference.search_index import _extract_title

        content = "# My Title\n\nSome content"
        self.assertEqual(_extract_title(content, "some-path"), "My Title")

    def test_extract_title_fallback_to_path(self):
        """_extract_title falls back to path when no H1."""
        from reference.search_index import _extract_title

        content = "Some content without H1"
        self.assertEqual(_extract_title(content, "my-page-name"), "My Page Name")

    def test_extract_snippet_with_match(self):
        """_extract_snippet returns context around match."""
        from reference.views import _extract_snippet

        content = "Some text before the keyword and some text after"
        snippet = _extract_snippet(content, "keyword", context_chars=10)
        self.assertIn("keyword", snippet)
        self.assertTrue(snippet.startswith("..."))

    def test_extract_snippet_no_match_short_content(self):
        """_extract_snippet returns full content when short and no match."""
        from reference.views import _extract_snippet

        content = "Short content"
        snippet = _extract_snippet(content, "notfound")
        self.assertEqual(snippet, "Short content")

    def test_extract_snippet_no_match_long_content(self):
        """_extract_snippet returns truncated content when long and no match."""
        from reference.views import _extract_snippet

        content = "x" * 300
        snippet = _extract_snippet(content, "notfound")
        self.assertEqual(len(snippet), 203)  # 200 chars + "..."
        self.assertTrue(snippet.endswith("..."))


class ReferenceSearchIndexTest(BaseTestCase):
    """Tests for the in-memory search index built at app startup."""

    def setUp(self):
        super().setUp()
        # Each test starts with a freshly built index so they don't pollute each
        # other across the suite ordering.
        from reference import search_index

        search_index.build_index()
        self.search_index = search_index

    def test_build_index_returns_dict_keyed_by_path(self):
        index = self.search_index.build_index()
        # The real fixture content includes at least overview.md.
        self.assertIn("overview", index)
        title, normalized = index["overview"]
        self.assertIsInstance(title, str)
        # Normalized content is lower-cased so queries can be a substring check.
        self.assertEqual(normalized, normalized.lower())

    def test_get_index_uses_cache_without_rebuild(self):
        first = self.search_index.get_index()
        # Same object identity means no rebuild happened.
        self.assertIs(self.search_index.get_index(), first)

    def test_get_raw_content_returns_original_markdown(self):
        raw = self.search_index.get_raw_content("overview")
        self.assertIsNotNone(raw)
        assert raw is not None  # narrow for the type checker
        # Raw content is the actual markdown (mixed case preserved), unlike the
        # lower-cased copy used for query matching.
        self.assertIn("#", raw)

    def test_get_raw_content_unknown_path_returns_none(self):
        self.assertIsNone(self.search_index.get_raw_content("not-a-real-page"))

    def test_search_view_uses_cached_index_without_walking_disk(self):
        """End-to-end: search hits return data and the index is not rebuilt."""
        from unittest.mock import patch

        # Watch the index build itself (issue #450). This used to patch
        # pathlib.Path.read_text for the whole process, which also broke any
        # other reader in the request -- gm.maps reads the gazetteer -- so the
        # test failed on its own and passed only when a earlier test had
        # already cached those names.
        with patch.object(
            self.search_index, "build_index", wraps=self.search_index.build_index
        ) as build_mock:
            response = self.client.get("/tarmar/reference/search/?q=tarmar")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(build_mock.call_count, 0)

    def test_get_index_lazy_builds_when_empty(self):
        """First call after a clear rebuilds the index automatically."""
        self.search_index._INDEX.clear()
        self.search_index._CONTENT_SNAPSHOT.clear()
        index = self.search_index.get_index()
        self.assertGreater(len(index), 0)

    def test_get_raw_content_lazy_builds_when_snapshot_missing(self):
        self.search_index._CONTENT_SNAPSHOT.clear()
        raw = self.search_index.get_raw_content("overview")
        self.assertIsNotNone(raw)

    def test_get_index_rebuilds_in_debug_when_mtime_changes(self):
        """Under DEBUG, an mtime change triggers a rebuild on next get_index."""
        from django.test import override_settings

        # Force the fingerprint to a stale value so get_index detects "change".
        self.search_index._LAST_BUILD_FINGERPRINT = (("/nope.md", 0.0),)
        with override_settings(DEBUG=True):
            index = self.search_index.get_index()
        self.assertGreater(len(index), 0)
        # After rebuild, the fingerprint should match the real filesystem again.
        self.assertEqual(
            self.search_index._LAST_BUILD_FINGERPRINT,
            self.search_index._fingerprint(),
        )

    def test_build_index_excludes_content_not_in_nav(self):
        """A real, unpublished draft under content/proposals/ isn't indexed.

        `content/proposals/d20-combat-resolution-spec.md` exists on disk but
        is not wired into REFERENCE_NAV, so ReferenceView 404s it. The index
        must not include it either - see issue #140.
        """
        index = self.search_index.build_index()
        self.assertNotIn("proposals/d20-combat-resolution-spec", index)

    def test_build_index_includes_nav_pages(self):
        """Ordinary published nav pages remain indexed."""
        index = self.search_index.build_index()
        self.assertIn("overview", index)
        self.assertIn("public-rules/combat", index)

    def test_build_index_includes_dm_only_nav_pages(self):
        """DM-only nav pages stay in the shared index at build time.

        The index is built once and shared across all users, so DM-only
        content must still be present here - the actual player/DM split
        happens at query time in `_search_content`.
        """
        index = self.search_index.build_index()
        self.assertIn("dm-rules/magic-mechanics/mana-pool", index)

    def test_search_excludes_non_nav_draft_content(self):
        """End-to-end: a non-nav draft's title/snippet never reach search.

        "combat resolution" only appears in the unpublished
        content/proposals/d20-combat-resolution-spec.md draft.
        """
        response = self.client.get("/tarmar/reference/search/?q=combat+resolution")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "d20 Combat Resolution")
        self.assertNotContains(response, "proposals")

    def test_fingerprint_includes_nav_file(self):
        """The fingerprint used for DEBUG auto-rebuild covers nav.py.

        Index membership (`all_nav_paths`) and the DM-only search gate both
        derive from REFERENCE_NAV, so a nav.py edit under `runserver` must be
        able to trigger a rebuild too, not just .md edits (issue #166).
        """
        fingerprint = self.search_index._fingerprint()
        stamped_paths = {path for path, _mtime in fingerprint}
        self.assertIn(str(self.search_index.NAV_FILE), stamped_paths)

    def test_get_index_rebuilds_in_debug_when_nav_mtime_changes(self):
        """Under DEBUG, a nav.py-only mtime change also triggers a rebuild.

        Mirrors test_get_index_rebuilds_in_debug_when_mtime_changes above but
        stales only the nav.py entry, proving the fingerprint - not just the
        .md entries - drives the rebuild decision.
        """
        from django.test import override_settings

        real_fingerprint = self.search_index._fingerprint()
        stale_fingerprint = tuple(
            (path, mtime - 1.0)
            if path == str(self.search_index.NAV_FILE)
            else (path, mtime)
            for path, mtime in real_fingerprint
        )
        self.search_index._LAST_BUILD_FINGERPRINT = stale_fingerprint
        with override_settings(DEBUG=True):
            index = self.search_index.get_index()
        self.assertGreater(len(index), 0)
        self.assertEqual(
            self.search_index._LAST_BUILD_FINGERPRINT,
            self.search_index._fingerprint(),
        )


class SearchDmOnlyOutsideDmRulesTest(BaseTestCase):
    """Issue #166: the search gate must match ReferenceView's, not just dm-rules/.

    `_search_content` used to gate on `path.startswith("dm-rules")` alone,
    while `ReferenceView` gates on that OR `collect_dm_only_paths(...)`.
    Every real dm_only page happens to live under dm-rules/, so the two
    checks have been equivalent so far - this constructs the one case where
    they diverge: a dm_only page outside dm-rules/. Built by patching
    REFERENCE_NAV to flag the real public-rules/combat page dm_only (no new
    fixture content needed) and rebuilding the shared index against it.
    """

    PATH = "public-rules/combat"
    QUERY = "five seconds"  # unique phrase in content/public-rules/combat.md

    def setUp(self):
        super().setUp()
        from reference import search_index
        from reference.nav import REFERENCE_NAV

        patched_nav = _mark_dm_only(REFERENCE_NAV, ["public-rules", "combat"])
        nav_patcher = patch("reference.nav.REFERENCE_NAV", patched_nav)
        nav_patcher.start()
        self.addCleanup(nav_patcher.stop)
        search_index.build_index()
        # Restore the real index for tests that run after this one, since the
        # index is process-global state built from the (unpatched) real nav.
        self.addCleanup(search_index.build_index)

    def test_hidden_from_players(self):
        """A dm_only page outside dm-rules/ is excluded for a non-DM query."""
        from reference.views import _search_content

        paths = [result["path"] for result in _search_content(self.QUERY, is_dm=False)]
        self.assertNotIn(self.PATH, paths)

    def test_visible_to_dms(self):
        """The same page still surfaces for a DM query."""
        from reference.views import _search_content

        paths = [result["path"] for result in _search_content(self.QUERY, is_dm=True)]
        self.assertIn(self.PATH, paths)

    def test_hidden_from_player_end_to_end(self):
        # The query text itself gets echoed back into the search box
        # regardless of results, so assert on the result link/count instead
        # of the raw query string.
        player = self.create_player()
        self.login_as(player)
        response = self.client.get("/tarmar/reference/search/", {"q": self.QUERY})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f"/tarmar/reference/{self.PATH}/")
        self.assertContains(response, "0 results")

    def test_visible_to_dm_end_to_end(self):
        dm = self.create_dm()
        self.login_as(dm)
        response = self.client.get("/tarmar/reference/search/", {"q": self.QUERY})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/tarmar/reference/{self.PATH}/")


class EqualTitleOrderTest(TestCase):
    """Results with the same title come in path order, not filesystem order."""

    def test_equal_titles_are_ordered_by_path(self) -> None:
        from reference.views import _search_content

        for query, title in (("runaway", "runaway"), ("magic", "magic")):
            with self.subTest(query=query):
                results = _search_content(query, is_dm=True)
                tied_paths = [
                    result["path"]
                    for result in results
                    if result["title"].lower() == title
                ]
                self.assertGreater(len(tied_paths), 1)
                self.assertEqual(tied_paths, sorted(tied_paths))
