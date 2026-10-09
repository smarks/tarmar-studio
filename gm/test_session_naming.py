"""Sessions a player can tell apart, and rename where they are (issue #339).

Items 10 and 12 of the UI-expectations audit. A session used to be called
"New Session" forever, only the newest twenty were listed, and the one
control that renamed one was a command-bar button labelled "Save" that
opened the browser's prompt() and then reloaded the page.
"""

import json
from unittest.mock import patch

from django.urls import reverse

from common.test_helpers import BaseTestCase

from .models import PLACEHOLDER_SESSION_NAME, GMSession, name_from_history
from .testing import gm_replies

CHAT_URL = "/tarmar/gm/"
SAVE_URL = "/tarmar/gm/save/"
STREAM_URL = "/tarmar/gm/stream/"


class NameFromHistoryTest(BaseTestCase):
    """The naming rule itself, without a request in the way."""

    def test_it_takes_the_first_thing_the_player_said(self):
        history = [
            {"role": "assistant", "content": "Welcome."},
            {"role": "user", "content": "I want to play a thief"},
            {"role": "user", "content": "and steal a horse"},
        ]
        self.assertEqual(name_from_history(history), "I want to play a thief")

    def test_it_squeezes_a_multi_line_opening_onto_one_line(self):
        history = [{"role": "user", "content": " I sneak\n  in \n"}]
        self.assertEqual(name_from_history(history), "I sneak in")

    def test_a_long_opening_is_cut_on_a_word(self):
        opening = (
            "I would like to begin by describing the smell of the tavern in some detail"
        )
        name = name_from_history([{"role": "user", "content": opening}])
        self.assertLessEqual(len(name), 49)
        self.assertTrue(name.endswith("…"))
        self.assertFalse(name.rstrip("…").endswith(" "))
        self.assertTrue(opening.startswith(name.rstrip("…")))

    def test_one_enormous_word_is_still_cut(self):
        name = name_from_history([{"role": "user", "content": "x" * 200}])
        self.assertEqual(len(name), 49)

    def test_a_history_with_nothing_said_keeps_the_placeholder(self):
        self.assertEqual(name_from_history([]), PLACEHOLDER_SESSION_NAME)
        self.assertEqual(
            name_from_history([{"role": "assistant", "content": "hi"}]),
            PLACEHOLDER_SESSION_NAME,
        )

    def test_a_hidden_block_is_not_the_players_words(self):
        history = [
            {"role": "user", "content": "canon excerpt", "kind": "canon"},
            {"role": "user", "content": "I light a torch"},
        ]
        self.assertEqual(name_from_history(history), "I light a torch")


class SessionNamingThroughTheStreamTest(BaseTestCase):
    """A session takes its name from its opening line (item 12)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_player()
        self.login_as(self.player)

    def stream(self, body):
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat",
            side_effect=gm_replies(["ok"]),
        ):
            response = self.client.post(
                STREAM_URL, json.dumps(body), content_type="application/json"
            )
            # Drain the SSE generator so the turn is persisted, keeping what
            # it sent for a test that reads the frames.
            sent = b"".join(response.streaming_content)
            response.streaming_content = [sent]
        return response

    def test_a_new_session_is_named_after_the_first_message(self):
        self.stream({"session_id": None, "message": "I want to play a thief"})
        session = GMSession.objects.get(user=self.player)
        self.assertEqual(session.name, "I want to play a thief")

    def test_a_session_created_empty_is_named_by_its_first_message(self):
        session = GMSession.objects.create(
            name=PLACEHOLDER_SESSION_NAME, user=self.player
        )
        self.stream({"session_id": session.pk, "message": "I climb the wall"})
        session.refresh_from_db()
        self.assertEqual(session.name, "I climb the wall")

    def test_the_done_frame_names_a_session_made_before_its_first_message(self):
        """Attaching a character makes the session first (issue #829).

        The page then showed "New Session" and "0 msgs" beside the reply
        until a reload; the done frame carries what the page must show.
        """
        session = GMSession.objects.create(
            name=PLACEHOLDER_SESSION_NAME, user=self.player
        )
        response = self.stream(
            {"session_id": session.pk, "message": "I climb the wall"}
        )
        frames = [
            json.loads(chunk[len("data: ") :])
            for chunk in b"".join(response.streaming_content).decode().split("\n\n")
            if chunk.startswith("data: ")
        ]
        done = frames[-1]
        self.assertTrue(done["done"])
        self.assertEqual(done["session_name"], "I climb the wall")
        self.assertEqual(done["message_count"], 2)

    def test_a_named_session_keeps_its_name(self):
        session = GMSession.objects.create(name="The Salt Road", user=self.player)
        self.stream({"session_id": session.pk, "message": "I climb the wall"})
        session.refresh_from_db()
        self.assertEqual(session.name, "The Salt Road")


class SessionSidebarTest(BaseTestCase):
    """Item 12: every session is listed, not the newest twenty."""

    def setUp(self):
        super().setUp()
        self.player = self.create_player()
        self.login_as(self.player)

    def test_more_than_twenty_sessions_all_appear(self):
        for index in range(25):
            GMSession.objects.create(name=f"Session {index:02d}", user=self.player)
        response = self.client.get(CHAT_URL)
        self.assertEqual(len(response.context["sessions"]), 25)
        self.assertContains(response, "Session 00")
        self.assertContains(response, "Session 24")

    def test_another_players_sessions_are_not_listed(self):
        stranger = self.create_user(username="stranger")
        GMSession.objects.create(name="Not Yours", user=stranger)
        response = self.client.get(CHAT_URL)
        self.assertNotContains(response, "Not Yours")


class SessionRenameTest(BaseTestCase):
    """Item 10: the name is a field on the page, and it saves in place."""

    def setUp(self):
        super().setUp()
        self.player = self.create_player()
        self.login_as(self.player)
        self.session = GMSession.objects.create(name="The Salt Road", user=self.player)

    def post(self, body):
        return self.client.post(
            SAVE_URL, json.dumps(body), content_type="application/json"
        )

    def test_the_chat_page_carries_an_editable_name_field(self):
        response = self.client.get(CHAT_URL, {"session": self.session.pk})
        self.assertContains(response, 'id="session-name"')
        self.assertContains(response, 'class="autosave session-name-input"')
        self.assertContains(response, 'value="The Salt Road"')

    def test_the_command_bar_button_says_what_it_does(self):
        response = self.client.get(CHAT_URL, {"session": self.session.pk})
        self.assertContains(response, ">Rename</button>")
        self.assertNotContains(response, 'title="Save session"')

    def test_a_page_with_no_session_explains_where_the_name_comes_from(self):
        response = self.client.get(CHAT_URL)
        self.assertNotContains(response, 'id="session-name"')
        self.assertContains(response, "takes its name from your")

    def test_a_rename_sticks(self):
        response = self.post({"session_id": self.session.pk, "name": "  Deep Wells  "})
        self.assertEqual(response.json(), {"ok": True, "name": "Deep Wells"})
        self.session.refresh_from_db()
        self.assertEqual(self.session.name, "Deep Wells")

    def test_an_empty_name_is_refused_rather_than_ignored(self):
        response = self.post({"session_id": self.session.pk, "name": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("needs a name", response.json()["error"])
        self.session.refresh_from_db()
        self.assertEqual(self.session.name, "The Salt Road")

    def test_an_over_long_name_is_refused(self):
        response = self.post({"session_id": self.session.pk, "name": "x" * 500})
        self.assertEqual(response.status_code, 400)
        self.assertIn("characters or fewer", response.json()["error"])

    def test_somebody_elses_session_cannot_be_renamed(self):
        stranger = self.create_user(username="stranger")
        theirs = GMSession.objects.create(name="Not Yours", user=stranger)
        response = self.post({"session_id": theirs.pk, "name": "Mine Now"})
        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.name, "Not Yours")

    def test_the_name_field_declares_the_columns_own_limit(self):
        response = self.client.get(CHAT_URL, {"session": self.session.pk})
        limit = GMSession._meta.get_field("name").max_length
        self.assertContains(response, f'maxlength="{limit}"')


class UrlLiteralTest(BaseTestCase):
    """The literals above are the routes, checked rather than assumed."""

    def test_the_url_literals_match_the_routes(self):
        self.assertEqual(reverse("gm:chat"), CHAT_URL)
        self.assertEqual(reverse("gm:save"), SAVE_URL)
        self.assertEqual(reverse("gm:stream"), STREAM_URL)
