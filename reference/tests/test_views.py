"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from django.utils.html import escape

from common.test_helpers import BaseTestCase
from ruins.hooks import load_hooks


class ReferenceViewTest(BaseTestCase):
    """Tests for the reference section."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.admin = self.create_admin()
        self.player = self.create_player()

    def test_reference_index_accessible(self):
        """Reference index page is accessible."""
        response = self.client.get("/tarmar/reference/")
        self.assertEqual(response.status_code, 200)

    def test_reference_has_sidebar(self):
        """Reference page has sidebar navigation."""
        response = self.client.get("/tarmar/reference/")
        self.assertContains(response, 'class="sidebar"')

    def test_reference_has_nav_items(self):
        """Reference page has navigation items."""
        response = self.client.get("/tarmar/reference/")
        self.assertContains(response, "Overview")
        self.assertContains(response, "The World of Tarmar")
        self.assertContains(response, "Public Rules")

    def test_reference_subpage_accessible(self):
        """Reference subpage is accessible and loads markdown content."""
        response = self.client.get("/tarmar/reference/the-world-of-tarmar/")
        self.assertEqual(response.status_code, 200)
        # Check for content only in the markdown file, not navigation
        self.assertContains(response, "God King Emperor")
        self.assertNotContains(response, "Content not found")

    def test_reference_nested_page_accessible(self):
        """Reference nested page is accessible and loads markdown content."""
        response = self.client.get("/tarmar/reference/public-rules/combat/")
        self.assertEqual(response.status_code, 200)
        # Check for content only in the markdown file, not navigation
        self.assertContains(response, "five seconds")
        self.assertNotContains(response, "Content not found")

    def test_reference_deep_nested_page_accessible(self):
        """Reference deeply nested page is accessible and loads markdown content."""
        response = self.client.get(
            "/tarmar/reference/public-rules/combat/turn-sequence/"
        )
        self.assertEqual(response.status_code, 200)
        # Check for content only in the markdown file, not navigation
        self.assertContains(response, "Initiative")
        self.assertNotContains(response, "Content not found")

    def test_reference_skill_tracks_table_renders(self):
        """Skill tracks page renders tables correctly."""
        response = self.client.get(
            "/tarmar/reference/public-rules/characters/prior-experience/skill-tracks/"
        )
        self.assertEqual(response.status_code, 200)
        # Check that the table HTML is present, annotated with its column count
        self.assertContains(response, '<table data-columns="9">')
        self.assertContains(response, "Merchant")
        self.assertContains(response, "Campaigner")
        self.assertNotContains(response, "Content not found")

    def test_reference_fourth_level_nav_visible(self):
        """Fourth level navigation items are visible (e.g., Prior Experience children)."""
        response = self.client.get(
            "/tarmar/reference/public-rules/characters/prior-experience/"
        )
        self.assertEqual(response.status_code, 200)
        # These are 4th level nav items under Prior Experience
        self.assertContains(response, "Aging")
        self.assertContains(response, "Survivability")
        self.assertContains(response, "Skill Tracks")

    def test_reference_has_hamburger_menu(self):
        """Reference page has hamburger menu."""
        response = self.client.get("/tarmar/reference/")
        self.assertContains(response, 'id="menuToggle"')

    def test_reference_not_found_shows_message(self):
        """Reference page shows message for non-existent content."""
        response = self.client.get("/tarmar/reference/nonexistent/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Content not found")

    def test_dm_rules_hidden_from_anonymous(self):
        """DM Rules are not shown in nav for anonymous users."""
        response = self.client.get("/tarmar/reference/")
        self.assertNotContains(response, "DM Rules")

    def test_dm_rules_hidden_from_player(self):
        """DM Rules are not shown in nav for players."""
        self.login_as(self.player)
        response = self.client.get("/tarmar/reference/")
        self.assertNotContains(response, "DM Rules")

    def test_dm_rules_shown_to_dm(self):
        """DM Rules are shown in nav for DMs."""
        self.login_as(self.dm)
        response = self.client.get("/tarmar/reference/")
        self.assertContains(response, "DM Rules")

    def test_dm_rules_shown_to_admin(self):
        """DM Rules are shown in nav for admins."""
        self.login_as(self.admin)
        response = self.client.get("/tarmar/reference/")
        self.assertContains(response, "DM Rules")

    def test_dm_rules_access_denied_for_player(self):
        """Players cannot access DM Rules content."""
        self.login_as(self.player)
        response = self.client.get("/tarmar/reference/dm-rules/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Access Denied")
        self.assertContains(response, "do not have permission")

    def test_dm_rules_access_denied_for_anonymous(self):
        """Anonymous users cannot access DM Rules content."""
        response = self.client.get("/tarmar/reference/dm-rules/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Access Denied")

    def test_dm_can_access_dm_rules(self):
        """DMs can access DM Rules content."""
        self.login_as(self.dm)
        response = self.client.get("/tarmar/reference/dm-rules/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Access Denied")

    def test_admin_can_access_dm_rules(self):
        """Admins can access DM Rules content."""
        self.login_as(self.admin)
        response = self.client.get("/tarmar/reference/dm-rules/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Access Denied")

    def test_hamburger_menu_has_reference_link(self):
        """Hamburger menu has Reference link."""
        response = self.client.get("/tarmar/")
        self.assertContains(response, "/tarmar/reference/")

    def test_path_traversal_blocked(self):
        """Path traversal attempts are blocked."""
        response = self.client.get("/tarmar/reference/../../../etc/passwd/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Content not found")
        self.assertNotContains(response, "root:")

    def test_path_traversal_with_encoded_chars_blocked(self):
        """Path traversal with URL-encoded characters is blocked."""
        response = self.client.get("/tarmar/reference/..%2F..%2Fetc%2Fpasswd/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Content not found")

    def test_path_traversal_is_logged(self):
        """A rejected traversal attempt logs a warning for audit visibility."""
        with self.assertLogs("reference.views", level="WARNING") as captured:
            self.client.get("/tarmar/reference/..%2F..%2Fetc%2Fpasswd/")
        self.assertTrue(
            any("path-traversal" in line for line in captured.output),
            captured.output,
        )

    def test_search_page_accessible(self):
        """Search page is accessible."""
        response = self.client.get("/tarmar/reference/search/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search Results")

    def test_search_form_in_sidebar(self):
        """Reference pages have search form in sidebar."""
        response = self.client.get("/tarmar/reference/")
        self.assertContains(response, 'action="/tarmar/reference/search/"')
        self.assertContains(response, 'name="q"')

    def test_search_returns_results(self):
        """Search returns matching results."""
        response = self.client.get("/tarmar/reference/search/?q=attack")
        self.assertEqual(response.status_code, 200)
        # Should find results in combat-related pages
        self.assertContains(response, "result")

    def test_search_short_query_shows_message(self):
        """Search with short query shows message."""
        response = self.client.get("/tarmar/reference/search/?q=a")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least 2 characters")

    def test_search_empty_query(self):
        """Search with empty query shows prompt."""
        response = self.client.get("/tarmar/reference/search/?q=")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a search term")

    def test_search_no_results(self):
        """Search with no matches shows message."""
        response = self.client.get("/tarmar/reference/search/?q=xyznonexistent123")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No pages found")

    def test_search_results_link_to_pages(self):
        """Search results contain links to matching pages."""
        response = self.client.get("/tarmar/reference/search/?q=movement")
        self.assertEqual(response.status_code, 200)
        # Should have a link to the movement page
        self.assertContains(
            response, "/tarmar/reference/public-rules/combat/action-options/movement/"
        )

    def test_search_dm_content_hidden_from_player(self):
        """DM-only content not in search results for players."""
        self.login_as(self.player)
        # Search for something likely in DM rules
        response = self.client.get("/tarmar/reference/search/?q=mana")
        self.assertEqual(response.status_code, 200)
        # Should not find dm-rules pages
        self.assertNotContains(response, "dm-rules")

    def test_search_dm_content_visible_to_dm(self):
        """DM-only content appears in search results for DMs."""
        self.login_as(self.dm)
        response = self.client.get("/tarmar/reference/search/?q=mana")
        self.assertEqual(response.status_code, 200)
        # Should find dm-rules pages about mana
        self.assertContains(response, "dm-rules")


class HandToHandGrapplingPageTest(BaseTestCase):
    """Issue #227: hand-to-hand/grapple rules are a published, player-facing page."""

    def test_page_accessible_to_anonymous(self):
        """The page is public rules content - no login required."""
        response = self.client.get(
            "/tarmar/reference/public-rules/combat/action-options/"
            "hand-to-hand-and-grappling/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Initiating a Grapple")
        self.assertNotContains(response, "Content not found")

    def test_nav_lists_the_page_for_anonymous(self):
        """The nav entry appears alongside its Action Options siblings."""
        response = self.client.get(
            "/tarmar/reference/public-rules/combat/action-options/"
        )
        self.assertContains(response, "Hand-to-Hand &amp; Grappling")


class SpellDescriptionsPageTest(BaseTestCase):
    """Issue #228: every spell gets a description and a WIS/INT affinity."""

    def test_page_accessible_to_anonymous(self):
        response = self.client.get(
            "/tarmar/reference/public-rules/magic/spell-descriptions/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Affinity")
        self.assertContains(response, "Missile")
        self.assertNotContains(response, "Content not found")

    def test_nav_lists_the_page_for_anonymous(self):
        response = self.client.get("/tarmar/reference/public-rules/magic/")
        self.assertContains(response, "Spell Descriptions")


class LowExperienceHooksPageTest(BaseTestCase):
    """Issue #229: low-experience adventure hooks are a DM-only GM tool."""

    URL = "/tarmar/reference/dm-rules/gm-tools/low-experience-hooks/"

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()

    def test_hidden_from_anonymous(self):
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Access Denied")

    def test_hidden_from_player(self):
        self.login_as(self.player)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Access Denied")

    def test_accessible_to_dm(self):
        self.login_as(self.dm)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 200)
        # The first hook the loaded canon publishes, whichever canon that is.
        self.assertContains(response, escape(load_hooks()[0].title))
        self.assertNotContains(response, "Content not found")

    def test_dm_only_nav_entry_hidden_from_player(self):
        self.login_as(self.player)
        response = self.client.get("/tarmar/reference/")
        self.assertNotContains(response, "Hooks for New Characters")

    def test_nav_entry_visible_to_dm(self):
        self.login_as(self.dm)
        response = self.client.get("/tarmar/reference/dm-rules/gm-tools/")
        self.assertContains(response, "Hooks for New Characters")


class GmToolsLinksTest(BaseTestCase):
    """The GM Tools reference page gathers ALL the GM tooling (issue #215):
    the interactive apps, not just the markdown table pages."""

    def test_gm_tools_page_links_the_interactive_tools(self):
        self.login_as(self.create_dm())
        response = self.client.get("/tarmar/reference/dm-rules/gm-tools/")
        self.assertEqual(response.status_code, 200)
        for url in (
            "/tarmar/gm/",
            "/tarmar/battles/",
            "/tarmar/gm/bestiary/",
            "/tarmar/gm/encounters/",
            "/tarmar/gm/ruins/",
        ):
            self.assertContains(response, f'href="{url}"')


class FirstSessionPageTest(BaseTestCase):
    """Issue #276: the first-session on-ramp is a public, player-facing page."""

    URL = "/tarmar/reference/first-session/"

    def test_page_accessible_to_anonymous(self):
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What a First Session Looks Like")
        self.assertContains(response, "Where Journeys Begin")
        self.assertNotContains(response, "Content not found")
        self.assertNotContains(response, "Access Denied")

    def test_page_links_the_create_flow_and_the_generator(self):
        response = self.client.get(self.URL)
        self.assertContains(response, 'href="/tarmar/characters/create/"')
        self.assertContains(response, 'href="/tarmar/gm/hooks/"')

    def test_nav_lists_the_page_for_anonymous(self):
        response = self.client.get("/tarmar/reference/")
        self.assertContains(response, "Your First Session")
