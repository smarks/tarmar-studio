"""A session's mode is chosen by the person at it, not by their account (#372).

Spencer is a DM and an admin, so every session he opened was answered by the
aide from #353 — the prompt instructed never to set a scene, never to ask what
he does, and to answer and stop. He could not play a game with his own tool.

The fix is to stop making one answer serve two questions:

* **Authority** — what may be written down — stays with the account, exactly
  where #352 put it. Nothing typed into the chat and nothing chosen on the
  page can reach it. :class:`AuthorityAndMannerAreOrthogonalTest` is the test
  that holds that line.
* **Manner** — run the game, or assist someone running one — is a property of
  the session, defaulted from the role and changeable by whoever is sitting
  at it.

The rest of this module covers what that costs and what it must not break:
four prompt strings still built once at import over a shared prefix (#330,
#331), a switch that keeps the conversation, and a control on the page rather
than a badge that only reports.
"""

import json
from unittest.mock import patch

from common.test_helpers import BaseTestCase, create_character

from .engine import (
    GM_MODES,
    MODE_AIDE,
    MODE_PLAY,
    SECTION_DM_AIDE,
    SECTION_DM_AUTHORITY,
    aide_system_prompt,
    default_mode_for,
    dm_play_system_prompt,
    dm_system_prompt,
    normalise_mode,
    system_prompt,
    system_prompt_for,
    system_prompts_by_authority_and_mode,
)
from .messages import build_mode_switch_message
from .models import MODE_DESCRIPTIONS, MODE_LABELS, GMSession
from .retrieval import MODE_KIND, is_hidden_message
from .services import default_mode_for_user, get_system_message
from .testing import gm_replies

CHAT_URL = "/tarmar/gm/"
MODE_URL = "/tarmar/gm/mode/"
STREAM_URL = "/tarmar/gm/stream/"


class PromptMatrixTest(BaseTestCase):
    """Four fixed strings over one shared prefix, joined once at import."""

    def test_every_combination_has_its_own_constant(self):
        self.assertEqual(
            system_prompts_by_authority_and_mode(),
            {
                (False, MODE_PLAY): system_prompt(),
                (False, MODE_AIDE): aide_system_prompt(),
                (True, MODE_PLAY): dm_play_system_prompt(),
                (True, MODE_AIDE): dm_system_prompt(),
            },
        )

    def test_nothing_is_assembled_per_call(self):
        """Issues #330/#331: the same object every time, never a new join."""
        for is_game_master in (False, True):
            for mode in GM_MODES:
                with self.subTest(is_game_master=is_game_master, mode=mode):
                    first = system_prompt_for(is_game_master, mode)
                    self.assertIs(first, system_prompt_for(is_game_master, mode))
                    self.assertIs(
                        first,
                        system_prompts_by_authority_and_mode()[(is_game_master, mode)],
                    )

    def test_all_four_open_with_the_same_bytes(self):
        """The expensive ~7k-token prefix is shared, so one warm-up serves all."""
        for prompt in system_prompts_by_authority_and_mode().values():
            with self.subTest(prompt=prompt[:40]):
                self.assertTrue(prompt.startswith(system_prompt()))

    def test_the_addenda_follow_the_shared_prompt_in_one_order(self):
        """Authority, then aide, after the shared prompt (issues #352, #353)."""
        self.assertEqual(
            dm_system_prompt(),
            "\n\n".join((system_prompt(), SECTION_DM_AUTHORITY, SECTION_DM_AIDE)),
        )
        self.assertEqual(
            dm_play_system_prompt(),
            "\n\n".join((system_prompt(), SECTION_DM_AUTHORITY)),
        )
        self.assertEqual(
            aide_system_prompt(), "\n\n".join((system_prompt(), SECTION_DM_AIDE))
        )

    def test_playing_adds_nothing(self):
        """Play mode is the prompt's own voice; only the aide is an override."""
        self.assertNotIn(SECTION_DM_AIDE, system_prompt())
        self.assertNotIn(SECTION_DM_AIDE, dm_play_system_prompt())

    def test_an_omitted_mode_takes_the_roles_default(self):
        self.assertIs(system_prompt_for(True), dm_system_prompt())
        self.assertIs(system_prompt_for(False), system_prompt())

    def test_a_mode_nobody_offers_falls_back_rather_than_raising(self):
        """A mistyped mode is a request for the ordinary session, not a 500."""
        for junk in (None, "", "PLAY", "gm", 7, {"mode": "play"}):
            with self.subTest(junk=junk):
                self.assertEqual(normalise_mode(junk, is_game_master=True), MODE_AIDE)
                self.assertEqual(normalise_mode(junk, is_game_master=False), MODE_PLAY)

    def test_the_roles_default_is_what_it_was_getting_before(self):
        self.assertEqual(default_mode_for(True), MODE_AIDE)
        self.assertEqual(default_mode_for(False), MODE_PLAY)


class AuthorityAndMannerAreOrthogonalTest(BaseTestCase):
    """The one test this issue must not be allowed to lose (#352 + #372).

    A mode is a request about manner. It travels from the page, from a form
    post, and on the body of a session's first turn — all places a player can
    write to. If choosing one could also hand over the Game Master's
    authority, #372 would have undone #352 and a player could set STR 22 by
    picking an option from a dropdown.
    """

    def test_a_player_choosing_either_mode_gains_no_authority(self):
        player = self.create_player()
        for mode in GM_MODES:
            with self.subTest(mode=mode):
                sent = get_system_message(player, mode)["content"]
                self.assertNotIn(SECTION_DM_AUTHORITY, sent)

    def test_a_game_master_keeps_authority_in_either_mode(self):
        dm = self.create_dm()
        for mode in GM_MODES:
            with self.subTest(mode=mode):
                self.assertIn(
                    SECTION_DM_AUTHORITY, get_system_message(dm, mode)["content"]
                )

    def test_the_mode_endpoint_grants_a_player_nothing(self):
        """Switching a session's mode changes the manner and only the manner."""
        player = self.create_player()
        self.login_as(player)
        session = GMSession.objects.create(
            name="Quest", user=player, mode=default_mode_for_user(player)
        )
        self.client.post(MODE_URL, {"mode": MODE_AIDE, "session_id": session.pk})
        session.refresh_from_db()

        self.assertEqual(session.mode, MODE_AIDE)
        sent = self._system_message_sent(player, session)
        self.assertEqual(sent, aide_system_prompt())
        self.assertNotIn(SECTION_DM_AUTHORITY, sent)

    def test_a_mode_posted_with_the_turn_cannot_grant_authority_either(self):
        """The first turn of a session carries its mode in the request body."""
        player = self.create_player()
        self.login_as(player)
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat",
            side_effect=gm_replies(["ok"]),
        ) as streamed:
            response = self.client.post(
                STREAM_URL,
                json.dumps(
                    {
                        "message": "I step into the tavern.",
                        "mode": MODE_AIDE,
                        # Neither of these is a field the view reads; they are
                        # here because a body is attacker-controlled and the
                        # test should say so.
                        "is_dm": True,
                        "user": "admin",
                    }
                ),
                content_type="application/json",
            )
            b"".join(response.streaming_content)
        sent = streamed.call_args.args[1][0]["content"]
        self.assertEqual(sent, aide_system_prompt())
        self.assertNotIn(SECTION_DM_AUTHORITY, sent)

    def test_a_game_master_in_play_mode_is_still_the_authority(self):
        """Spencer's own case: he plays, and his numbers are still his."""
        dm = self.create_dm()
        self.login_as(dm)
        session = GMSession.objects.create(name="Quest", user=dm, mode=MODE_PLAY)
        sent = self._system_message_sent(dm, session)
        self.assertEqual(sent, dm_play_system_prompt())
        self.assertIn(SECTION_DM_AUTHORITY, sent)
        self.assertNotIn(SECTION_DM_AIDE, sent)

    def _system_message_sent(self, user, session: GMSession) -> str:
        """The system message the stream endpoint actually sent to Ollama."""
        self.login_as(user)
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat",
            side_effect=gm_replies(["ok"]),
        ) as streamed:
            response = self.client.post(
                STREAM_URL,
                json.dumps({"session_id": session.pk, "message": "Where am I?"}),
                content_type="application/json",
            )
            b"".join(response.streaming_content)
        messages = streamed.call_args.args[1]
        self.assertEqual(messages[0]["role"], "system")
        return messages[0]["content"]


class SessionOpensInTheRolesModeTest(BaseTestCase):
    """The role sets the default; nothing changes underfoot for a DM."""

    def _first_turn_as(self, user) -> GMSession:
        self.login_as(user)
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat",
            side_effect=gm_replies(["ok"]),
        ):
            response = self.client.post(
                STREAM_URL,
                json.dumps({"message": "Hello."}),
                content_type="application/json",
            )
            b"".join(response.streaming_content)
        return GMSession.objects.get(user=user)

    def test_a_game_masters_new_session_opens_in_the_aide(self):
        self.assertEqual(self._first_turn_as(self.create_dm()).mode, MODE_AIDE)

    def test_an_admins_new_session_opens_in_the_aide_too(self):
        self.assertEqual(self._first_turn_as(self.create_admin()).mode, MODE_AIDE)

    def test_a_players_new_session_opens_playing(self):
        self.assertEqual(self._first_turn_as(self.create_player()).mode, MODE_PLAY)

    def test_a_session_created_by_attaching_a_character_opens_the_same_way(self):
        """The other path that makes a session must not disagree with the first."""
        dm = self.create_dm()
        self.login_as(dm)
        character = create_character(dm)
        self.client.post(
            "/tarmar/gm/characters/attach/",
            json.dumps({"character_id": character.pk}),
            content_type="application/json",
        )
        self.assertEqual(GMSession.objects.get(user=dm).mode, MODE_AIDE)

    def test_a_game_master_who_chose_play_keeps_it_on_the_next_turn(self):
        """The session's stored mode wins over the role's default from then on."""
        dm = self.create_dm()
        session = self._first_turn_as(dm)
        self.client.post(MODE_URL, {"mode": MODE_PLAY, "session_id": session.pk})
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat",
            side_effect=gm_replies(["ok"]),
        ) as streamed:
            response = self.client.post(
                STREAM_URL,
                json.dumps({"session_id": session.pk, "message": "I go north."}),
                content_type="application/json",
            )
            b"".join(response.streaming_content)
        self.assertEqual(
            streamed.call_args.args[1][0]["content"], dm_play_system_prompt()
        )


class SwitchingKeepsTheConversationTest(BaseTestCase):
    """What happens to the history when the mode changes mid-session."""

    def _session_with_history(self, mode: str = MODE_AIDE) -> GMSession:
        session = GMSession.objects.create(
            name="Quest", user=self.create_dm(), mode=mode
        )
        session.history = [
            {"role": "user", "content": "How much does a longsword cost?"},
            {"role": "assistant", "content": "40 silver."},
        ]
        session.save()
        return session

    def test_the_conversation_survives_the_switch(self):
        """It is the game. A person changing how they are answered has not
        asked to lose what has already happened."""
        session = self._session_with_history()
        session.switch_mode(MODE_PLAY)
        session.refresh_from_db()

        self.assertEqual(session.mode, MODE_PLAY)
        self.assertEqual(
            session.history[0]["content"], "How much does a longsword cost?"
        )
        self.assertEqual(session.history[1]["content"], "40 silver.")

    def test_a_hidden_note_is_filed_at_the_seam(self):
        """So the model reads the switch instead of imitating its own old voice."""
        session = self._session_with_history()
        session.switch_mode(MODE_PLAY)
        session.refresh_from_db()

        note = session.history[-1]
        self.assertEqual(note["kind"], MODE_KIND)
        self.assertEqual(note["role"], "user")
        self.assertTrue(is_hidden_message(note))
        self.assertIn("running the game yourself", note["content"])

    def test_the_note_is_appended_never_inserted(self):
        """Every byte the model has already seen stays where it was."""
        session = self._session_with_history()
        before = list(session.history)
        session.switch_mode(MODE_PLAY)
        session.refresh_from_db()
        self.assertEqual(session.history[: len(before)], before)
        self.assertEqual(len(session.history), len(before) + 1)

    def test_the_reader_is_shown_no_extra_turn(self):
        """The indicator on the page is what a person reads; this is for the model."""
        session = self._session_with_history()
        before = session.message_count
        session.switch_mode(MODE_PLAY)
        session.refresh_from_db()
        self.assertEqual(session.message_count, before)

    def test_a_turn_that_landed_since_the_page_rendered_is_not_lost(self):
        """The switch writes the history column, so it re-reads it first.

        The page's copy of a session can be minutes old. Building the new
        history from that snapshot would drop whatever the model streamed in
        between -- which, on a page whose whole point is a long conversation,
        is the conversation.
        """
        session = self._session_with_history()
        stale = GMSession.objects.get(pk=session.pk)  # the page's copy
        session.history = [
            *session.history,
            {"role": "user", "content": "And a shortsword?"},
            {"role": "assistant", "content": "25 silver."},
        ]
        session.save(update_fields=["history_json"])

        stale.switch_mode(MODE_PLAY)

        session.refresh_from_db()
        contents = [entry["content"] for entry in session.history]
        self.assertIn("And a shortsword?", contents)
        self.assertIn("25 silver.", contents)
        self.assertEqual(session.history[-1]["kind"], MODE_KIND)

    def test_switching_to_the_mode_already_in_force_writes_nothing(self):
        session = self._session_with_history(mode=MODE_AIDE)
        before = list(session.history)
        self.assertFalse(session.switch_mode(MODE_AIDE))
        session.refresh_from_db()
        self.assertEqual(session.history, before)

    def test_each_mode_has_wording_of_its_own(self):
        for mode in GM_MODES:
            with self.subTest(mode=mode):
                note = build_mode_switch_message(mode)
                self.assertEqual(note["kind"], MODE_KIND)
                self.assertTrue(note["content"])
        self.assertNotEqual(
            build_mode_switch_message(MODE_PLAY)["content"],
            build_mode_switch_message(MODE_AIDE)["content"],
        )

    def test_the_note_reaches_the_model_on_the_next_turn(self):
        session = self._session_with_history()
        session.switch_mode(MODE_PLAY)
        self.login_as(session.user)
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat",
            side_effect=gm_replies(["ok"]),
        ) as streamed:
            response = self.client.post(
                STREAM_URL,
                json.dumps({"session_id": session.pk, "message": "Begin."}),
                content_type="application/json",
            )
            b"".join(response.streaming_content)
        sent = [message["content"] for message in streamed.call_args.args[1]]
        self.assertEqual(sent[0], dm_play_system_prompt())
        self.assertTrue(
            any("running the game yourself" in content for content in sent),
            "the switch note was not sent to the model",
        )


class ModeEndpointTest(BaseTestCase):
    """The control posts a form and gets the page back, rendered from state."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.login_as(self.dm)

    def test_it_redirects_back_to_the_session_it_changed(self):
        session = GMSession.objects.create(name="Quest", user=self.dm, mode=MODE_AIDE)
        response = self.client.post(
            MODE_URL, {"mode": MODE_PLAY, "session_id": session.pk}
        )
        self.assertRedirects(response, f"{CHAT_URL}?session={session.pk}")
        session.refresh_from_db()
        self.assertEqual(session.mode, MODE_PLAY)

    def test_with_no_session_yet_the_choice_rides_in_the_query_string(self):
        """There is no row to write it on until the first message makes one."""
        response = self.client.post(MODE_URL, {"mode": MODE_PLAY})
        self.assertRedirects(response, f"{CHAT_URL}?mode={MODE_PLAY}")
        self.assertFalse(GMSession.objects.exists())

    def test_that_choice_is_what_the_first_turn_opens_the_session_in(self):
        self.client.post(MODE_URL, {"mode": MODE_PLAY})
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat",
            side_effect=gm_replies(["ok"]),
        ):
            response = self.client.post(
                STREAM_URL,
                json.dumps({"message": "Begin.", "mode": MODE_PLAY}),
                content_type="application/json",
            )
            b"".join(response.streaming_content)
        self.assertEqual(GMSession.objects.get(user=self.dm).mode, MODE_PLAY)

    def test_another_persons_session_cannot_be_switched(self):
        stranger = self.create_player(username="stranger")
        session = GMSession.objects.create(name="Theirs", user=stranger, mode=MODE_PLAY)
        response = self.client.post(
            MODE_URL, {"mode": MODE_AIDE, "session_id": session.pk}
        )
        self.assertRedirects(response, CHAT_URL)
        session.refresh_from_db()
        self.assertEqual(session.mode, MODE_PLAY)

    def test_an_unrecognised_mode_takes_the_default_rather_than_500ing(self):
        session = GMSession.objects.create(name="Quest", user=self.dm, mode=MODE_PLAY)
        self.client.post(MODE_URL, {"mode": "wizard", "session_id": session.pk})
        session.refresh_from_db()
        self.assertEqual(session.mode, MODE_AIDE)

    def test_it_refuses_a_get(self):
        self.assertEqual(self.client.get(MODE_URL).status_code, 405)


class ModeControlOnThePageTest(BaseTestCase):
    """The #353 indicator names the mode and changes it, without a menu."""

    def _page(self, user, query: str = "") -> str:
        self.login_as(user)
        response = self.client.get(f"{CHAT_URL}{query}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_indicator_carries_the_control(self):
        page = self._page(self.create_dm())
        indicator = page.split('id="gm-mode-indicator"')[1].split("</div>")[0]
        self.assertIn(f'action="{MODE_URL}"', indicator)
        self.assertIn('name="mode"', indicator)
        for mode in GM_MODES:
            with self.subTest(mode=mode):
                self.assertIn(f'value="{mode}"', indicator)
                self.assertIn(MODE_LABELS[mode], indicator)

    def test_the_selected_option_is_the_mode_the_model_is_being_run_in(self):
        dm = self.create_dm()
        session = GMSession.objects.create(name="Quest", user=dm, mode=MODE_PLAY)
        response = self._page_response(dm, f"?session={session.pk}")
        self.assertEqual(response.context["session_mode"], MODE_PLAY)
        self.assertEqual(response.context["session_mode_label"], MODE_LABELS[MODE_PLAY])
        self.assertEqual(
            response.context["session_mode_description"], MODE_DESCRIPTIONS[MODE_PLAY]
        )
        self.assertEqual(
            [option["selected"] for option in response.context["session_mode_options"]],
            [mode == MODE_PLAY for mode in MODE_LABELS],
        )

    def test_a_session_yet_to_exist_shows_the_mode_from_the_query_string(self):
        response = self._page_response(self.create_dm(), f"?mode={MODE_PLAY}")
        self.assertEqual(response.context["session_mode"], MODE_PLAY)

    def test_a_junk_mode_in_the_query_string_shows_the_default(self):
        response = self._page_response(self.create_dm(), "?mode=wizard")
        self.assertEqual(response.context["session_mode"], MODE_AIDE)

    def test_the_chrome_follows_the_mode_not_the_role(self):
        """A Game Master who has chosen to play is asked what he does."""
        dm = self.create_dm()
        session = GMSession.objects.create(name="Quest", user=dm, mode=MODE_PLAY)
        page = self._page(dm, f"?session={session.pk}")
        self.assertIn('placeholder="What do you do?"', page)
        self.assertNotIn('placeholder="Ask the aide"', page)

    def test_a_player_in_aide_mode_gets_the_aides_chrome(self):
        player = self.create_player()
        session = GMSession.objects.create(name="Quest", user=player, mode=MODE_AIDE)
        page = self._page(player, f"?session={session.pk}")
        self.assertIn('placeholder="Ask the aide"', page)

    def _page_response(self, user, query: str = ""):
        self.login_as(user)
        response = self.client.get(f"{CHAT_URL}{query}")
        self.assertEqual(response.status_code, 200)
        return response
