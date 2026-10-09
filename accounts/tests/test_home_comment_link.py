"""The home page's link row ends with a way to leave a comment.

"Leave a comment" sits after "Play" in the same row and opens the issues of
the public feedback repository (the code repository is private), so a player
with a problem or an idea knows where to put it.
"""

import re
from unittest.mock import patch

from common.test_helpers import BaseTestCase

HOME_URL = "/tarmar/"
REPOSITORY_URL = "https://github.com/smarks/tarmar-studio"
ISSUES_URL = "https://github.com/smarks/tarmar-feedback/issues"


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
    """The footer's version link is built from the code repository's address."""

    def test_version_link_points_at_a_commit_in_the_repository(self) -> None:
        commit_sha = "0123456789abcdef0123456789abcdef01234567"
        with patch(
            "common.context_processors._get_git_info",
            return_value={"sha": commit_sha, "date": "2026-10-08"},
        ):
            page = self.client.get(HOME_URL).content.decode()
        self.assertIn(f'href="{REPOSITORY_URL}/commit/{commit_sha}"', page)
