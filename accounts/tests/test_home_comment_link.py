"""The home page's link row ends with a way to leave a comment.

"Leave a comment" sits after "Play" in the same row and opens the public
repository's issue forms (bug, feature request, general comment); the code
repository is private. The address is built from REPOSITORY_PUBLIC_URL.
"""

import re
from unittest.mock import patch

from common.test_helpers import BaseTestCase

HOME_URL = "/tarmar/"
PUBLIC_REPOSITORY_URL = "https://github.com/smarks/tarmar-studio"
PRIVATE_REPOSITORY_URL = "https://github.com/smarks/tarmar-studio-private"
ISSUES_URL = "https://github.com/smarks/tarmar-studio/issues/new/choose"


class HomeCommentLinkTest(BaseTestCase):
    """Signed in or out, the nav row offers the issues page last."""

    def nav_links(self) -> list[tuple[str, str]]:
        """The home nav's links as (opening tag, text) pairs, in order."""
        page = self.client.get(HOME_URL).content.decode()
        nav = re.search(r'<nav class="home-nav">(.*?)</nav>', page, re.DOTALL)
        assert nav is not None, "home page has no home-nav"
        return re.findall(r"(<a [^>]*>)([^<]*)</a>", nav.group(1))

    def assert_comment_link_follows_play(self) -> None:
        links = self.nav_links()
        texts = [text for _tag, text in links]
        self.assertEqual(texts[-2:], ["Play", "Leave a comment"])
        self.assertIn(f'href="{ISSUES_URL}"', links[-1][0])

    def test_signed_out_visitor_sees_the_link(self) -> None:
        self.assert_comment_link_follows_play()

    def test_signed_in_player_sees_the_link(self) -> None:
        self.login_as(self.create_user("comment-player"))
        self.assert_comment_link_follows_play()

    def test_link_opens_in_a_new_tab_without_an_opener(self) -> None:
        tag = self.nav_links()[-1][0]
        self.assertIn('target="_blank"', tag)
        self.assertIn('rel="noopener"', tag)


class FooterCommitLinkTest(BaseTestCase):
    """The footer's version is a link only for people who can open it (#914).

    The deployed commit lives in the private repository; the public one is a
    snapshot that does not hold it. Everyone gets the public repository's root
    as "Source code", and the commit id is plain text unless the viewer is a
    Game Master or admin, who can open the private commit page.
    """

    COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
    COMMIT_URL = f"{PRIVATE_REPOSITORY_URL}/commit/{COMMIT_SHA}"

    def footer(self) -> str:
        """The home page's footer markup, with a known commit checked out."""
        with patch(
            "common.context_processors._get_git_info",
            return_value={"sha": self.COMMIT_SHA, "date": "2026-10-08"},
        ):
            page = self.client.get(HOME_URL).content.decode()
        footer = re.search(r"<footer>(.*?)</footer>", page, re.DOTALL)
        assert footer is not None, "page has no footer"
        return footer.group(1)

    def assert_public_view(self, footer: str) -> None:
        self.assertIn(f'<a href="{PUBLIC_REPOSITORY_URL}"', footer)
        self.assertIn("Version: 0123456</span>", footer)
        self.assertNotIn("/commit/", footer)
        self.assertNotIn(PRIVATE_REPOSITORY_URL, footer)

    def test_signed_out_visitor_sees_the_public_root_and_the_id_as_text(self) -> None:
        self.assert_public_view(self.footer())

    def test_player_sees_the_public_root_and_the_id_as_text(self) -> None:
        self.login_as(self.create_user("footer-player"))
        self.assert_public_view(self.footer())

    def test_game_master_sees_the_private_commit_link(self) -> None:
        self.login_as(self.create_dm("footer-game-master"))
        footer = self.footer()
        self.assertIn(f'<a href="{self.COMMIT_URL}"', footer)
        self.assertIn(f'<a href="{PUBLIC_REPOSITORY_URL}"', footer)

    def test_admin_sees_the_private_commit_link(self) -> None:
        self.login_as(self.create_admin("footer-admin"))
        self.assertIn(f'<a href="{self.COMMIT_URL}"', self.footer())

    def test_no_checked_out_commit_means_no_version(self) -> None:
        self.login_as(self.create_dm("footer-no-commit"))
        with patch(
            "common.context_processors._get_git_info",
            return_value={"sha": "", "date": ""},
        ):
            page = self.client.get(HOME_URL).content.decode()
        self.assertNotIn("Version:", page)
        self.assertNotIn("/commit/", page)
        self.assertIn(f'<a href="{PUBLIC_REPOSITORY_URL}"', page)

    def test_addresses_come_from_the_settings(self) -> None:
        with self.settings(
            REPOSITORY_PUBLIC_URL="https://example.test/public",
            REPOSITORY_PRIVATE_URL="https://example.test/private",
        ):
            self.assertIn('<a href="https://example.test/public"', self.footer())
            # The comment link is built from the same setting, so the two
            # cannot point at different repositories.
            home = self.client.get(HOME_URL).content.decode()
            self.assertIn('href="https://example.test/public/issues/new/choose"', home)
            self.login_as(self.create_dm("footer-settings-gm"))
            self.assertIn(
                f'<a href="https://example.test/private/commit/{self.COMMIT_SHA}"',
                self.footer(),
            )
