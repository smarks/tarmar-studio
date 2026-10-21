"""The Games Master app's refusals are configuration, not code (issue #97).

The app's views, hand edits, fights, Luck, withdrawals, a turn's plan policy
and its executor refuse in the words of ``config/gm-refusals.toml``
(``settings.GM_REFUSAL_WORDS``), read at startup. The shipped file is
Spencer's to edit, so these tests pin no sentence of it: they run under fixed
words in which each sentence is its own table and key
(``common.wording.fixed_wording``). Changing the words must not change who is
refused, so both sides of each rule are checked under the fixed words too, and
a refusal is known by its reason key, never by its words.
"""

import ast
from dataclasses import fields, replace
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from common.test_helpers import BaseTestCase, create_character
from common.tests.refusal_scan import fixed_words, literal_refusals

from .engine import MODE_AIDE, MODE_PLAY
from .hand_records import HandEditError, open_thread
from .models import GMEvent, GMSession, GMTurnRecord
from .refusal_words import GmRefusalWords, PolicyWords, load_refusal_words
from .test_turn_engine_executor import ExecutorTestCase, plan
from .testing import FIXED_GM_REFUSALS, with_fixed_gm_refusals
from .turn_engine import events
from .turn_engine import policy as policy_module
from .turn_engine.policy import (
    REASON_AIDE_UNASKED_ROLL,
    REASON_GAME_MASTER_ONLY,
    REFUSAL_REASON,
    SETTLED_REASONS,
    TurnPolicy,
)
from .turn_engine.state import settled_refusals

#: One sentence an author might give two of the policy's reasons.
SAME_WORDS = "that is not for this table"

#: The calls whose arguments a player reads, by module.
REFUSING_CALLS = {
    "gm/hand_records.py": {"HandEditError"},
    "gm/views.py": {
        "_json_error",
        "HttpResponseBadRequest",
        "PermissionDenied",
        "_rejected_turn",
        "finish",
    },
    "gm/views_records.py": {"_json_error"},
    "gm/views_history.py": {"HttpResponseBadRequest"},
    "gm/fight.py": {"FightError"},
    "gm/turn_engine/executor.py": {"_Refused", "_refused"},
    "gm/turn_engine/plan.py": {"replace"},
    "gm/withdraw.py": {"PermissionError", "HttpResponseBadRequest"},
    "gm/luck.py": {"CharacterChangeError", "_json_error"},
    "gm/attach.py": {"AttachRefusal"},
}


def same_words_for_two_reasons() -> GmRefusalWords:
    """The shipped words, with the aide's unasked roll and the Game Master's
    own actions given one sentence."""
    shipped = settings.GM_REFUSAL_WORDS
    return replace(
        shipped,
        policy=replace(
            shipped.policy, aide_unasked_roll=SAME_WORDS, game_master_only=SAME_WORDS
        ),
    )


class ShippedRefusalWordsTest(SimpleTestCase):
    """The shipped file loads, and the settings hold the file they name."""

    def test_the_shipped_file_loads_and_settings_hold_the_named_file(self):
        load_refusal_words(Path(settings.BASE_DIR) / "config" / "gm-refusals.toml")
        self.assertEqual(
            load_refusal_words(settings.GM_REFUSAL_WORDS_FILE),
            settings.GM_REFUSAL_WORDS,
        )


class NoRefusalWrittenInCodeTest(SimpleTestCase):
    """The app's refusing calls and the policy hold no sentence of their own."""

    def test_every_refusal_reads_the_configuration(self):
        for module, calls in REFUSING_CALLS.items():
            with self.subTest(module=module):
                self.assertEqual(
                    literal_refusals(Path(settings.BASE_DIR) / module, calls), []
                )

    def test_the_policy_says_its_refusals_only_in_the_configured_words(self):
        source = Path(policy_module.__file__)
        tree = ast.parse(source.read_text(encoding="utf-8"))
        speakers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name in {"refusal_for", "refusal_text"}
        ]
        self.assertEqual(len(speakers), 2)
        fixed = [
            node.lineno
            for speaker in speakers
            for node in ast.walk(speaker)
            if isinstance(node, ast.Return)
            and node.value is not None
            and fixed_words(node.value, set())
        ]
        self.assertEqual(fixed, [])

    def test_every_reason_is_a_key_of_the_policy_table(self):
        keys = {word_field.name for word_field in fields(PolicyWords)}
        reasons = {
            value
            for name, value in vars(policy_module).items()
            if name.startswith("REASON_")
        }
        # One key is a clause, not a reason: what the literacy ruling's
        # refusal adds when the canon leaves an origin to the Game Master
        # (issue #128).
        self.assertEqual(keys - reasons, {"literacy_ruling_origin"})
        self.assertLessEqual(reasons, keys)
        self.assertLess(SETTLED_REASONS, keys)
        self.assertNotIn(REASON_AIDE_UNASKED_ROLL, SETTLED_REASONS)


@with_fixed_gm_refusals
class RefusalsFollowTheConfigurationTest(BaseTestCase):
    """Hand edits and the plan policy refuse in the configured words, and the
    same accounts are refused as before."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="words-gm")
        self.player = self.create_player(username="words-player")

    def test_a_player_setting_records_by_hand_is_refused_in_the_configured_words(
        self,
    ):
        session = GMSession.objects.create(user=self.player, name="Mine")
        with self.assertRaisesMessage(HandEditError, "hand_records.game_master_only"):
            open_thread(session, self.player, "a debt")
        self.login_as(self.player)
        response = self.client.post(
            reverse("gm:threads", args=[session.pk]),
            data='{"change": "open", "text": "a debt"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "hand_records.game_master_only")
        # A Game Master is not refused, whatever the words say.
        own = GMSession.objects.create(user=self.game_master, name="Crypt")
        done = open_thread(own, self.game_master, "a debt")
        self.assertIn("a debt", done.message)

    def test_a_plan_policy_refuses_in_the_configured_words(self):
        player = TurnPolicy.for_turn(MODE_PLAY, is_game_master=False)
        master = TurnPolicy.for_turn(MODE_PLAY, is_game_master=True)
        self.assertEqual(
            player.refusal_for("revive_character"), "policy.game_master_only"
        )
        self.assertEqual(master.refusal_for("revive_character"), "")
        self.assertEqual(
            player.refusal_for("record_attributes", states_scores=True),
            "policy.player_stated_scores",
        )
        self.assertEqual(
            master.refusal_for("record_attributes", states_scores=True), ""
        )


class SettledByReasonNotWordsPlayerTest(ExecutorTestCase):
    """A player's Game-Master-only refusal settles by its reason key (#97)."""

    game_master = False
    mode = MODE_PLAY

    def setUp(self):
        super().setUp()
        self.wren = create_character(self.user, name="Wren", is_alive=False)
        self.session.characters.add(self.wren)

    def revive(self) -> str:
        return plan({"action": "revive_character", "character_id": self.wren.pk})

    def test_the_refusal_settles_whatever_words_it_was_given(self):
        with override_settings(GM_REFUSAL_WORDS=same_words_for_two_reasons()):
            outcome, *_rest = self.play(self.revive())
            self.assertEqual(outcome.results[0].error, SAME_WORDS)
            self.assertEqual(
                outcome.results[0].procedure[REFUSAL_REASON], REASON_GAME_MASTER_ONLY
            )
        # Its sentence edited since: the stored refusal is still settled.
        with override_settings(GM_REFUSAL_WORDS=FIXED_GM_REFUSALS):
            self.assertEqual(
                settled_refusals(self.session, is_game_master=False),
                {"revive_character": SAME_WORDS},
            )

    def test_a_refusal_stored_with_no_reason_key_is_not_settled(self):
        """A frame written before reason keys (#97) carries only the words;
        it is not matched by them, so it does not settle."""
        turn = GMTurnRecord.objects.create(
            user=self.user,
            session=self.session,
            outcome="done",
            mode=self.mode,
            is_game_master=False,
        )
        GMEvent.objects.create(
            turn=turn,
            seq=1,
            kind=events.ACTION,
            payload={
                "frame": {
                    "kind": "revive_character",
                    "ran": False,
                    "error": settings.GM_REFUSAL_WORDS.policy.game_master_only,
                }
            },
        )
        self.assertEqual(settled_refusals(self.session, is_game_master=False), {})


class SettledByReasonNotWordsAideTest(ExecutorTestCase):
    """An aide's unasked roll does not settle, though an author gave it the
    words of a refusal that does; the Game Master's next, asked-for roll runs."""

    game_master = True
    mode = MODE_AIDE

    def roll(self) -> str:
        return plan({"action": "roll", "dice": "3d6", "purpose": "a test"})

    @override_settings(GM_REFUSAL_WORDS=same_words_for_two_reasons())
    def test_two_reasons_with_one_sentence_settle_independently(self):
        self.policy = TurnPolicy.for_turn(
            MODE_AIDE, is_game_master=True, latest="How is Gorn after the fall?"
        )
        outcome, *_rest = self.play(self.roll())
        self.assertEqual(outcome.results[0].error, SAME_WORDS)
        self.assertEqual(settled_refusals(self.session, is_game_master=True), {})

        self.policy = TurnPolicy.for_turn(
            MODE_AIDE, is_game_master=True, latest="Roll 3d6 for Gorn"
        )
        outcome, *_rest = self.play(self.roll())
        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)
