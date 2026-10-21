"""Tests for a GM session's fight on the hex board (issue #345).

Covers ``/fight`` -- the foe text, the model's structured answer, the
bestiary match and stand-in, the battle it creates and how the party is
seated -- the Battle tab of the chat page, the hidden ``kind: "battle"``
entries that carry each turn's log to the model, the closing summary, and
the write-back of the fallen to their ``Character`` rows. Ollama is never
reached: the model's answer is a mocked ``chat``.
"""

import json
import time
from unittest.mock import MagicMock, patch

import httpx
from django.conf import settings
from django.test import SimpleTestCase

from battle import control
from battle.models import Battle
from battle.services import (
    MAX_COMBATANTS,
    advance_turn,
    record_player_action,
    restart_battle,
)
from bestiary.models import Beast
from characters.models import Character
from common.services.ollama_client import (
    STRUCTURED_OUTPUT_UNAVAILABLE_STATUS,
    StreamDeadlinePassed,
)
from common.test_helpers import BaseTestCase, create_character, logged_exception
from common.test_helpers import make_beast as shared_make_beast

from . import battle_report, fight
from .battle_report import (
    LOG_PREAMBLE,
    RESTART_TEXT,
    SUMMARY_PREAMBLE,
    battle_entry,
    closing_line,
    pending_battle_entries,
)
from .character_schema import SCHEMA_IN_PROMPT_PREAMBLE
from .engine import MODE_AIDE, MODE_PLAY
from .fight import (
    _COUNT_PREFIX,
    _COUNT_SUFFIX,
    _FOE_SEPARATOR,
    FOE_SCHEMA,
    FOE_TEAM,
    PARTY_TEAM,
    FightError,
    FoeRequest,
    find_catalog_beast,
    parse_foe_text,
    request_foes_from_model,
    resolve_foes,
    start_fight,
)
from .models import GMSession, GMTurnRecord
from .refusal_words import refusal_words
from .retrieval import BATTLE_KIND, is_battle_message, is_hidden_message
from .testing import game_master_sets_the_foes_asked_for, gm_replies
from .turn_record import running_turn_for

FIGHT_URL = "/tarmar/gm/fight/"
STREAM_URL = "/tarmar/gm/stream/"


# A weaker, slower beast than the shared default, so fights stay short.
FIGHT_BEAST_STATS = {
    "description": "A test beast.",
    "strength": 10,
    "constitution": 10,
    "dexterity": 12,
    "fatigue_pool": 28,
    "body_pool": 19,
    "movement": 8,
    "attacks": [{"name": "Bite", "damage": "1d6", "weapon_class": "Piercing"}],
}


def make_beast(slug: str, name: str, **overrides) -> Beast:
    """A beast beyond the catalog (which the bestiary migration loads in full:
    Gray Wolf, Dire Wolf and 237 more are already there for every test)."""
    return shared_make_beast(slug=slug, name=name, **{**FIGHT_BEAST_STATS, **overrides})


def play_turn(battle: Battle, user) -> Battle:
    """Advance one turn, handing every awaited player choice to the AI."""
    battle, events = advance_turn(battle.pk)
    while battle.status == Battle.STATUS_AWAITING:
        battle, events = record_player_action(
            battle.pk,
            user,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter="",
            as_ai=True,
        )
    return battle


class FoeTextTest(SimpleTestCase):
    """``/fight <foes>`` as the player types it."""

    def test_counts_lead_or_trail_and_entries_split_on_commas_and_and(self):
        requests = parse_foe_text("two gray wolves, a dire wolf and rat x3; Bear")
        self.assertEqual(
            [(request.name, request.count) for request in requests],
            [("gray wolves", 2), ("dire wolf", 1), ("rat", 3), ("Bear", 1)],
        )

    def test_digits_and_number_words_count_and_counts_are_clamped(self):
        requests = parse_foe_text("3 rats, ten wolves, 40 ants")
        self.assertEqual([request.count for request in requests], [3, 10, 10])

    def test_nothing_named_is_an_error(self):
        with self.assertRaises(FightError):
            parse_foe_text("  , ; . ")

    def test_and_after_a_comma_still_separates(self):
        # The separator no longer swallows the space before "and", so an
        # Oxford comma no longer leaves a foe named "and a bear".
        requests = parse_foe_text("two wolves, and a bear")
        self.assertEqual(
            [(request.name, request.count) for request in requests],
            [("wolves", 2), ("bear", 1)],
        )

    def test_and_inside_a_word_does_not_separate(self):
        requests = parse_foe_text("sand wraith and Andrew")
        self.assertEqual(
            [request.name for request in requests], ["sand wraith", "Andrew"]
        )

    def test_long_runs_of_spaces_parse_without_backtracking(self):
        # Each input is one of CodeQL's py/polynomial-redos witnesses for the
        # patterns this module had before: thousands of spaces that the old
        # separator, count prefix and "x N" suffix each split in every possible
        # way before failing. The linear patterns parse them in one pass.
        spaces = " " * 5000
        cases = {
            "separator": (f"wolf{spaces}bear", [(f"wolf{spaces}bear", 1)]),
            # The newline stops ".", so the count prefix must fail.
            "prefix": (f"a{spaces}b\nc", [(f"a{spaces}b\nc", 1)]),
            "suffix": (f"rat{spaces}b", [(f"rat{spaces}b", 1)]),
            "suffix with a count": (f"rat{spaces}x 2", [("rat", 2)]),
        }
        for label, (text, expected) in cases.items():
            with self.subTest(label):
                requests = parse_foe_text(text)
                self.assertEqual(
                    [(request.name, request.count) for request in requests],
                    expected,
                )

    def test_the_patterns_stay_fast_on_crafted_input(self):
        # The test above checks what the patterns match; this one checks how
        # long they take. Each input is ten times the one above and goes to
        # the patterns directly, so a pattern that backtracks again takes
        # seconds here rather than a millisecond (CodeQL py/polynomial-redos).
        spaces = " " * 50_000
        crafted = {
            "only spaces": spaces,
            "spaces after a word": f"a{spaces}",
            "spaces between words": f"rat{spaces}b",
            # The newline stops ".", so a pattern ending in ".+$" must fail.
            "spaces before a newline": f"a{spaces}b\nc",
        }
        patterns = {
            "separator": _FOE_SEPARATOR.split,
            "count prefix": _COUNT_PREFIX.match,
            "count suffix": _COUNT_SUFFIX.match,
        }
        for input_label, text in crafted.items():
            for pattern_label, apply_pattern in patterns.items():
                with self.subTest(input=input_label, pattern=pattern_label):
                    started = time.perf_counter()
                    apply_pattern(text)
                    self.assertLess(time.perf_counter() - started, 1.0)


class FoeResolutionTest(BaseTestCase):
    """Matching requests to the bestiary, and standing in for the unknown."""

    def setUp(self):
        super().setUp()
        self.gray_wolf = Beast.objects.get(slug="gray-wolf")
        self.dire_wolf = Beast.objects.get(slug="dire-wolf")

    def test_slug_then_exact_name_then_singular_then_shortest_containing(self):
        self.assertEqual(
            find_catalog_beast(FoeRequest("anything", 1, beast_slug="dire-wolf")),
            self.dire_wolf,
        )
        self.assertEqual(find_catalog_beast(FoeRequest("gray wolf", 1)), self.gray_wolf)
        self.assertEqual(
            find_catalog_beast(FoeRequest("Dire Wolves", 2)), self.dire_wolf
        )
        # "wolf" alone: the shortest catalog name containing it, names tied
        # on length falling to alphabetical order.
        self.assertEqual(find_catalog_beast(FoeRequest("wolves", 2)), self.dire_wolf)
        self.assertIsNone(find_catalog_beast(FoeRequest("shadow hound", 1)))

    def test_generated_variants_are_never_matched(self):
        variant = make_beast(
            "gray-wolf-variant-test", "Gray Wolf Variant Test", is_generated=True
        )
        self.assertIsNone(find_catalog_beast(FoeRequest("gray wolf variant test", 1)))
        self.assertTrue(variant.is_generated)

    def test_unknown_creature_gets_a_renamed_stand_in_from_the_band(self):
        (foe,) = resolve_foes(
            [FoeRequest("shadow hound", 2, category="mammals", difficulty="hard")]
        )
        self.assertEqual(foe.count, 2)
        assert foe.beast is not None  # a creature, not a person (#787)
        self.assertEqual(foe.beast.name, "shadow hound")
        self.assertTrue(foe.beast.is_generated)
        base = foe.beast.base_beast
        self.assertEqual(foe.generated_from, base)
        self.assertEqual(base.category, "mammals")
        self.assertTrue(25 <= base.strength <= 59, base.strength)

    def test_stand_in_falls_back_to_the_band_then_to_the_whole_catalog(self):
        calls = []
        real_pick_base = fight.pick_base

        def picky(**kwargs):
            calls.append(kwargs)
            if len(calls) < 3:
                raise Beast.DoesNotExist("nothing there")
            return real_pick_base(**kwargs)

        seeded = fight.stand_in_rng(7)
        with patch("gm.fight.pick_base", side_effect=picky):
            (foe,) = resolve_foes(
                [FoeRequest("bog wraith", 1, category="plants")], rng=seeded
            )
        self.assertEqual(
            [
                {"category": call["category"], "difficulty": call["difficulty"]}
                for call in calls
            ],
            [
                {"category": "plants", "difficulty": "moderate"},
                {"category": None, "difficulty": "moderate"},
                {"category": None, "difficulty": None},
            ],
        )
        # Every attempt draws from the fight's generator (issue #898).
        self.assertEqual([call["rng"] for call in calls], [seeded] * 3)
        assert foe.beast is not None  # a creature, not a person (#787)
        self.assertTrue(foe.beast.is_generated)
        self.assertEqual(foe.beast.name, "bog wraith")

    def test_an_empty_catalog_cannot_stand_in(self):
        Beast.objects.all().delete()
        with self.assertRaises(FightError):
            resolve_foes([FoeRequest("shadow hound", 1)])

    def test_the_same_seed_stands_in_the_same_beast_with_the_same_stats(self):
        # Issue #898: the stand-in was drawn from the unseeded module random,
        # so the same seed gave "skeleton warrior" a different beast each time.
        def stand_in(seed: int) -> Beast:
            (foe,) = resolve_foes(
                [FoeRequest("skeleton warrior", 1)], rng=fight.stand_in_rng(seed)
            )
            assert foe.beast is not None
            return foe.beast

        drawn = [stand_in(7) for _ in range(4)]
        self.assertEqual(
            {beast.base_beast_id for beast in drawn}, {drawn[0].base_beast_id}
        )
        stats = {
            (beast.strength, beast.dexterity, beast.constitution, beast.stops)
            for beast in drawn
        }
        self.assertEqual(len(stats), 1, stats)
        # Another seed may pick another beast: the seed is what decides.
        others = {stand_in(seed).base_beast_id for seed in range(20, 32)}
        self.assertGreater(len(others), 1)


class FightStartTest(BaseTestCase):
    """What ``start_fight`` builds and records."""

    def setUp(self):
        super().setUp()
        # Not about who chooses the foes (#841).
        self.enterContext(game_master_sets_the_foes_asked_for())
        self.user = self.create_user()
        self.session = GMSession.objects.create(name="The Wolf Road", user=self.user)
        self.aldwin = create_character(self.user, name="Aldwin")
        self.mira = create_character(self.user, name="Mira")
        self.session.characters.add(self.aldwin, self.mira)

    def test_party_and_foes_take_their_teams_and_the_party_is_seated(self):
        started = start_fight(self.session, foes_text="two gray wolves", seed=7)
        battle = started.battle
        # Named from its sides, not the session's first line (issue #831).
        self.assertEqual(battle.name, "Aldwin, Mira vs 2 × Gray Wolf")
        self.assertEqual(battle.status, Battle.STATUS_SETUP)
        self.assertEqual(battle.created_by, self.user)
        self.assertEqual(battle.control_mode, control.AUTOMATIC)
        party = list(battle.combatants.filter(character__isnull=False))
        foes = list(battle.combatants.filter(beast__isnull=False))
        self.assertEqual(sorted(row.display_name for row in party), ["Aldwin", "Mira"])
        self.assertEqual(
            [row.display_name for row in foes], ["Gray Wolf", "Gray Wolf (2)"]
        )
        for row in party:
            self.assertEqual(row.team, PARTY_TEAM)
            self.assertEqual(row.player, self.user)
            self.assertEqual(row.control_mode, control.MANUAL)
            self.assertTrue(row.is_saved_character)
        for row in foes:
            self.assertEqual(row.team, FOE_TEAM)
            self.assertIsNone(row.player)
            self.assertEqual(row.control_mode, control.INHERIT)
        self.session.refresh_from_db()
        self.assertEqual(self.session.battle, battle)
        self.assertEqual(started.dropped, 0)

    def test_the_session_learns_of_the_fight_hidden_and_in_the_open(self):
        start_fight(self.session, foes_text="a gray wolf")
        self.session.refresh_from_db()
        hidden, visible = self.session.history
        self.assertEqual(hidden["kind"], BATTLE_KIND)
        self.assertEqual(hidden["role"], "user")
        self.assertEqual(hidden["battle_id"], self.session.battle.pk)
        self.assertEqual(hidden["battle_sequence"], 0)
        self.assertIn(
            "Party (the player decides their moves): Aldwin, Mira", hidden["content"]
        )
        self.assertIn("Foes (the AI plays them): Gray Wolf", hidden["content"])
        self.assertEqual(visible["role"], "assistant")
        self.assertIn("Aldwin, Mira against Gray Wolf", visible["content"])
        self.assertEqual(self.session.message_count, 1)

    def test_a_seeded_fight_builds_the_same_stand_in_every_time(self):
        # Issue #898: the battle was seeded and its stand-in foe was not.
        bases = set()
        for _attempt in range(4):
            started = start_fight(self.session, foes_text="skeleton warrior", seed=7)
            (foe,) = started.foes
            assert foe.generated_from is not None
            bases.add(foe.generated_from.pk)
            self.session.battle = None
            self.session.save(update_fields=["battle"])
        self.assertEqual(len(bases), 1, bases)

    def test_an_unseeded_fight_records_the_seed_its_stand_ins_came_from(self):
        started = start_fight(self.session, foes_text="skeleton warrior")
        (foe,) = started.foes
        assert foe.generated_from is not None
        (again,) = resolve_foes(
            [FoeRequest("skeleton warrior", 1)],
            rng=fight.stand_in_rng(started.battle.seed),
        )
        self.assertEqual(again.generated_from, foe.generated_from)

    def test_stand_ins_are_named_in_the_hidden_block(self):
        started = start_fight(self.session, foes_text="shadow hound")
        self.session.refresh_from_db()
        (foe,) = started.foes
        assert foe.generated_from is not None
        self.assertIn(
            f"shadow hound (a stand-in varied from {foe.generated_from.name})",
            self.session.history[0]["content"],
        )

    def test_no_living_characters_no_fight(self):
        self.session.characters.clear()
        with self.assertRaisesMessage(
            FightError, refusal_words().fight.attach_living_character
        ):
            start_fight(self.session, foes_text="gray wolf")
        self.session.characters.add(self.aldwin)
        self.aldwin.is_alive = False
        self.aldwin.save()
        with self.assertRaisesMessage(
            FightError, refusal_words().fight.attach_living_character
        ):
            start_fight(self.session, foes_text="gray wolf")

    def test_a_fight_under_way_refuses_a_second_and_a_finished_one_allows_it(self):
        first = start_fight(self.session, foes_text="gray wolf").battle
        with self.assertRaisesMessage(
            FightError, refusal_words().fight.fight_under_way
        ):
            start_fight(self.session, foes_text="gray wolf")
        Battle.objects.filter(pk=first.pk).update(status=Battle.STATUS_FINISHED)
        self.session.refresh_from_db()
        second = start_fight(self.session, foes_text="gray wolf").battle
        self.assertNotEqual(first.pk, second.pk)
        self.session.refresh_from_db()
        kinds = [
            (message.get("battle_id"), bool(message.get("battle_ended")))
            for message in self.session.history
            if is_battle_message(message)
        ]
        # The first fight's summary is filed before the second fight opens.
        self.assertEqual(
            kinds, [(first.pk, False), (first.pk, True), (second.pk, False)]
        )

    def test_foes_past_the_field_limit_are_dropped_and_counted(self):
        started = start_fight(self.session, foes_text="ten gray wolves, 10 gray wolves")
        room = MAX_COMBATANTS - 2
        self.assertEqual(started.battle.combatants.count(), MAX_COMBATANTS)
        self.assertEqual(started.dropped, 20 - room)

    def test_a_full_party_leaves_no_room(self):
        for number in range(MAX_COMBATANTS):
            self.session.characters.add(
                create_character(self.user, name=f"Extra {number}")
            )
        with self.assertRaisesMessage(
            FightError, refusal_words().fight.field_full.format(most=MAX_COMBATANTS)
        ):
            start_fight(self.session, foes_text="gray wolf")

    def test_long_names_fit_the_battle_name_column(self):
        self.session.name = "x" * 150
        self.session.save()
        self.aldwin.name = "Aldwin the " + "Very " * 30 + "Tall"
        self.aldwin.save()
        battle = start_fight(self.session, foes_text="gray wolf").battle
        self.assertLessEqual(len(battle.name), 100)
        self.assertTrue(battle.name.startswith("Aldwin the Very"))
        self.assertTrue(battle.name.endswith("…"))
        self.assertNotIn("x" * 10, battle.name)


class FoesFromTheModelTest(BaseTestCase):
    """The structured-output question, and what its answer becomes."""

    def setUp(self):
        super().setUp()
        self.history = [
            {"role": "system", "content": "GM"},
            {"role": "user", "content": "Two wolves circle us"},
        ]

    @patch("gm.services.ollama_client.chat")
    def test_the_question_carries_the_catalog_and_the_schema(self, mock_stream):
        mock_stream.return_value = '{"foes": [{"name": "Gray Wolf", "beast_slug": "gray-wolf", "category": "", "difficulty": "", "count": 2}]}'
        requests = request_foes_from_model(self.history, max_foes=5)
        self.assertEqual(requests, [FoeRequest("Gray Wolf", 2, "gray-wolf", "", "")])
        args, kwargs = mock_stream.call_args
        self.assertEqual(kwargs["response_format"], FOE_SCHEMA)
        self.assertIs(kwargs["think"], False)
        messages = args[1]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "user"])
        self.assertEqual(set(messages[-1]), {"role", "content"})
        self.assertIn("Up to 5 figures", messages[-1]["content"])
        self.assertIn("[Mammals]", messages[-1]["content"])
        self.assertIn("gray-wolf: Gray Wolf", messages[-1]["content"])
        self.assertIn("dire-wolf: Dire Wolf", messages[-1]["content"])

    @patch("gm.services.ollama_client.chat")
    def test_thinking_is_stripped_and_counts_are_clamped(self, mock_stream):
        mock_stream.return_value = '<think>hmm</think>{"foes": [{"name": "rat", "beast_slug": "", "category": "insects_vermin", "difficulty": "easy", "count": 99}]}'
        (request,) = request_foes_from_model(self.history, max_foes=5)
        self.assertEqual(request.count, fight.MAX_FOES_PER_KIND)
        self.assertEqual(request.category, "insects_vermin")

    @patch("gm.services.ollama_client.chat")
    def test_an_answer_that_is_not_a_foe_list_is_an_error(self, mock_stream):
        for answer in ("not json", '{"foes": "wolves"}', '{"foes": [{"count": 1}]}'):
            mock_stream.return_value = answer
            with self.assertRaises(FightError):
                request_foes_from_model(self.history, max_foes=5)

    @patch("gm.services.ollama_client.chat")
    def test_an_empty_list_is_an_error(self, mock_stream):
        mock_stream.return_value = '{"foes": []}'
        with self.assertRaisesMessage(FightError, refusal_words().fight.found_nobody):
            request_foes_from_model(self.history, max_foes=5)

    @patch("gm.services.ollama_client.chat")
    def test_a_runner_without_structured_output_gets_the_schema_in_the_prompt(
        self, mock_chat
    ):
        """The MLX runner's 501 -> one retry without ``format`` (issue #368).

        This is the bug the production model shipped into: ``qwen3.6:27b-mlx``
        on Ollama 0.33.3 answers every ``format`` with 501, and a blank foes
        box used to reach the player as a 500.
        """
        response = MagicMock(status_code=STRUCTURED_OUTPUT_UNAVAILABLE_STATUS)
        refused = httpx.HTTPStatusError("501", request=MagicMock(), response=response)
        mock_chat.side_effect = [
            refused,
            '```json\n{"foes": [{"name": "Gray Wolf", "beast_slug": "gray-wolf", '
            '"category": "", "difficulty": "", "count": 2}]}\n```',
        ]

        requests = request_foes_from_model(self.history, max_foes=5)

        self.assertEqual(requests, [FoeRequest("Gray Wolf", 2, "gray-wolf", "", "")])
        self.assertEqual(mock_chat.call_count, 2)
        first, second = mock_chat.call_args_list
        self.assertEqual(first.kwargs["response_format"], FOE_SCHEMA)
        self.assertNotIn("response_format", second.kwargs)
        self.assertIs(second.kwargs["think"], False)
        self.assertEqual(second.kwargs["options"], first.kwargs["options"])
        # The retry carries the schema itself, so the unconstrained model
        # still knows the shape to answer in.
        retry_ask = second.args[1][-1]["content"]
        self.assertIn(SCHEMA_IN_PROMPT_PREAMBLE, retry_ask)
        self.assertIn('"beast_slug"', retry_ask)
        self.assertIn("gray-wolf: Gray Wolf", retry_ask)

    @patch("gm.services.ollama_client.chat")
    def test_other_http_errors_propagate(self, mock_chat):
        """A 500 is Ollama being broken, not a runner refusing schemas."""
        mock_chat.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
        with self.assertRaises(httpx.HTTPStatusError):
            request_foes_from_model(self.history, max_foes=5)
        self.assertEqual(mock_chat.call_count, 1)

    @patch("gm.services.ollama_client.chat")
    def test_an_unparseable_fallback_answer_is_still_the_players_message(
        self, mock_chat
    ):
        """Falling back does not turn a refusal into a 500 either."""
        response = MagicMock(status_code=STRUCTURED_OUTPUT_UNAVAILABLE_STATUS)
        refused = httpx.HTTPStatusError("501", request=MagicMock(), response=response)
        mock_chat.side_effect = [refused, "I would rather not say who you face."]
        with self.assertRaisesMessage(
            FightError, refusal_words().fight.could_not_say_who
        ):
            request_foes_from_model(self.history, max_foes=5)


class FightEndpointTest(BaseTestCase):
    """``POST /tarmar/gm/fight/``, as a Game Master.

    A Game Master's typed foes are built as typed and his blank ``/fight``
    asks the model who the party faces. These tests ran as a player until
    #841, and the first pinned the behaviour it removed: a player naming the
    foes, built without the model. A player's ``/fight`` is
    :class:`PlayersFoesAreTheGameMastersTest`'s.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_dm()
        self.login_as(self.user)
        self.session = GMSession.objects.create(name="Quest", user=self.user)
        self.session.characters.add(create_character(self.user, name="Aldwin"))

    def _post(self, body: dict):
        return self.client.post(
            FIGHT_URL, json.dumps(body), content_type="application/json"
        )

    def test_named_foes_start_a_fight_without_the_model(self):
        with patch("gm.services.ollama_client.chat") as mock_stream:
            response = self._post(
                {"session_id": self.session.pk, "foes": "2 gray wolves"}
            )
        mock_stream.assert_not_called()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.session.refresh_from_db()
        self.assertEqual(
            data,
            {
                "ok": True,
                "session_id": self.session.pk,
                "battle_id": self.session.battle.pk,
                "battle_name": "Aldwin vs 2 × Gray Wolf",
                "party": ["Aldwin"],
                "foes": [{"name": "Gray Wolf", "count": 2}],
                "dropped": 0,
            },
        )

    @patch("gm.services.ollama_client.chat")
    def test_no_foes_named_asks_the_model(self, mock_stream):
        mock_stream.return_value = '{"foes": [{"name": "Gray Wolf", "beast_slug": "gray-wolf", "category": "", "difficulty": "", "count": 1}]}'
        response = self._post({"session_id": self.session.pk, "foes": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["foes"], [{"name": "Gray Wolf", "count": 1}])
        self.assertEqual(mock_stream.call_args.kwargs["response_format"], FOE_SCHEMA)

    @patch("gm.services.ollama_client.chat")
    def test_a_bad_model_answer_is_a_400_in_the_players_words(self, mock_stream):
        mock_stream.return_value = "nope"
        response = self._post({"session_id": self.session.pk})
        self.assertEqual(response.status_code, 400)
        self.assertIn(refusal_words().fight.could_not_say_who, response.json()["error"])
        self.assertIsNone(Battle.objects.first())

    @patch("gm.services.ollama_client.chat")
    def test_a_runner_that_refuses_schemas_still_starts_the_fight(self, mock_chat):
        """Issue #368, end to end: the 501 used to reach the player as a 500."""
        response = MagicMock(status_code=STRUCTURED_OUTPUT_UNAVAILABLE_STATUS)
        mock_chat.side_effect = [
            httpx.HTTPStatusError("501", request=MagicMock(), response=response),
            '{"foes": [{"name": "Gray Wolf", "beast_slug": "gray-wolf", '
            '"category": "", "difficulty": "", "count": 2}]}',
        ]
        result = self._post({"session_id": self.session.pk, "foes": ""})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["foes"], [{"name": "Gray Wolf", "count": 2}])

    @patch("gm.services.ollama_client.chat")
    def test_a_refusal_after_the_fallback_is_still_a_400(self, mock_chat):
        """The fallback does not turn an unreadable answer into a 500 either."""
        response = MagicMock(status_code=STRUCTURED_OUTPUT_UNAVAILABLE_STATUS)
        mock_chat.side_effect = [
            httpx.HTTPStatusError("501", request=MagicMock(), response=response),
            "I would rather not say who you face.",
        ]
        result = self._post({"session_id": self.session.pk, "foes": ""})
        self.assertEqual(result.status_code, 400)
        self.assertIn(refusal_words().fight.could_not_say_who, result.json()["error"])
        self.assertIsNone(Battle.objects.first())

    def test_refusals(self):
        self.assertEqual(self._post({}).status_code, 400)
        self.assertEqual(self._post({"session_id": "abc"}).status_code, 400)
        self.assertEqual(self._post({"session_id": 99999}).status_code, 404)
        self.assertEqual(
            self._post({"session_id": self.session.pk, "foes": ["wolf"]}).status_code,
            400,
        )
        self.assertEqual(
            self._post({"session_id": self.session.pk, "foes": "x" * 4001}).status_code,
            400,
        )
        other = self.create_user(username="other")
        theirs = GMSession.objects.create(name="Theirs", user=other)
        self.assertEqual(
            self._post({"session_id": theirs.pk, "foes": "wolf"}).status_code, 404
        )
        self.session.characters.clear()
        response = self._post({"session_id": self.session.pk, "foes": "wolf"})
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            refusal_words().fight.attach_living_character, response.json()["error"]
        )

    def test_requires_login(self):
        self.logout()
        response = self._post({"session_id": self.session.pk, "foes": "wolf"})
        self.assertIn(response.status_code, (302, 403))


class ChatPageBattleTabTest(BaseTestCase):
    """The Battle tab renders the session's board, or the way to start one."""

    def setUp(self):
        super().setUp()
        # Not about who chooses the foes (#841).
        self.enterContext(game_master_sets_the_foes_asked_for())
        self.user = self.create_user()
        self.login_as(self.user)
        self.session = GMSession.objects.create(name="Quest", user=self.user)
        self.session.characters.add(create_character(self.user, name="Aldwin"))

    def test_without_a_battle_the_tab_offers_the_fight_form(self):
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertContains(response, 'id="gm-panel-tab-battle"')
        self.assertContains(response, 'id="gm-battle-empty"')
        self.assertContains(response, 'data-fight-url="/tarmar/gm/fight/"')
        self.assertNotContains(response, "is-live")
        self.assertNotContains(response, 'id="battle-arena"')
        self.assertIsNone(response.context["panel_battle"])
        self.assertFalse(response.context["battle_live"])
        self.assertNotIn("view_json", response.context)

    def test_a_blank_chat_page_has_the_tab_too(self):
        response = self.client.get("/tarmar/gm/")
        self.assertContains(response, 'id="gm-panel-battle"')
        self.assertContains(response, 'id="gm-fight-button"')

    def test_with_a_battle_the_tab_hosts_the_arena(self):
        battle = start_fight(self.session, foes_text="gray wolf").battle
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertEqual(response.context["panel_battle"], battle)
        self.assertTrue(response.context["battle_live"])
        self.assertTrue(response.context["can_control"])
        self.assertEqual(response.context["battle"], battle)
        self.assertContains(response, "gm-panel-tab-button is-live")
        self.assertContains(response, f'data-battle-id="{battle.pk}"')
        self.assertContains(response, f'href="/tarmar/battles/{battle.pk}/"')
        self.assertContains(response, 'id="battle-arena"')
        self.assertContains(response, 'id="battle-view"')
        self.assertContains(response, 'id="battle-roster"')
        self.assertContains(response, 'id="battle-log"')
        self.assertContains(response, 'id="advance-turn"')
        self.assertContains(response, 'id="turn-panel"')
        self.assertContains(response, 'data-is-dm="true"')
        # The fight's foes are the Game Master's, and so is its restart
        # (#762): the player who started it advances it and plays Aldwin.
        self.assertNotContains(response, 'id="restart-battle"')
        self.assertContains(response, "js/battle.js")
        self.assertContains(response, "js/gm-panel-battle.js")
        # The turn panel's words reach the tab as they reach the battle page
        # (#94), in the arena partial's own block.
        self.assertContains(response, 'id="battle-turn-panel-words"')
        self.assertContains(response, "css/battle.css")
        # The fight form waits while this one is under way.
        self.assertContains(response, 'id="gm-fight-start" disabled')
        view = json.loads(
            response.context["view_json"] and json.dumps(response.context["view_json"])
        )
        self.assertEqual(
            sorted(combatant["name"] for combatant in view["combatants"]),
            ["Aldwin", "Gray Wolf"],
        )
        seats = {seat["display_name"]: seat for seat in response.context["seats"]}
        self.assertTrue(seats["Aldwin"]["is_you"])
        self.assertEqual(seats["Aldwin"]["effective_control_mode"], control.MANUAL)
        self.assertTrue(seats["Aldwin"]["you_decide"])
        self.assertEqual(
            seats["Gray Wolf"]["effective_control_mode"], control.AUTOMATIC
        )

    def test_a_finished_battle_stays_on_the_board_but_the_tab_goes_quiet(self):
        battle = start_fight(self.session, foes_text="gray wolf").battle
        Battle.objects.filter(pk=battle.pk).update(status=Battle.STATUS_FINISHED)
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertFalse(response.context["battle_live"])
        self.assertNotContains(response, "is-live")
        self.assertContains(response, 'id="battle-arena"')
        self.assertNotContains(response, 'id="gm-fight-start" disabled')

    def test_battle_entries_are_hidden_from_the_transcript(self):
        battle = start_fight(self.session, foes_text="gray wolf").battle
        self.session.refresh_from_db()
        self.session.history = self.session.history + [
            battle_entry("SECRET BATTLE LOG", battle=battle, sequence=3, turn=1),
            {"role": "user", "content": "Hello"},
        ]
        self.session.save()
        response = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        self.assertNotContains(response, "SECRET BATTLE LOG")
        self.assertEqual(
            [message["content"] for message in response.context["history"]][-1],
            "Hello",
        )
        self.assertEqual(response.context["sessions"][0].message_count, 2)

    def test_the_battle_page_itself_still_renders_the_arena(self):
        battle = start_fight(self.session, foes_text="gray wolf").battle
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="battle-arena"')
        self.assertContains(response, 'id="advance-turn"')
        # The control desk commands the foes, which are the Game Master's in
        # a session's fight (#762). This used to pass with the player given
        # the whole desk: the battle's maker was all it checked.
        self.assertNotContains(response, 'id="battle-control"')
        self.assertNotContains(response, 'id="restart-battle"')
        dm = self.create_dm()
        self.login_as(dm)
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        self.assertContains(response, 'id="battle-control"')
        self.assertContains(response, 'id="restart-battle"')


class BattleReportTest(BaseTestCase):
    """The board's log reaches the model turn by turn, then the summary."""

    def setUp(self):
        super().setUp()
        # Not about who chooses the foes (#841).
        self.enterContext(game_master_sets_the_foes_asked_for())
        self.user = self.create_user()
        self.login_as(self.user)
        self.aldwin = create_character(self.user, name="Aldwin")
        self.session = GMSession.objects.create(name="Quest", user=self.user)
        self.session.characters.add(self.aldwin)
        self.battle = start_fight(self.session, foes_text="gray wolf", seed=11).battle
        self.session.refresh_from_db()

    def _stream(self, message: str = "What now?") -> list[dict[str, str]]:
        """Play one chat turn through the stream view; return the wire messages."""
        stream = gm_replies("reply")
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat", side_effect=stream
        ):
            response = self.client.post(
                STREAM_URL,
                json.dumps({"session_id": self.session.pk, "message": message}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            b"".join(response.streaming_content)
        # The narrate call: its history, then the message it answers.
        sent = [dict(message) for message in stream.calls[-1]]
        self.session.refresh_from_db()
        return sent

    def _battle_entries(self) -> list[dict]:
        return [m for m in self.session.history if is_battle_message(m)]

    def test_a_played_turn_is_filed_ahead_of_the_players_message(self):
        battle = play_turn(self.battle, self.user)
        self.assertGreaterEqual(battle.turn_number, 1)
        sent = self._stream("I swing at the wolf")
        roles = [m["role"] for m in sent]
        self.assertEqual(roles[0], "system")
        contents = [m["content"] for m in sent]
        log_blocks = [
            c
            for c in contents
            if c.startswith(f"{LOG_PREAMBLE} — {self.battle.name}, turn 1:")
        ]
        self.assertEqual(len(log_blocks), 1)
        self.assertIn("Latest message: I swing at the wolf", contents[-1])
        # The log block sits after the opening block, in the history.
        opening = next(
            i
            for i, c in enumerate(contents)
            if c.startswith("Battle — the fight is now")
        )
        log_index = contents.index(log_blocks[0])
        self.assertLess(opening, log_index)
        self.assertLess(log_index, len(contents) - 1)
        self.assertEqual({key for m in sent for key in m}, {"role", "content"})
        # Every line of the turn is there, verbatim.
        for event in battle.events.filter(turn=1):
            self.assertIn(f"- {event.message}", log_blocks[0])
        entries = self._battle_entries()
        last = entries[-1]
        self.assertEqual(last["battle_turn"], battle.turn_number)
        self.assertEqual(
            last["battle_sequence"],
            battle.events.order_by("-sequence").first().sequence,
        )
        self.assertTrue(last["battle_mark"])
        self.assertTrue(is_hidden_message(last))
        self.assertEqual(self.session.message_count, 3)

    def test_nothing_new_files_nothing_and_the_prefix_holds(self):
        play_turn(self.battle, self.user)
        first = self._stream("one")
        second = self._stream("two")
        self.assertEqual(second[: len(first) - 1], first[:-1])
        self.assertEqual(
            sum(1 for m in second if m["content"].startswith(LOG_PREAMBLE)), 1
        )

    def test_several_turns_file_one_entry_each(self):
        battle = play_turn(self.battle, self.user)
        battle = play_turn(battle, self.user)
        self._stream()
        turns = [m["battle_turn"] for m in self._battle_entries()]
        self.assertEqual(turns, [0, 1, 2][: len(turns)])
        self.assertEqual(len(turns), 1 + battle.turn_number)

    def _finish_with_aldwin_dead(self):
        """Let the board kill Aldwin: a survival save no roll can make.

        Aldwin is put far past minus his full Body, where the save is 3d6
        against CON less the distance, and the engine's own phase 6 fails it.
        The death is the engine's, recorded the way any death on the board
        is. (This used to write ``alive: False`` into the state and mark the
        battle finished by hand, which no engine ever did, so the tests below
        could only ever exercise a write-back that waited for a chat turn.)
        """
        state = Battle.objects.get(pk=self.battle.pk).state_json
        for combatant in state["combatants"]:
            if combatant["name"] == "Aldwin":
                combatant["fatigue"] = -3
                combatant["body"] = -100
                combatant["conscious"] = False
        Battle.objects.filter(pk=self.battle.pk).update(state_json=state)
        # The board reached those pools on turns that would have written them
        # to the record (#750); the record is put in step, without history,
        # so the fight is not refused as fighting on stale pools (#842).
        fatigue, body = self.aldwin.pools()
        Character.objects.filter(pk=self.aldwin.pk).update(
            fatigue_damage=fatigue + 3, body_damage=body + 100
        )
        battle = play_turn(self.battle, self.user)
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        return battle

    def test_the_board_records_a_death_before_any_chat_message(self):
        # Issue #750: the record changes when the engine records the death,
        # not on the player's next message.
        battle = self._finish_with_aldwin_dead()
        self.aldwin.refresh_from_db()
        self.assertFalse(self.aldwin.is_alive)
        self.assertEqual(self.aldwin.death_cause, f"Killed in battle — {battle.name}")
        # Through characters.services: the history says so, naming the Game
        # Master whose side dealt it -- never the player who started the
        # fight, who did not mark their own death.
        death = [entry for entry in self.aldwin.history if entry["field"] == "is_alive"]
        self.assertEqual(len(death), 1)
        self.assertEqual((death[0]["old"], death[0]["new"]), ("True", "False"))
        self.assertEqual(
            {entry["by"] for entry in self.aldwin.history}, {"Games Master"}
        )
        self.assertFalse(self.aldwin.is_played)

    def test_a_restart_or_a_detach_after_the_death_does_not_undo_it(self):
        # Issue #750's two windows: both used to leave Aldwin alive.
        self._finish_with_aldwin_dead()
        response = self.client.post(f"/tarmar/battles/{self.battle.pk}/restart/")
        self.assertEqual(response.status_code, 403)
        detach = self.client.post(
            "/tarmar/gm/characters/detach/",
            json.dumps({"session_id": self.session.pk, "character_id": self.aldwin.pk}),
            content_type="application/json",
        )
        self.assertEqual(detach.status_code, 200)
        self.session.refresh_from_db()
        pending_battle_entries(self.session.history, self.session)
        self.aldwin.refresh_from_db()
        self.assertFalse(self.aldwin.is_alive)
        # A Game Master's restart replays the fight; the death stays recorded.
        dm = self.create_dm()
        self.login_as(dm)
        response = self.client.post(f"/tarmar/battles/{self.battle.pk}/restart/")
        self.assertEqual(response.status_code, 200, response.content)
        self.aldwin.refresh_from_db()
        self.assertFalse(self.aldwin.is_alive)

    def test_the_end_files_a_summary_and_marks_the_fallen(self):
        battle = self._finish_with_aldwin_dead()
        sent = self._stream("Is it over?")
        summaries = [
            m["content"] for m in sent if m["content"].startswith(SUMMARY_PREAMBLE)
        ]
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertIn("Party:", summary)
        self.assertIn("Foes:", summary)
        self.assertIn("Aldwin: dead;", summary)
        self.assertIn("Gray Wolf: standing;", summary)
        self.assertIn("Fatigue -3/", summary)
        self.assertIn("Body -100/", summary)
        self.assertIn("(wounds:", summary)
        self.aldwin.refresh_from_db()
        self.assertFalse(self.aldwin.is_alive)
        self.assertEqual(self.aldwin.death_cause, f"Killed in battle — {battle.name}")
        # The state block of the same turn already says so.
        self.assertIn("Aldwin — ", sent[-1]["content"])
        self.assertIn("dead (Killed in battle", sent[-1]["content"])
        self.assertTrue(self._battle_entries()[-1]["battle_ended"])
        # The model is no longer asked to carry the pools itself (#751): the
        # records hold them, and the character list reads them out.
        self.assertIn("already on their records", summary)
        self.assertNotIn("carry the survivors", summary)
        # Nothing more comes after the summary, and the write is not repeated.
        self.aldwin.death_cause = "changed by hand"
        self.aldwin.save()
        before = len(self._battle_entries())
        self._stream("And then?")
        self.assertEqual(len(self._battle_entries()), before)
        self.aldwin.refresh_from_db()
        self.assertEqual(self.aldwin.death_cause, "changed by hand")

    def test_a_survivor_is_left_alive(self):
        battle = play_turn(self.battle, self.user)
        Battle.objects.filter(pk=battle.pk).update(status=Battle.STATUS_STALEMATE)
        self.session.refresh_from_db()
        self._stream()
        self.aldwin.refresh_from_db()
        self.assertTrue(self.aldwin.is_alive)
        self.assertIn("stalemate", self._battle_entries()[-1]["content"].lower())

    def test_the_verdict_line_is_the_engines_or_a_plain_fallback(self):
        battle = Battle.objects.get(pk=self.battle.pk)
        self.assertEqual(closing_line(battle), "The battle ended (setup)")
        battle.events.create(
            turn=1,
            phase=6,
            sequence=500,
            event_type="info",
            actor="Foes",
            payload={"winner_team": "Foes", "winners": [1]},
            message="Foes stands victorious — Gray Wolf left on the field",
        )
        # A session fight's side is named in words that agree with it
        # (issue #785); this asserted the engine's "Foes stands" as written.
        self.assertEqual(
            closing_line(battle),
            "The foes stand victorious — Gray Wolf left on the field",
        )

    def test_a_restart_voids_the_earlier_log_and_refiles(self):
        play_turn(self.battle, self.user)
        self._stream("one")
        filed = self._battle_entries()[-1]
        # A Game Master's restart: the turn marked Aldwin's record (#750).
        restart_battle(self.battle.pk, is_game_master=True)
        # The log is empty again: the line last filed is gone.
        sent = self._stream("two")
        contents = [m["content"] for m in sent]
        self.assertIn(RESTART_TEXT, contents)
        self.assertEqual(contents.index(RESTART_TEXT), len(contents) - 2)
        self.assertEqual(self._battle_entries()[-1]["battle_sequence"], 0)
        # The replayed turn is filed afresh after the notice.
        play_turn(Battle.objects.get(pk=self.battle.pk), self.user)
        sent = self._stream("three")
        refiled = [
            m["content"]
            for m in sent
            if m["content"].startswith(f"{LOG_PREAMBLE} — {self.battle.name}, turn 1:")
        ]
        self.assertEqual(len(refiled), 2)
        self.assertEqual(
            self._battle_entries()[-1]["battle_mark"], filed["battle_mark"]
        )

    def test_an_identical_replay_after_a_restart_files_nothing_twice(self):
        # Same seed, same choices: the replay reproduces the log line for
        # line, so what the model already holds is still the truth.
        play_turn(self.battle, self.user)
        first = self._stream("one")
        restart_battle(self.battle.pk, is_game_master=True)
        play_turn(Battle.objects.get(pk=self.battle.pk), self.user)
        second = self._stream("two")
        self.assertEqual(second[: len(first) - 1], first[:-1])
        self.assertNotIn(RESTART_TEXT, [m["content"] for m in second])

    def test_pending_entries_without_a_battle_or_after_the_end_are_empty(self):
        self.session.battle = None
        self.assertEqual(pending_battle_entries(self.session.history, self.session), [])
        self.session.battle = self.battle
        battle = self._finish_with_aldwin_dead()
        self.session.battle = battle
        entries = pending_battle_entries(self.session.history, self.session)
        self.assertTrue(entries[-1]["battle_ended"])
        self.assertEqual(
            pending_battle_entries(self.session.history + entries, self.session), []
        )

    def _play_to_the_end(self, battle: Battle) -> Battle:
        for _turn in range(80):
            if battle.status in battle_report.TERMINAL_STATUSES:
                return battle
            battle = play_turn(battle, self.user)
        self.fail(f"the fight did not end in 80 turns ({battle.status})")

    def test_a_restart_after_the_ending_was_filed_files_the_replay(self):
        # Issue #897: once the closing summary was filed, nothing about the
        # battle was ever filed again, so a Game Master's replay of a finished
        # fight reached the model as the first run's log.
        battle = self._play_to_the_end(self.battle)
        self._stream("How did it end?")
        self.assertTrue(self._battle_entries()[-1].get("battle_ended"))
        restart_battle(battle.pk, is_game_master=True)
        replay = play_turn(Battle.objects.get(pk=battle.pk), self.user)
        pending = pending_battle_entries(self.session.history, self.session)
        contents = [entry["content"] for entry in pending]
        self.assertEqual(contents[0], RESTART_TEXT)
        self.assertTrue(
            contents[1].startswith(f"{LOG_PREAMBLE} — {battle.name}, turn 1:"),
            contents,
        )
        # Filed with the next message, and the replay's own ending after it.
        self._stream("The replay begins.")
        self._play_to_the_end(replay)
        sent = self._stream("And now?")
        summaries = [
            message["content"]
            for message in sent
            if message["content"].startswith(SUMMARY_PREAMBLE)
        ]
        self.assertEqual(len(summaries), 2, "the replay's ending was not filed")
        self.assertTrue(self._battle_entries()[-1].get("battle_ended"))
        # Filed once: nothing new is pending after the replay's summary.
        self.assertEqual(pending_battle_entries(self.session.history, self.session), [])

    def test_a_restart_before_any_turn_was_filed_is_not_a_restart(self):
        position = battle_report.filed_position(self.session.history, self.battle.pk)
        assert position is not None
        self.assertEqual(position.sequence, 0)
        self.assertFalse(battle_report.restarted_since(self.battle, position))


class SessionFightFoesTest(BaseTestCase):
    """Whose the foes of a session's fight are (#762), and that it writes (#750)."""

    def fight_for(self, owner, mode: str) -> Battle:
        aldwin = create_character(owner, name="Aldwin")
        session = GMSession.objects.create(name="Quest", user=owner, mode=mode)
        session.characters.add(aldwin)
        with game_master_sets_the_foes_asked_for():
            started = start_fight(session, foes_text="gray wolf", seed=3)
        return Battle.objects.get(pk=started.battle.pk)

    def test_a_players_fight_gives_the_foes_to_the_game_master_in_either_mode(self):
        # A session's mode is the player's to switch and confers nothing
        # (#352): switching to aide mode used to hand the player the foes.
        for mode in (MODE_PLAY, MODE_AIDE):
            with self.subTest(mode=mode):
                battle = self.fight_for(self.create_user(f"player-{mode}"), mode)
                self.assertTrue(battle.foes_belong_to_game_master)
                self.assertTrue(battle.writes_to_records)

    def test_a_game_masters_session_keeps_them(self):
        battle = self.fight_for(self.create_dm(), MODE_AIDE)
        self.assertFalse(battle.foes_belong_to_game_master)
        self.assertTrue(battle.writes_to_records)


class OverlappingFightsTest(BaseTestCase):
    """Two /fight calls at once build one battle, not two (issue #671).

    ``start_fight`` asked "is a fight already under way?" off an unlocked
    in-memory session and did not lock the row until after ``create_battle``
    had written a whole Battle with its combatants and arena. Both requests
    passed the check, both built one, and the second ``save`` overwrote
    ``session.battle`` -- leaving the first in the database with the party
    seated to the player and nothing pointing at it, so its turns would never
    be filed and the GM would never narrate it.
    """

    def setUp(self):
        super().setUp()
        # Not about who chooses the foes (#841).
        self.enterContext(game_master_sets_the_foes_asked_for())
        self.user = self.create_user()
        self.session = GMSession.objects.create(name="The Wolf Road", user=self.user)
        self.aldwin = create_character(self.user, name="Aldwin")
        self.session.characters.add(self.aldwin)

    def test_the_second_request_is_refused_and_builds_nothing(self):
        real_parse = parse_foe_text
        landed = []

        def start_another(text: str):
            """A second /fight lands between the first one's check and its lock."""
            if not landed:
                landed.append(True)
                start_fight(
                    GMSession.objects.get(pk=self.session.pk), foes_text="dire wolf"
                )
            return real_parse(text)

        with patch("gm.fight.parse_foe_text", side_effect=start_another):
            with self.assertRaisesMessage(
                FightError, refusal_words().fight.fight_under_way
            ):
                start_fight(self.session, foes_text="gray wolf")

        self.assertEqual(Battle.objects.count(), 1)
        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.battle)
        openings = [
            message
            for message in self.session.history
            if is_battle_message(message) and not message.get("battle_ended")
        ]
        self.assertEqual(len(openings), 1)
        assert self.session.battle is not None
        self.assertEqual(openings[0]["battle_id"], self.session.battle.pk)


class StandInsAreNotLeftBehindTest(BaseTestCase):
    """A fight that is refused generates no orphan Beast rows (issue #675).

    ``stand_in_beast`` saves its variant the moment it is generated, and the
    trim to the field, the "No foes to fight" bail and any ``create_battle``
    failure all came afterwards and outside the transaction. The rows that
    were left carry ``is_generated=True``, so they stay out of the browsable
    catalog and out of ``find_catalog_beast``: the cost is dead rows and a
    wrong variant number for every later stand-in of the same base.
    """

    def setUp(self):
        super().setUp()
        # Stand-ins are made only for a Game Master's named foes now; a
        # player's are held to the catalog (#841).
        self.user = self.create_dm()
        self.session = GMSession.objects.create(name="The Wolf Road", user=self.user)
        self.session.characters.add(create_character(self.user, name="Aldwin"))

    def generated(self) -> int:
        return Beast.objects.filter(is_generated=True).count()

    def test_a_fight_refused_under_the_lock_leaves_no_variants(self):
        real_parse = parse_foe_text
        landed = []

        def start_another(text: str):
            if not landed:
                landed.append(True)
                start_fight(
                    GMSession.objects.get(pk=self.session.pk), foes_text="gray wolf"
                )
            return real_parse(text)

        before = self.generated()
        with patch("gm.fight.parse_foe_text", side_effect=start_another):
            with self.assertRaisesMessage(
                FightError, refusal_words().fight.fight_under_way
            ):
                start_fight(self.session, foes_text="shadow hound")

        self.assertEqual(self.generated(), before)

    def test_a_battle_that_cannot_be_created_leaves_no_variants(self):
        before = self.generated()

        with patch("gm.fight.create_battle", side_effect=RuntimeError("no arena")):
            with self.assertRaisesMessage(RuntimeError, "no arena"):
                start_fight(self.session, foes_text="shadow hound")

        self.assertEqual(self.generated(), before)

    def test_a_fight_that_does_happen_keeps_its_stand_in(self):
        before = self.generated()

        started = start_fight(self.session, foes_text="shadow hound")

        self.assertEqual(self.generated(), before + 1)
        self.assertIsNotNone(started.foes[0].generated_from)


class FightTakesTheTurnClaimTest(BaseTestCase):
    """/fight takes the session's one-turn claim, as a message does (#836).

    ``gm_fight`` took no claim: pressed while a turn was being planned, it
    appended the opening block and "The fight moves to the board" to the
    history, and the running turn then saved a reply written without knowing
    the fight had begun.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_dm()
        self.login_as(self.user)
        self.session = GMSession.objects.create(name="Quest", user=self.user)
        self.session.characters.add(create_character(self.user, name="Aldwin"))

    def _fight(self, foes: str = "gray wolf"):
        return self.client.post(
            FIGHT_URL,
            json.dumps({"session_id": self.session.pk, "foes": foes}),
            content_type="application/json",
        )

    def test_a_fight_is_refused_while_a_turn_is_running(self):
        running = GMTurnRecord.objects.create(
            user=self.user,
            session=self.session,
            outcome=GMTurnRecord.OUTCOME_RUNNING,
        )
        before = list(self.session.history)

        response = self._fight()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["running_turn"], running.pk)
        self.assertEqual(Battle.objects.count(), 0)
        self.session.refresh_from_db()
        self.assertEqual(self.session.history, before)
        self.assertIsNone(self.session.battle)

    def test_a_message_is_refused_while_a_fight_is_being_set_up(self):
        answers: list[int] = []

        def message_meanwhile(text: str):
            reply = self.client.post(
                STREAM_URL,
                json.dumps({"session_id": self.session.pk, "message": "I run"}),
                content_type="application/json",
            )
            answers.append(reply.status_code)
            return parse_foe_text(text)

        with patch("gm.fight.parse_foe_text", side_effect=message_meanwhile):
            response = self._fight()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(answers, [409])
        self.session.refresh_from_db()
        self.assertNotIn(
            "I run", [entry.get("content") for entry in self.session.history]
        )

    def test_the_fight_is_recorded_as_a_turn_that_owns_its_entries(self):
        before = len(self.session.history)

        self._fight()

        self.session.refresh_from_db()
        record = GMTurnRecord.objects.get(session=self.session)
        self.assertEqual(record.command, "fight")
        self.assertEqual(record.user_message, "/fight gray wolf")
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_OK)
        self.assertIsNone(record.prompt, "the foe call sends no system prompt")
        self.assertEqual(
            (record.history_start, record.history_end),
            (before, len(self.session.history)),
        )
        self.assertTrue(record.reply.startswith("The fight moves to the board"))
        self.assertIsNone(running_turn_for(self.user, self.session.pk))

    def test_a_refused_fight_releases_the_claim(self):
        self.session.characters.clear()

        response = self._fight()

        self.assertEqual(response.status_code, 400)
        record = GMTurnRecord.objects.get(session=self.session)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_REJECTED)
        self.assertIn(refusal_words().fight.attach_living_character, record.error)
        self.assertIsNone(running_turn_for(self.user, self.session.pk))

    def test_a_fight_that_fails_releases_the_claim(self):
        secret = "board broke reading /srv/tarmar/.env DB_PASSWORD=hunter2"
        with (
            patch("gm.views.start_fight", side_effect=RuntimeError(secret)),
            self.assertLogs("django.request", "ERROR") as logs,
            self.assertRaises(RuntimeError),
        ):
            self._fight()

        record = GMTurnRecord.objects.get(session=self.session)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_ERROR)
        # The record is read back to the player by gm:turn, so it holds the
        # fixed sentence; Django logs the traceback of what was raised (#13).
        self.assertEqual(record.error, refusal_words().views.server_error)
        state = self.client.get(f"/tarmar/gm/turns/{record.pk}/")
        self.assertNotIn("hunter2", state.content.decode())
        self.assertIn(secret, str(logged_exception(logs.records[0])))

    def test_a_retry_after_a_fight_still_knows_its_message_was_answered(self):
        """The #515 guard reads past a fight: a fight answers no message."""
        GMTurnRecord.objects.create(
            user=self.user,
            session=self.session,
            outcome=GMTurnRecord.OUTCOME_OK,
            user_message="We draw steel",
        )
        self._fight()

        response = self.client.post(
            STREAM_URL,
            json.dumps(
                {
                    "session_id": self.session.pk,
                    "message": "We draw steel",
                    "retry": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            refusal_words().views.turn_already_answered, response.json()["error"]
        )


class FightSeatsOnlyWhatTheOwnerMayChangeTest(BaseTestCase):
    """A session's fight writes to records, so it is held to the role (#837)."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm()
        self.other = self.create_user(username="their_player")
        self.session = GMSession.objects.create(name="Quest", user=self.game_master)
        self.theirs = create_character(self.other, name="Brenna")
        self.session.characters.add(self.theirs)

    def lose_the_role(self) -> None:
        self.game_master.is_dm = False
        self.game_master.save(update_fields=["is_dm"])

    def test_a_game_master_fights_with_another_players_character(self):
        started = start_fight(self.session, foes_text="gray wolf", seed=3)

        self.assertEqual([character.name for character in started.party], ["Brenna"])

    def test_after_losing_the_role_their_character_is_not_seated(self):
        mine = create_character(self.game_master, name="Gorm")
        self.session.characters.add(mine)
        self.lose_the_role()

        with game_master_sets_the_foes_asked_for():
            started = start_fight(self.session, foes_text="gray wolf", seed=3)

        self.assertEqual([character.name for character in started.party], ["Gorm"])
        self.assertFalse(
            started.battle.combatants.filter(character=self.theirs).exists()
        )

    def test_with_only_their_character_there_is_no_fight(self):
        self.lose_the_role()

        with self.assertRaisesMessage(
            FightError, refusal_words().fight.other_players_characters
        ):
            start_fight(self.session, foes_text="gray wolf", seed=3)

        self.assertEqual(Battle.objects.count(), 0)


class PlayersFoesAreTheGameMastersTest(BaseTestCase):
    """A player's ``/fight`` text is a request the Game Master answers (#841).

    #762 ruled a session fight's foes the Game Master's (coordinator's ruling
    under the standing rule; Spencer may overrule), and a player could still
    type ``/fight a rat`` and have exactly that built. Now the model, as
    their Game Master, sets the foes with a ``set_foes`` answer held to the
    bestiary catalog, and the opening line tells the player so.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.session = GMSession.objects.create(name="Quest", user=self.user)
        self.session.history = [
            {"role": "assistant", "content": "Three gray wolves leap from the ditch."}
        ]
        self.session.save()
        self.session.characters.add(create_character(self.user, name="Aldwin"))

    def _post(self, foes: str):
        return self.client.post(
            FIGHT_URL,
            json.dumps({"session_id": self.session.pk, "foes": foes}),
            content_type="application/json",
        )

    @staticmethod
    def answer(*foes: tuple[str, int]) -> str:
        return json.dumps(
            {
                "action": fight.SET_FOES_ACTION,
                "foes": [{"beast_slug": slug, "count": count} for slug, count in foes],
            }
        )

    def wolf_slug(self) -> str:
        beast = find_catalog_beast(FoeRequest(name="gray wolf", count=1))
        assert beast is not None
        return beast.slug

    @patch("gm.services.ollama_client.chat")
    def test_the_game_master_may_set_people_for_a_player(self, mock_chat):
        # Issue #894: a set_foes entry may name a person by calling, built as
        # the generated fighter a Game Master's "/fight a bandit" builds.
        self.session.history = [
            {"role": "assistant", "content": "Two bandits step out onto the road."}
        ]
        self.session.save()
        mock_chat.return_value = json.dumps(
            {
                "action": fight.SET_FOES_ACTION,
                "foes": [{"calling": "bandit", "count": 2}],
            }
        )

        response = self._post("the bandits")

        self.assertEqual(response.status_code, 200, response.content)
        battle = Battle.objects.get()
        people = list(
            battle.combatants.filter(beast__isnull=True, character__isnull=True)
            .order_by("pk")
            .values_list("display_name", "team")
        )
        self.assertEqual(people, [("Bandit", FOE_TEAM), ("Bandit (2)", FOE_TEAM)])
        figures = {figure["name"]: figure for figure in battle.state_json["combatants"]}
        self.assertEqual(figures["Bandit"]["archetype"], "unarmoured duelist")
        self.assertFalse(figures["Bandit"]["is_beast"])
        self.session.refresh_from_db()
        self.assertIn(
            "the Game Master set the foes", self.session.history[-1]["content"]
        )

    @patch("gm.services.ollama_client.chat")
    def test_the_players_text_is_a_request_and_the_game_master_sets_the_foes(
        self, mock_chat
    ):
        slug = self.wolf_slug()
        mock_chat.return_value = self.answer((slug, 3))

        response = self._post("an ancient red dragon")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([foe["count"] for foe in response.json()["foes"]], [3])
        battle = Battle.objects.get()
        self.assertEqual(
            sorted(
                set(
                    battle.combatants.exclude(beast=None).values_list(
                        "beast__slug", flat=True
                    )
                )
            ),
            [slug],
        )
        asked = mock_chat.call_args.args[1][-1]["content"]
        self.assertIn('The player asked for: "an ancient red dragon"', asked)
        self.assertIn(f"{slug}:", asked, "the catalog is offered by slug")
        self.assertEqual(
            mock_chat.call_args.kwargs["response_format"], fight.SET_FOES_SCHEMA
        )
        self.session.refresh_from_db()
        opening = self.session.history[-1]["content"]
        self.assertTrue(
            opening.startswith(
                'You asked for "an ancient red dragon"; the Game Master set the foes.'
            ),
            opening,
        )

    @patch("gm.services.ollama_client.chat")
    def test_a_blank_fight_is_set_by_the_game_master_too(self, mock_chat):
        mock_chat.return_value = self.answer((self.wolf_slug(), 1))

        response = self._post("")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn(
            "The player named no foes.", mock_chat.call_args.args[1][-1]["content"]
        )
        self.session.refresh_from_db()
        self.assertTrue(
            self.session.history[-1]["content"].startswith(
                "the Game Master set the foes."
            )
        )

    @patch("gm.services.ollama_client.chat")
    def test_a_beast_the_catalog_lacks_is_asked_again_and_never_made(self, mock_chat):
        variants_before = Beast.objects.count()
        mock_chat.side_effect = [
            self.answer(("a-rat-of-my-own-design", 1)),
            self.answer((self.wolf_slug(), 2)),
        ]

        response = self._post("a rat of my own design")

        self.assertEqual(response.status_code, 200, response.content)
        retry = mock_chat.call_args_list[1].args[1][-1]["content"]
        self.assertIn("'a-rat-of-my-own-design' is not a slug from the bestiary", retry)
        self.assertEqual(Beast.objects.count(), variants_before, "no stand-in made")

    @patch("gm.services.ollama_client.chat")
    def test_every_foe_call_shares_one_turns_budget(self, mock_chat):
        """Issue #936: each call had the 300 s read timeout and nothing more.

        The no-``format`` fallback and the second ask used to add a call's
        worth each; now all of them end at one deadline, the fight's budget on.
        """
        refused = MagicMock(status_code=STRUCTURED_OUTPUT_UNAVAILABLE_STATUS)
        mock_chat.side_effect = [
            httpx.HTTPStatusError("501", request=MagicMock(), response=refused),
            self.answer(("no-such-beast", 1)),
            self.answer((self.wolf_slug(), 1)),
        ]

        with self.settings(GM_FIGHT_BUDGET_SECONDS=40, GM_STREAM_BUDGET_SECONDS=300):
            asked_at = time.monotonic()
            response = self._post("a wolf")

        self.assertEqual(response.status_code, 200, response.content)
        deadlines = {call.kwargs["stop_at"] for call in mock_chat.call_args_list}
        self.assertEqual(len(mock_chat.call_args_list), 3)
        self.assertEqual(len(deadlines), 1, deadlines)
        self.assertAlmostEqual(deadlines.pop() - asked_at, 40, delta=5)

    @patch("gm.services.ollama_client.chat")
    def test_a_foe_call_cut_at_its_deadline_is_said_in_words(self, mock_chat):
        """Issue #936: a cut is the player's to read, not a 500."""
        mock_chat.side_effect = StreamDeadlinePassed("cut off at its deadline")

        response = self._post("wolves")

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"], settings.GM_FIGHT_TOO_SLOW_MESSAGE)
        self.assertIsNone(Battle.objects.first())
        record = GMTurnRecord.objects.get(session=self.session)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_TOO_SLOW)
        self.assertIsNone(running_turn_for(self.user, self.session.pk))

    @patch("gm.services.ollama_client.chat")
    def test_two_unusable_answers_start_no_fight(self, mock_chat):
        mock_chat.side_effect = ["nope", self.answer(("no-such-beast", 1))]

        response = self._post("a rat")

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            refusal_words().fight.no_foes_in_bestiary, response.json()["error"]
        )
        self.assertIsNone(Battle.objects.first())

    @patch("gm.services.ollama_client.chat")
    def test_a_runner_without_structured_output_gets_the_schema_in_the_ask(
        self, mock_chat
    ):
        refused = MagicMock(status_code=STRUCTURED_OUTPUT_UNAVAILABLE_STATUS)
        mock_chat.side_effect = [
            httpx.HTTPStatusError("501", request=MagicMock(), response=refused),
            self.answer((self.wolf_slug(), 1)),
        ]

        response = self._post("wolves")

        self.assertEqual(response.status_code, 200, response.content)
        fallback = mock_chat.call_args_list[1].args[1][-1]["content"]
        self.assertIn(SCHEMA_IN_PROMPT_PREAMBLE, fallback)
        self.assertIn('"beast_slug"', fallback)

    @patch("gm.services.ollama_client.chat")
    def test_a_model_that_cannot_be_reached_is_said_in_words(self, mock_chat):
        """Review of #841: this was an HTTP 500 with the turn record's error.

        The record says what the player was told, since ``gm:turn`` reads it
        back to them; the model error, which names the model host, goes to
        the log (#13).
        """
        secret = "connection refused by http://10.0.0.9:11434 token=s3cr3t"
        mock_chat.side_effect = httpx.ConnectError(secret)

        with self.assertLogs("gm.views", "WARNING") as logs:
            response = self._post("wolves")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"], refusal_words().views.fight_model_unreachable
        )
        self.assertIsNone(Battle.objects.first())
        record = GMTurnRecord.objects.get(session=self.session)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_ERROR)
        self.assertEqual(record.error, refusal_words().views.fight_model_unreachable)
        state = self.client.get(f"/tarmar/gm/turns/{record.pk}/")
        self.assertNotIn("s3cr3t", state.content.decode())
        self.assertIn(secret, "\n".join(logs.output))
        self.assertIsNone(running_turn_for(self.user, self.session.pk))

    def test_a_game_masters_named_foes_are_still_built_as_named(self):
        """The neighbouring case: his /fight <foes> needs no model."""
        game_master = self.create_dm()
        session = GMSession.objects.create(name="His", user=game_master)
        session.characters.add(create_character(game_master, name="Gorm"))

        with patch("gm.services.ollama_client.chat") as mock_chat:
            started = start_fight(session, foes_text="two gray wolves")

        mock_chat.assert_not_called()
        self.assertFalse(started.chosen_by_game_master)
        self.assertEqual(
            [(foe.name, foe.count) for foe in started.foes], [("Gray Wolf", 2)]
        )
        session.refresh_from_db()
        self.assertTrue(session.history[-1]["content"].startswith("The fight moves"))


class ReadSetFoesTest(BaseTestCase):
    def test_every_slug_must_be_the_catalogs(self):
        found = find_catalog_beast(FoeRequest(name="gray wolf", count=1))
        assert found is not None
        known = found.slug
        for answer, ok in (
            ({"action": "set_foes", "foes": [{"beast_slug": known, "count": 2}]}, True),
            (
                {"action": "set_foes", "foes": [{"beast_slug": "made-up", "count": 1}]},
                False,
            ),
            ({"action": "set_foes", "foes": []}, False),
            (
                {"action": "set_foes", "foes": [{"beast_slug": known, "count": 0}]},
                False,
            ),
            (
                {"action": "set_foes", "foes": [{"beast_slug": known, "count": True}]},
                False,
            ),
            ({"action": "other", "foes": [{"beast_slug": known, "count": 1}]}, False),
            ({"foes": [{"beast_slug": known, "count": 1}]}, False),
        ):
            with self.subTest(answer=answer):
                requests, errors = fight.read_set_foes(json.dumps(answer))
                self.assertEqual(bool(requests), ok, errors)
                self.assertEqual(not errors, ok)

    def test_a_person_is_named_by_a_known_calling(self):
        # Issue #894: a player's fight could only ever face catalog beasts.
        found = find_catalog_beast(FoeRequest(name="gray wolf", count=1))
        assert found is not None
        requests, errors = fight.read_set_foes(
            json.dumps(
                {
                    "action": "set_foes",
                    "foes": [
                        {"calling": "bandit", "count": 2},
                        {"beast_slug": found.slug, "count": 1},
                    ],
                }
            )
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            [
                (request.calling, request.beast_slug, request.count)
                for request in requests
            ],
            [("bandit", "", 2), ("", found.slug, 1)],
        )
        for answer in (
            {"action": "set_foes", "foes": [{"calling": "dragon", "count": 1}]},
            {
                "action": "set_foes",
                "foes": [{"calling": "skeleton warrior", "count": 1}],
            },
            {"action": "set_foes", "foes": [{"calling": 7, "count": 1}]},
            {"action": "set_foes", "foes": [{"calling": "bandit", "count": 0}]},
            {"action": "set_foes", "foes": [{"calling": "bandit", "count": True}]},
        ):
            with self.subTest(answer=answer):
                requests, errors = fight.read_set_foes(json.dumps(answer))
                self.assertEqual(requests, [])
                self.assertTrue(errors)

    def test_the_ask_offers_the_callings(self):
        asked = fight.set_foes_message(4, "some bandits")["content"]
        self.assertIn('{"calling": "bandit", "count": 1}', asked)
        for calling in ("bandit", "guard", "cultist", "wizard"):
            self.assertIn(calling, asked)
        schema_asked = fight.set_foes_message(4, "", schema_in_prompt=True)["content"]
        self.assertIn('"calling"', schema_asked)

    def test_counts_are_held_to_the_field(self):
        found = find_catalog_beast(FoeRequest(name="gray wolf", count=1))
        assert found is not None
        known = found.slug

        requests, _errors = fight.read_set_foes(
            json.dumps(
                {"action": "set_foes", "foes": [{"beast_slug": known, "count": 99}]}
            )
        )

        self.assertEqual(requests[0].count, fight.MAX_FOES_PER_KIND)
