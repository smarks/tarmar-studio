"""Issue #972: the GM page renders the latest page of turns and resolved threads.

Spencer's ruling, 2026-10-10: "the GM page renders the latest PAGE_SIZE turns
and resolved threads; a persistent 'Show earlier' control loads the previous
page above (no transient UI); the transcript export keeps everything."
"""

import re
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from common.pagination import PAGE_SIZE, latest_window
from common.test_helpers import BaseTestCase

from .history_pages import history_page, history_turns, resolved_threads_page
from .models import GMSession, StoryThread

#: Two more turns than a page holds, so the first two are on the page above.
TURNS = PAGE_SIZE + 2


def played(turns: int) -> list[dict]:
    """A stored history of ``turns`` player messages, each with a reply."""
    history: list[dict] = []
    for number in range(1, turns + 1):
        history.append({"role": "user", "content": f"Player move {number}."})
        history.append({"role": "assistant", "content": f"GM reply {number}."})
    return history


def shown(page: str, pattern: str) -> list[int]:
    """The turn numbers ``pattern`` (with one ``(\\d+)``) finds on ``page``."""
    return [int(number) for number in re.findall(pattern, page)]


class LatestWindowTest(BaseTestCase):
    """The rows a page counted back from the end shows."""

    def test_the_latest_page_is_the_last_rows(self):
        self.assertEqual(latest_window(120, None, 50), (70, 120))

    def test_each_earlier_page_ends_where_the_shown_one_starts(self):
        self.assertEqual(latest_window(120, 70, 50), (20, 70))
        self.assertEqual(latest_window(120, 20, 50), (0, 20))

    def test_a_short_list_is_one_page(self):
        self.assertEqual(latest_window(3, None, 50), (0, 3))
        self.assertEqual(latest_window(0, None, 50), (0, 0))

    def test_a_stale_cursor_is_clamped(self):
        self.assertEqual(latest_window(10, 99, 50), (0, 10))
        self.assertEqual(latest_window(10, -5, 50), (0, 0))


class HistoryTurnsTest(BaseTestCase):
    """A turn is a player's message and everything up to the next one."""

    def test_replies_chips_and_notices_stay_with_the_message_they_follow(self):
        visible = [
            {"role": "assistant", "kind": "notice", "content": "Filed before."},
            {"role": "user", "content": "I swing."},
            {"role": "assistant", "kind": "dice", "content": "3d6: 11"},
            {"role": "assistant", "content": "You hit."},
            {"role": "user", "content": "/roll 1d6", "typed": True},
            {"role": "assistant", "kind": "dice", "content": "1d6: 4"},
        ]

        turns = history_turns(visible)

        self.assertEqual([len(turn) for turn in turns], [1, 3, 2])
        self.assertEqual(turns[1][0]["content"], "I swing.")

    def test_a_page_holds_whole_turns_from_the_end(self):
        visible = played(5)

        page = history_page(visible, size=2)

        self.assertEqual((page.start, page.total), (3, 5))
        self.assertEqual(
            [entry["content"] for entry in page.entries],
            ["Player move 4.", "GM reply 4.", "Player move 5.", "GM reply 5."],
        )
        above = history_page(visible, before=page.start, size=2)
        self.assertEqual(above.start, 1)
        self.assertEqual(above.entries[0]["content"], "Player move 2.")


class ChatPageTest(BaseTestCase):
    """The chat renders the latest page, and the control for the one above."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("history-player")
        self.session = GMSession.objects.create(
            user=self.player, name="Long game", history=played(TURNS)
        )
        self.login_as(self.player)

    def chat(self):
        return self.client.get(reverse("gm:chat"), {"session": self.session.pk})

    def earlier(self, before: int | str):
        return self.client.get(
            reverse("gm:history_page", args=[self.session.pk]), {"before": before}
        )

    def test_the_page_renders_the_latest_turns_and_a_control(self):
        response = self.chat()

        page = response.content.decode()
        self.assertEqual(shown(page, r"Player move (\d+)\."), list(range(3, TURNS + 1)))
        self.assertIn("2 earlier turns not shown.", page)
        self.assertIn(
            f'data-earlier-url="{reverse("gm:history_page", args=[self.session.pk])}'
            '?before=2"',
            page,
        )
        # What reads the conversation still reads all of it.
        self.assertEqual(len(response.context["history"]), TURNS * 2)

    def test_a_short_session_has_no_control(self):
        self.session.history = played(3)
        self.session.save()

        page = self.chat().content.decode()

        self.assertEqual(shown(page, r"Player move (\d+)\."), [1, 2, 3])
        self.assertNotIn("data-earlier", page)

    def test_the_endpoint_renders_the_page_above_with_no_further_control(self):
        response = self.earlier(2)

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertEqual(shown(page, r"Player move (\d+)\."), [1, 2])
        self.assertEqual(shown(page, r"GM reply (\d+)\."), [1, 2])
        self.assertNotIn("data-earlier", page)

    def test_a_page_further_back_carries_the_next_control(self):
        self.session.history = played(PAGE_SIZE * 2 + 1)
        self.session.save()

        page = self.earlier(PAGE_SIZE + 1).content.decode()

        self.assertEqual(
            shown(page, r"Player move (\d+)\."), list(range(2, PAGE_SIZE + 2))
        )
        self.assertIn("1 earlier turn not shown.", page)
        self.assertIn("?before=1", page)

    def test_a_fold_on_an_earlier_page_renders_there(self):
        self.session.summary = "The party crossed the river."
        self.session.summary_covers = 2
        self.session.save()

        page = self.earlier(2).content.decode()

        self.assertIn('id="gm-fold-recap"', page)
        self.assertIn("The party crossed the river.", page)
        self.assertNotIn('id="gm-fold-recap"', self.chat().content.decode())

    def test_only_the_owner_reads_a_page(self):
        other = self.create_user("history-other")
        self.logout()
        self.login_as(other)

        self.assertEqual(self.earlier(2).status_code, 404)

    def test_a_game_master_reads_only_their_own_sessions_pages(self):
        """The chat page is the owner's for everyone, a Game Master too."""
        self.logout()
        self.login_as(self.create_dm("history-gm"))

        self.assertEqual(self.earlier(2).status_code, 404)

    def test_signing_in_comes_first(self):
        self.logout()

        response = self.earlier(2)

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_cursor_that_is_not_a_number_is_refused(self):
        for before in ("", "two", "-1", "²"):
            with self.subTest(before=before):
                self.assertEqual(self.earlier(before).status_code, 400)

    def test_a_cursor_past_the_end_shows_what_is_there(self):
        page = self.earlier(TURNS + 40).content.decode()

        self.assertEqual(shown(page, r"Player move (\d+)\."), list(range(3, TURNS + 1)))

    def test_it_only_reads(self):
        response = self.client.post(
            reverse("gm:history_page", args=[self.session.pk]) + "?before=2"
        )
        self.assertEqual(response.status_code, 405)

    def test_the_transcript_keeps_every_turn(self):
        response = self.client.get(reverse("gm:transcript", args=[self.session.pk]))

        text = response.content.decode()
        self.assertIn("Player move 1.", text)
        self.assertIn(f"Player move {TURNS}.", text)


class ResolvedThreadsPageTest(BaseTestCase):
    """The Campaign tab lists the latest page of resolved threads."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("threads-player")
        self.session = GMSession.objects.create(user=self.player, name="Threads")
        # One moment for every thread, so the order rests on the id alone.
        moment = timezone.now() - timedelta(days=1)
        self.threads = [
            StoryThread.objects.create(
                session=self.session,
                text=f"Settled matter {number}",
                resolved=True,
                created_at=moment,
            )
            for number in range(1, TURNS + 1)
        ]
        self.open_thread = StoryThread.objects.create(
            session=self.session, text="Still hanging", created_at=moment
        )
        self.login_as(self.player)

    def panel(self) -> str:
        return self.client.get(
            reverse("gm:campaign_panel", args=[self.session.pk])
        ).content.decode()

    def earlier(self, before: int | str):
        return self.client.get(
            reverse("gm:resolved_threads", args=[self.session.pk]),
            {"before": before},
        )

    def test_the_tab_lists_the_latest_page_and_counts_them_all(self):
        page = self.panel()

        self.assertEqual(
            shown(page, r"Settled matter (\d+)<"), list(range(3, TURNS + 1))
        )
        self.assertIn(f"{TURNS} resolved", page)
        self.assertIn("2 earlier resolved threads not shown.", page)
        self.assertIn(f"?before={self.threads[2].pk}", page)
        self.assertIn("Still hanging", page)

    def test_the_endpoint_renders_the_threads_above(self):
        response = self.earlier(self.threads[2].pk)

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertEqual(shown(page, r"Settled matter (\d+)<"), [1, 2])
        self.assertNotIn("data-earlier", page)

    def test_the_page_above_holds_still_while_threads_are_reopened(self):
        """Keyed on the thread, not a position: reopening one on the page
        shown moves nothing above it."""
        reopened = self.threads[10]
        reopened.resolved = False
        reopened.save()

        page = self.earlier(self.threads[2].pk).content.decode()

        self.assertEqual(shown(page, r"Settled matter (\d+)<"), [1, 2])

    def test_a_reopened_thread_still_marks_its_place(self):
        anchor = self.threads[2]
        anchor.resolved = False
        anchor.save()

        page = self.earlier(anchor.pk).content.decode()

        self.assertEqual(shown(page, r"Settled matter (\d+)<"), [1, 2])

    def test_the_service_counts_what_is_above(self):
        page = resolved_threads_page(self.session, size=5)

        self.assertEqual((page.earlier, page.total), (TURNS - 5, TURNS))
        self.assertEqual(page.earlier_before, self.threads[TURNS - 5].pk)

    def test_only_the_owner_reads_a_page(self):
        self.logout()
        self.login_as(self.create_user("threads-other"))

        self.assertEqual(self.earlier(self.threads[2].pk).status_code, 404)

    def test_a_thread_of_another_session_is_not_a_cursor(self):
        elsewhere = StoryThread.objects.create(
            session=GMSession.objects.create(user=self.player, name="Other"),
            text="Elsewhere",
            resolved=True,
        )

        self.assertEqual(self.earlier(elsewhere.pk).status_code, 404)
        self.assertEqual(self.earlier("x").status_code, 400)
