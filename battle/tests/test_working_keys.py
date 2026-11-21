"""A working's key, read from a battle's state or a menu candidate (#208).

A working is a spell the canon's True Magic rules build from an effect and a
tier (tarmar-engine #77), not a catalog entry, so the catalog's own lookup
(``tarmar_engine.spells.get_spell``) raises ``KeyError`` for its key. Every
studio reader of a spell key from state or a candidate asks
:func:`battle.magic_rules.loaded_spell` instead: the roster card's Runaway and
contest lines, and the menu's anchor line, cast line and borrowed mana.

Since the battle screen's own slice (tarmar-studio #208) a craft that stands
is a key read from state too: by the roster card, the turn menu's craft line
and the cast stage, each through :mod:`battle.workings`.

The True Magic rules here are invented (``magic_fixtures.TRUE_MAGIC_RULES``).
"""

from dataclasses import replace

from django.test import SimpleTestCase
from tarmar_engine.actions import CRAFT_OPTION
from tarmar_engine.policy import ANCHOR_OPTION, Candidate
from tarmar_engine.spells import get_spell
from tarmar_engine.state import ContestState, CraftState, RunawayState

from battle import turn_menu
from battle.engine.state import BattleState
from battle.magic_rules import loaded_spell
from battle.persistence import TARMAR_PROFILE
from battle.services import caster_magic
from battle.viewmodel import tarmar_view
from battle.wording import words

from .magic_fixtures import (
    DEFAULT_MAGIC_RULES,
    INVENTED_RULES,
    REND_EFFECT,
    RUNAWAY_RULES,
    TRUE_MAGIC,
    TRUE_MAGIC_RULES,
    TRUE_SKILL,
    seated_rules,
)
from .rulings_words import with_fixed_words
from .test_state import make_combatant

#: The working these tests read: the invented damaging effect at tier 2, and
#: the name the engine builds for it.
TIER = 2
WORKING_KEY = TRUE_MAGIC.working_key(REND_EFFECT.key, TIER)
WORKING_NAME = "Rending (tier 2)"

#: :data:`TRUE_MAGIC_RULES` with Runaway's later turns, so a working that
#: runs away has an anchor to offer.
TRUE_MAGIC_WITH_RUNAWAY = replace(TRUE_MAGIC_RULES, runaway=RUNAWAY_RULES.runaway)


def board(**caster_fields: object) -> BattleState:
    """A caster (combatant 1) and a second figure, the caster as given."""
    return BattleState(
        arena_radius=6,
        combatants=[
            make_combatant(1, name="Caster", mana=1, max_mana=9, **caster_fields),
            make_combatant(2, name="Other", q=3, r=0),
        ],
    )


class LoadedSpellTest(SimpleTestCase):
    def test_a_catalog_key_is_the_catalogs_spell(self) -> None:
        with seated_rules(TRUE_MAGIC_RULES):
            self.assertIs(loaded_spell("fire_ball"), get_spell("fire_ball"))
        with seated_rules(INVENTED_RULES):
            self.assertIs(loaded_spell("heal"), get_spell("heal"))

    def test_a_working_key_is_the_working_the_loaded_rules_build(self) -> None:
        self.assertEqual(WORKING_KEY, "working:rend:2")
        with seated_rules(TRUE_MAGIC_RULES):
            working = loaded_spell(WORKING_KEY)
        self.assertEqual(working, TRUE_MAGIC.working(REND_EFFECT.key, TIER))
        self.assertEqual(
            (working.name, working.level, working.damage), (WORKING_NAME, TIER, "2d4")
        )

    def test_the_default_canon_builds_its_own_workings(self) -> None:
        true_magic = DEFAULT_MAGIC_RULES.true_magic
        assert true_magic is not None
        for effect in true_magic.effects:
            key = true_magic.working_key(effect.key, 1)
            with self.subTest(key=key), seated_rules(DEFAULT_MAGIC_RULES):
                self.assertEqual(loaded_spell(key), true_magic.working(effect.key, 1))

    def test_a_key_nothing_builds_is_a_key_error(self) -> None:
        with seated_rules(TRUE_MAGIC_RULES):
            for key in ("working:no_such_effect:1", "working:rend:0", "wish"):
                with self.subTest(key=key), self.assertRaises(KeyError):
                    loaded_spell(key)
        # A canon with no True Magic builds no working at all.
        with seated_rules(INVENTED_RULES), self.assertRaises(KeyError):
            loaded_spell(WORKING_KEY)


class RosterCardTest(SimpleTestCase):
    """The roster card's lines for a working that runs away or is countered."""

    def test_a_working_running_away_is_named_on_its_casters_card(self) -> None:
        state = board(runaway=RunawayState(WORKING_KEY, TIER, 1))
        with seated_rules(TRUE_MAGIC_RULES):
            card = tarmar_view(state, next_sequence=1)["combatants"][0]["runaway"]
        self.assertEqual(
            (card["spell"], card["spell_key"]), (WORKING_NAME, WORKING_KEY)
        )
        self.assertEqual(card["line"], f"Runaway {WORKING_NAME}: has fed for 1 turn")

    def test_a_countered_working_is_named_on_its_casters_card(self) -> None:
        state = board(
            contest=ContestState(
                spell_key=WORKING_KEY, target_id=2, defender_ids=[2], turns=1
            )
        )
        with seated_rules(TRUE_MAGIC_RULES):
            card = tarmar_view(state, next_sequence=1)["combatants"][0]["contest"]
        self.assertEqual(
            (card["spell"], card["spell_key"]), (WORKING_NAME, WORKING_KEY)
        )
        self.assertTrue(
            card["line"].startswith(f"{WORKING_NAME} countered once by Other"),
            card["line"],
        )


@with_fixed_words
class MenuLineTest(SimpleTestCase):
    """The menu's own readers of a candidate's or a Runaway's spell key."""

    def described(
        self, state: BattleState, candidate: Candidate
    ) -> turn_menu.DescribedCandidate:
        return turn_menu.describe(state, state.by_id(1), candidate, ai_choice=False)

    def test_an_anchor_names_the_working_that_runs_away(self) -> None:
        state = board(runaway=RunawayState(WORKING_KEY, TIER, 1))
        own_anchor = Candidate(
            ANCHOR_OPTION, "ANCHOR", 0.0, "", target_id=1, anchor_target=1
        )
        with seated_rules(TRUE_MAGIC_WITH_RUNAWAY):
            described = self.described(state, own_anchor)
            roll = RUNAWAY_RULES.runaway
            assert roll is not None
            self.assertEqual(
                described.plain,
                words().tarmar_menu.self_anchor.format(
                    spell=WORKING_NAME,
                    dice=roll.self_anchor.dice,
                    attribute=roll.self_anchor.attribute,
                ),
            )
        self.assertIn(WORKING_NAME, described.plain)

    def test_a_cast_line_names_a_working(self) -> None:
        cast = Candidate("h", "CAST SPELL", 0.0, "", target_id=2, spell_key=WORKING_KEY)
        with seated_rules(TRUE_MAGIC_RULES):
            described = self.described(board(), cast)
            self.assertEqual(
                described.plain,
                words().turn_menu.cast_at.format(spell=WORKING_NAME, aimed="Other"),
            )
        self.assertIn(WORKING_NAME, described.plain)

    def test_a_borrowing_craft_reads_the_workings_cost(self) -> None:
        # Tier 2 and 2 pushed is 4 mana; the caster holds 1.
        craft = Candidate(
            CRAFT_OPTION,
            "CRAFT WORKING",
            0.0,
            "",
            spell_key=WORKING_KEY,
            push_mana=2,
            borrow=True,
        )
        state = board()
        with seated_rules(TRUE_MAGIC_RULES):
            self.assertEqual(turn_menu.borrowed_mana(state.by_id(1), craft), 3)
            self.assertEqual(self.described(state, craft).borrowed, 3)


class EveryReaderOfAKeyFromStateTest(SimpleTestCase):
    """A working's key in every place a figure's state holds a spell's, read
    by every reader of one in a single pass: none of them asks the catalog."""

    def test_a_working_everywhere_at_once_crashes_no_reader(self) -> None:
        # One working runs away from the caster, another stands countered,
        # and a third is being crafted.
        state = board(
            skill_levels={TRUE_SKILL: 1},
            runaway=RunawayState(WORKING_KEY, TIER, 1),
            contest=ContestState(
                spell_key=WORKING_KEY, target_id=2, defender_ids=[2], turns=1
            ),
            crafting=CraftState(WORKING_KEY, 0, False, 1, 0, 2),
        )
        with seated_rules(TRUE_MAGIC_WITH_RUNAWAY), with_fixed_words:
            card = tarmar_view(state, next_sequence=1)["combatants"][0]
            menu = [
                turn_menu.menu_payload(option)
                for option in turn_menu.player_menu(
                    TARMAR_PROFILE, state, state.by_id(1)
                )
            ]
            stage = caster_magic(state, 1, menu)
        self.assertEqual(
            [card[key]["spell"] for key in ("runaway", "contest", "crafting")],
            [WORKING_NAME] * 3,
        )
        # The menu names it on the anchor of its Runaway and on the craft's
        # continuation, and the stage on the craft that stands.
        named = [option["plain"] for option in menu if WORKING_NAME in option["plain"]]
        self.assertEqual(
            sorted({line.split()[0] for line in named}),
            ["tarmar_menu.self_anchor", "true_magic.craft_aimed"],
        )
        assert stage is not None
        self.assertEqual(stage["craft"]["standing"]["name"], WORKING_NAME)

    def test_a_key_nothing_builds_is_refused_by_name_not_guessed_at(self) -> None:
        # A craft of a working the loaded canon does not build is a broken
        # state, which the engine refuses too: the card says which key.
        state = board(
            crafting=CraftState("working:no_such_effect:1", 0, False, 1, 0, 2)
        )
        with seated_rules(TRUE_MAGIC_RULES), self.assertRaises(KeyError) as refused:
            tarmar_view(state, next_sequence=1)
        self.assertIn("no_such_effect", str(refused.exception))
