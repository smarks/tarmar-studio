"""A slash command's prompt is built for its turn, never stored (issue #758).

``/rules`` and ``/state`` send the model a message the server writes: the
whole rules tree, or a request to summarise the game. Both used to be
appended to the session's history as the player's own message, so a reload
showed "I need a precise rules lookup. Here are the full rules..." (79,692
characters) as something the player said, and every later turn sent the rules
tree to the model again -- about 20,000 tokens a turn, enough to fold a
session after fourteen short turns. What is stored now is what the player
typed; the prompt exists only for the turn that asked for it.
"""

import importlib
import json
from unittest.mock import patch

from django.apps import apps as django_apps

from common.test_helpers import BaseTestCase
from gm.messages import (
    build_roll_message,
    build_rules_lookup_message,
    build_state_request_message,
    stored_roll_message,
)
from gm.models import GMSession, name_from_history
from gm.retrieval import HIDDEN_KINDS, estimate_message_tokens
from gm.testing import gm_replies

STREAM_URL = "/tarmar/gm/stream/"

#: A rules tree small enough to read in a failure, large enough to find.
RULES_TEXT = "--- characters/attributes.md ---\nRULES-TREE-MARKER " + "x" * 4000


def post_turn(client, body: dict) -> int | None:
    """Send one turn, drain its stream, and return the session it ran in."""
    response = client.post(
        STREAM_URL, json.dumps(body), content_type="application/json"
    )
    raw = b"".join(response.streaming_content).decode()
    frames = [
        json.loads(chunk[len("data: ") :]) for chunk in raw.split("\n\n") if chunk
    ]
    done = [frame for frame in frames if frame.get("done")]
    return done[-1]["session_id"] if done else None


def tokens(messages: list[dict[str, str]]) -> int:
    """What one call's messages cost, by the estimate the fold uses."""
    return sum(estimate_message_tokens(message) for message in messages)


@patch("gm.views.load_rules", return_value=RULES_TEXT)
@patch("gm.turn_engine.model.ollama_client.stream_chat")
class CommandPromptIsNotHistoryTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_rules_stores_the_command_the_player_typed(self, stream, _rules):
        stream.side_effect = replies = gm_replies("You may move ten hexes.")

        session_id = post_turn(
            self.client, {"command": "rules", "message": "How far can I move?"}
        )

        history = GMSession.objects.get(pk=session_id).history
        users = [entry for entry in history if entry.get("role") == "user"]
        self.assertEqual(
            [entry["content"] for entry in users], ["/rules How far can I move?"]
        )
        self.assertNotIn("RULES-TREE-MARKER", json.dumps(history))
        # The turn itself still had the rules to answer from.
        self.assertIn("RULES-TREE-MARKER", json.dumps(replies.calls[-1]))

    def test_state_stores_the_command_the_player_typed(self, stream, _rules):
        stream.side_effect = gm_replies("Wren is well.")

        session_id = post_turn(self.client, {"command": "state"})

        history = GMSession.objects.get(pk=session_id).history
        users = [entry["content"] for entry in history if entry.get("role") == "user"]
        self.assertEqual(users, ["/state"])
        self.assertNotIn("summarize the current game state", json.dumps(history))

    def test_the_turn_after_rules_does_not_carry_the_rules(self, stream, _rules):
        stream.side_effect = after_rules = gm_replies(
            "You may move ten hexes.", "The road north is empty."
        )
        session_id = post_turn(
            self.client, {"command": "rules", "message": "How far can I move?"}
        )
        post_turn(self.client, {"session_id": session_id, "message": "I walk north."})
        next_turn = after_rules.calls[-1]

        # The same conversation with the question asked in plain words.
        stream.side_effect = asked_plainly = gm_replies(
            "You may move ten hexes.", "The road north is empty."
        )
        plain_id = post_turn(self.client, {"message": "How far can I move?"})
        post_turn(self.client, {"session_id": plain_id, "message": "I walk north."})
        baseline = asked_plainly.calls[-1]

        self.assertNotIn("RULES-TREE-MARKER", json.dumps(next_turn))
        # The only difference the /rules turn leaves is the "/rules " the
        # player typed in front of the question.
        typed = estimate_message_tokens(
            {"content": "/rules How far can I move?"}
        ) - estimate_message_tokens({"content": "How far can I move?"})
        self.assertEqual(tokens(next_turn) - tokens(baseline), typed)

    def test_the_turn_after_state_does_not_carry_the_request(self, stream, _rules):
        stream.side_effect = replies = gm_replies("Wren is well.", "The road is empty.")
        session_id = post_turn(self.client, {"command": "state"})

        post_turn(self.client, {"session_id": session_id, "message": "I walk north."})

        self.assertNotIn(
            "summarize the current game state", json.dumps(replies.calls[-1])
        )

    def test_the_session_is_named_after_the_command_not_the_prompt(
        self, stream, _rules
    ):
        stream.side_effect = gm_replies("You may move ten hexes.")

        session_id = post_turn(
            self.client, {"command": "rules", "message": "How far can I move?"}
        )

        self.assertEqual(
            GMSession.objects.get(pk=session_id).name, "/rules How far can I move?"
        )


@patch("gm.views.roll_dice", return_value=([4, 5, 6], 15))
@patch("gm.turn_engine.model.ollama_client.stream_chat")
class RollIsStoredAsTypedTest(BaseTestCase):
    """A /roll reads back as the command, and the model reads what it did (#838)."""

    PROMPT = (
        "[The player rolled 3d6 and got: 4, 5, 6 = 15. "
        "Please adjudicate this roll in the current context.]"
    )

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_the_page_shows_the_command_and_its_chip_not_the_prompt(
        self, stream, _roll
    ):
        stream.side_effect = gm_replies("Fifteen: the lock gives.")

        session_id = post_turn(self.client, {"command": "roll", "message": "3d6"})
        page = self.client.get(f"/tarmar/gm/?session={session_id}")

        shown = page.context["history"]
        self.assertEqual(shown[0]["role"], "user")
        self.assertEqual(shown[0]["content"], "/roll 3d6")
        self.assertEqual(shown[1]["kind"], "dice")
        self.assertEqual(shown[1]["content"], "🎲 3d6: [4, 5, 6] = 15")
        self.assertNotContains(page, "Please adjudicate this roll")

    def test_the_session_is_named_after_the_command(self, stream, _roll):
        stream.side_effect = gm_replies("Fifteen: the lock gives.")

        session_id = post_turn(self.client, {"command": "roll", "message": "3d6"})

        self.assertEqual(GMSession.objects.get(pk=session_id).name, "/roll 3d6")

    def test_the_model_reads_the_roll_on_later_turns_as_before(self, stream, _roll):
        """The stored content is the prompt the model has always been sent."""
        stream.side_effect = replies = gm_replies(
            "Fifteen: the lock gives.", "The door swings open."
        )
        session_id = post_turn(self.client, {"command": "roll", "message": "3d6"})

        post_turn(self.client, {"session_id": session_id, "message": "I go in."})

        stored = GMSession.objects.get(pk=session_id).history[0]
        self.assertEqual(stored["content"], self.PROMPT)
        later = replies.calls[-1]
        self.assertIn({"role": "user", "content": self.PROMPT}, later)
        self.assertNotIn("/roll 3d6", json.dumps(later))

    def test_the_transcript_says_what_was_shown_and_what_was_read(self, stream, _roll):
        stream.side_effect = gm_replies("Fifteen: the lock gives.")
        session_id = post_turn(self.client, {"command": "roll", "message": "3d6"})

        text = self.client.get(f"/tarmar/gm/sessions/{session_id}/transcript/")

        body = b"".join(text.streaming_content if text.streaming else [text.content])
        self.assertIn(b"shown to the player as typed", body)
        self.assertIn(b'"typed": "/roll 3d6"', body)
        self.assertIn(self.PROMPT.encode(), body)


class StoredRollMigrationTest(BaseTestCase):
    """Rolls saved before #838 gain the line typed; the reverse takes it off."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.migration = importlib.import_module(
            "gm.migrations.0022_roll_entries_stored_as_typed"
        )

    def test_forward_and_back(self):
        prompt = build_roll_message("2d6+3", [3, 4], 10)
        history = [
            {"role": "user", "content": "I walk north."},
            prompt,
            {"role": "assistant", "content": "Ten."},
        ]
        opened_by_roll = GMSession.objects.create(
            user=self.user,
            name=name_from_history([prompt]),
            history_json=json.dumps([prompt]),
        )
        named = GMSession.objects.create(
            user=self.user, name="Tuesday", history_json=json.dumps(history)
        )

        self.migration.keep_rolls_as_typed(django_apps, None)

        opened_by_roll.refresh_from_db()
        named.refresh_from_db()
        self.assertEqual(opened_by_roll.name, "/roll 2d6+3")
        self.assertEqual(
            opened_by_roll.history[0],
            stored_roll_message("2d6+3", [3, 4], 10),
        )
        self.assertEqual(named.name, "Tuesday")
        self.assertEqual(named.history[1]["typed"], "/roll 2d6+3")
        self.assertEqual(named.history[0], history[0])

        self.migration.drop_typed_rolls(django_apps, None)

        opened_by_roll.refresh_from_db()
        named.refresh_from_db()
        self.assertEqual(opened_by_roll.history, [prompt])
        self.assertEqual(opened_by_roll.name, name_from_history([prompt]))
        self.assertEqual(named.history, history)
        self.assertEqual(named.name, "Tuesday")

    def test_a_player_line_that_looks_like_a_prompt_but_is_not_is_left_alone(self):
        said = {"role": "user", "content": "[The player rolled well.]"}
        hidden = {**build_roll_message("3d6", [1, 2, 3], 6), "kind": "canon"}
        self.assertEqual(self.migration.with_typed(said), said)
        self.assertEqual(self.migration.with_typed(hidden), hidden)

    def test_the_copied_prompt_matches_the_builder(self):
        for expression, faces, total in (("3d6", [4, 5, 6], 15), ("1d20-2", [7], 5)):
            with self.subTest(expression=expression):
                self.assertEqual(
                    self.migration.with_typed(
                        build_roll_message(expression, faces, total)
                    ),
                    stored_roll_message(expression, faces, total),
                )


class StoredCommandPromptMigrationTest(BaseTestCase):
    """Sessions saved before #758 are rewritten to the commands typed."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.migration = importlib.import_module(
            "gm.migrations.0021_command_entries_stored_as_typed"
        )

    def saved_before_758(self, *entries: dict, name: str | None = None) -> GMSession:
        history = list(entries)
        return GMSession.objects.create(
            user=self.user,
            name=name if name is not None else name_from_history(history),
            history_json=json.dumps(history),
        )

    def test_a_stored_rules_prompt_becomes_the_command(self):
        rules = build_rules_lookup_message("How far can I move?", RULES_TEXT)
        session = self.saved_before_758(
            rules,
            {"role": "assistant", "content": "Ten hexes."},
            build_state_request_message(),
            {"role": "assistant", "content": "Wren is well."},
        )

        self.migration.rewrite_command_prompts(django_apps, None)

        session.refresh_from_db()
        self.assertEqual(
            [entry["content"] for entry in session.history if entry["role"] == "user"],
            ["/rules How far can I move?", "/state"],
        )
        self.assertNotIn("RULES-TREE-MARKER", session.history_json)
        self.assertEqual(session.name, "/rules How far can I move?")

    def test_a_name_somebody_chose_is_kept(self):
        session = self.saved_before_758(
            build_state_request_message(), name="Tuesday's table"
        )

        self.migration.rewrite_command_prompts(django_apps, None)

        session.refresh_from_db()
        self.assertEqual(session.name, "Tuesday's table")
        self.assertEqual(session.history[0]["content"], "/state")

    def test_a_player_message_and_a_hidden_entry_are_left_alone(self):
        typed = {
            "role": "user",
            "content": "Please summarize the current game state: tell me",
        }
        hidden = {
            "role": "user",
            "kind": "canon",
            "content": "Please summarize the current game state:",
        }
        session = self.saved_before_758(
            {"role": "user", "content": "I walk north."}, hidden
        )
        before = session.history_json

        self.migration.rewrite_command_prompts(django_apps, None)

        session.refresh_from_db()
        self.assertEqual(session.history_json, before)
        self.assertEqual(self.migration.as_typed(hidden), hidden)
        self.assertEqual(self.migration.as_typed(typed), typed)
        self.assertEqual(
            self.migration.STATE_PROMPT, build_state_request_message()["content"]
        )

    def test_the_copied_naming_names_sessions_as_it_did_when_written(self):
        """Pinned to fixed cases: the copy must not follow a later app change."""
        cases = (
            (
                [{"role": "user", "content": "We enter the crypt."}],
                "We enter the crypt.",
            ),
            (
                [
                    {"role": "user", "kind": "recap", "content": "x"},
                    {"role": "user", "content": "Hi"},
                ],
                "Hi",
            ),
            (
                [{"role": "user", "content": "word " * 30}],
                "word word word word word word word word word…",
            ),
            ([{"role": "assistant", "content": "Hello"}], "New Session"),
            ([], "New Session"),
        )
        for history, name in cases:
            with self.subTest(history=history):
                self.assertEqual(self.migration.name_from_history(history), name)
        self.assertEqual(self.migration.HIDDEN_KINDS, HIDDEN_KINDS)
