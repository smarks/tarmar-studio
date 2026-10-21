"""Issue #18: a spell cast outside a battle, from the record.

casting-spells.md: "Spells cost mana equal to the spell's level" and "Roll:
3d6 ≤ INT or WIS (depending on the spell)"; the very high and very low
totals are the canon's ``casting_failure`` block and success tiers, judged by
the board's own ceiling (``battle.magic_rules.loaded_casting_success_ceiling``).

Most tests seat invented magic rules (``battle.tests.magic_fixtures``), so the
totals they pin hold under any canon; the last class reads the loaded canon's.
"""

from dataclasses import replace
from unittest.mock import patch

from tarmar_engine.magic import CastingSuccessTier
from tarmar_engine.spells import SPELLS

from battle.magic_rules import magic_rules
from battle.tests.magic_fixtures import CASTING_FAILURE_RULES, seated_rules
from characters.equipment import get_item
from characters.magic import CASTING_DICE, cast_in_story
from characters.services import CharacterChangeError
from characters.skill_checks import injury_penalty
from common.rolling import dice_total_range
from common.test_helpers import BaseTestCase, FacesRoller, create_character

#: The seated rules: a natural 12 or more fails, 12 and 13 spend the mana, 13
#: starts a Runaway (magic_fixtures.CASTING_FAILURE), and a natural 3 or 4 is
#: a bright cast that gives one mana back.
RULES = replace(
    CASTING_FAILURE_RULES,
    casting_success_tiers=(
        CastingSuccessTier(
            key="bright",
            label="Bright cast",
            natural_totals=frozenset({3, 4}),
            effect_bonus=1,
            mana_refund=1,
        ),
    ),
)

#: A level-3 INT spell that must hit, and a WIS spell that need not.
BOLT = SPELLS["lightning_bolt"]
HEAL = SPELLS["heal"]

#: Where the board's seat answers whether the character is fighting.
LIVE_FIGHT = "characters.magic.seated_in_a_live_fight"


def faces_for(total: int) -> FacesRoller:
    """A roller whose casting dice come to ``total`` (three faces, high first)."""
    faces = []
    remaining = total
    for dice_left in range(3, 0, -1):
        face = min(6, remaining - (dice_left - 1))
        faces.append(face)
        remaining -= face
    return FacesRoller(*faces)


class StoryCastTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(seated_rules(RULES))
        self.caster = create_character(
            self.create_user("story_caster"),
            name="Story Caster",
            intelligence=14,
            wisdom=10,
            dexterity=12,
            free_spell_points=6,
            spells={BOLT.key: 1, HEAL.key: 1},
        )

    def cast(self, spell=BOLT, total=9, *, is_game_master=False, **options):
        return cast_in_story(
            self.caster,
            spell.key,
            is_game_master=is_game_master,
            roller=options.pop("roller", faces_for(total)),
            **options,
        )


class CastingRollTest(StoryCastTestCase):
    def test_a_success_spends_the_spells_level_and_rolls_to_hit(self):
        cast = self.cast(total=9)
        self.assertTrue(cast.succeeded)
        self.assertEqual(cast.ceiling, 11)
        self.assertEqual(
            (cast.mana_spent, cast.mana_left), (BOLT.level, 6 - BOLT.level)
        )
        self.assertEqual(self.caster.mana_spent, BOLT.level)
        self.assertIn(
            f"casts {BOLT.name}: 3d6: [6, 2, 1] = 9 ≤ 11 (INT 14): cast; "
            f"{BOLT.level} mana spent, {6 - BOLT.level} left",
            cast.line,
        )
        # "Magic requiring hitting a target needs an additional DEX roll."
        self.assertEqual(cast.hit, "to hit, 3d6: [6, 2, 1] = 9 ≤ 12 (DEX): hits")
        self.assertIn(cast.hit, cast.line)

    def test_the_aim_is_the_boards_dex_with_worn_armour(self):
        # The board aims under combat_dexterity_without_modifiers: worn
        # armour's own DEX penalty counts (battle.services' seat).
        chainmail = get_item("chainmail")
        assert chainmail is not None, "the loaded catalog carries chainmail"
        penalty = chainmail["dex_penalty"]
        self.assertLess(penalty, 0)
        self.caster.equipment = [
            {"item_id": "chainmail", "quantity": 1, "in_pack": False, "equipped": True}
        ]
        cast = self.cast(total=12 + penalty)
        self.assertTrue(cast.hit.endswith(f"≤ {12 + penalty} (DEX): hits"), cast.hit)
        cast = self.cast(total=12 + penalty + 1)
        self.assertIn(f"> {12 + penalty} (DEX): misses", cast.hit)

    def test_the_injury_table_lowers_the_aim_too(self):
        fatigue = self.caster.fatigue
        assert fatigue is not None, "the caster's Fatigue pool is known"
        self.caster.fatigue_damage = fatigue - 3
        cast = self.cast(total=11)
        self.assertIn("> 10 (DEX): misses", cast.hit)

    def test_a_spell_that_need_not_hit_rolls_no_dex(self):
        self.assertEqual(self.cast(HEAL, total=6).hit, "")

    def test_the_spells_attribute_is_the_one_rolled_under(self):
        cast = self.cast(HEAL, total=10)
        self.assertEqual((cast.attribute, cast.attribute_score), (HEAL.attribute, 10))
        self.assertTrue(cast.succeeded)
        self.assertFalse(self.cast(HEAL, total=11).succeeded)

    def test_a_failure_over_the_attribute_keeps_the_mana(self):
        self.caster.intelligence = 8
        cast = self.cast(total=10)
        self.assertFalse(cast.succeeded)
        self.assertEqual((cast.mana_spent, self.caster.mana_spent), (0, 0))
        self.assertIn("= 10 > 8 (INT 8): fails; 0 mana spent, 6 left", cast.line)
        self.assertEqual(cast.hit, "")

    def test_the_fumble_floor_fails_a_total_under_the_attribute_and_spends(self):
        self.caster.intelligence = 18
        cast = self.cast(total=12)
        self.assertEqual(cast.ceiling, 11)
        self.assertFalse(cast.succeeded)
        self.assertEqual(cast.mana_spent, BOLT.level)
        self.assertFalse(cast.runaway)

    def test_the_runaway_total_starts_a_runaway_the_game_master_runs(self):
        self.caster.intelligence = 18
        cast = self.cast(total=13)
        self.assertTrue(cast.runaway)
        self.assertEqual(cast.mana_spent, BOLT.level)
        self.assertIn("a Runaway starts, which the Game Master runs", cast.line)

    def test_a_success_tier_gives_mana_back(self):
        cast = self.cast(total=4)
        self.assertEqual(cast.tier, "Bright cast")
        self.assertEqual((cast.mana_refund, cast.mana_spent), (1, BOLT.level - 1))
        self.assertEqual(self.caster.mana_spent, BOLT.level - 1)
        self.assertIn(", Bright cast;", cast.line)
        self.assertIn(f"{BOLT.level - 1} mana spent after 1 back", cast.line)

    def test_the_injury_table_lowers_the_attribute(self):
        fatigue = self.caster.fatigue
        assert fatigue is not None, "the caster's Fatigue pool is known"
        self.caster.fatigue_damage = fatigue - 3
        self.assertEqual(injury_penalty(self.caster), -2)
        cast = self.cast(total=11)
        self.assertEqual(cast.ceiling, 11)
        self.caster.intelligence = 12
        cast = self.cast(total=11)
        self.assertEqual(cast.ceiling, 10)
        self.assertFalse(cast.succeeded)
        self.assertIn("(INT 12 -2 injured)", cast.line)


class PlayerHeldToTheRulesTest(StoryCastTestCase):
    def assert_refused(self, reason, **cast_options):
        spent_before = self.caster.mana_spent
        with self.assertRaises(CharacterChangeError) as refused:
            self.cast(**cast_options)
        self.assertIn(reason, str(refused.exception))
        self.assertEqual(self.caster.mana_spent, spent_before)

    def test_a_spell_not_on_the_record_is_refused(self):
        self.assert_refused("learn it first", spell=SPELLS["fire_ball"])
        self.caster.spells = {BOLT.key: 0}
        self.assert_refused("learn it first")

    def test_the_pool_must_hold_the_whole_cost(self):
        self.caster.mana_spent = 6 - BOLT.level + 1
        self.assert_refused(f"costs {BOLT.level} mana and the Pool holds")

    def test_only_a_game_master_states_the_dice(self):
        self.assert_refused("Only a Game Master can state the dice", stated_total=5)

    def test_an_unconscious_caster_casts_nothing(self):
        self.caster.fatigue_damage = self.caster.fatigue
        self.assert_refused("is unconscious")

    def test_a_dead_caster_casts_nothing_even_for_a_game_master(self):
        self.caster.is_alive = False
        self.assert_refused("is dead", is_game_master=True)

    def test_nobody_casts_here_for_a_character_on_a_live_board(self):
        with patch(LIVE_FIGHT, return_value=True):
            self.assert_refused("is fighting on the board", is_game_master=True)

    def test_a_spell_that_must_hit_needs_dex_before_anything_is_rolled(self):
        self.caster.dexterity = None
        self.assert_refused("Dexterity is not recorded yet", is_game_master=True)
        self.assertEqual(self.cast(HEAL, total=6).mana_spent, HEAL.level)

    def test_an_unknown_spell_and_an_unrecorded_attribute_are_refused(self):
        with self.assertRaises(CharacterChangeError):
            cast_in_story(self.caster, "wish", is_game_master=True)
        self.caster.intelligence = None
        self.assert_refused("Intelligence is not recorded yet", is_game_master=True)


class GameMasterOverridesTest(StoryCastTestCase):
    def test_a_game_master_casts_a_spell_off_the_record_and_it_is_named(self):
        cast = self.cast(SPELLS["fire_ball"], total=9, is_game_master=True)
        self.assertTrue(cast.succeeded)
        self.assertIn("Fire Ball is not on the record", cast.game_master_call)
        self.assertIn(
            "(a Game Master's call: Fire Ball is not on the record)", cast.line
        )

    def test_past_the_pool_spends_only_what_is_there(self):
        self.caster.mana_spent = 5
        cast = self.cast(total=9, is_game_master=True)
        self.assertEqual((cast.mana_spent, self.caster.mana_spent), (1, 6))
        self.assertEqual(cast.mana_left, 0)
        self.assertIn(
            f"{BOLT.level - 1} mana past the Pool, not spent", cast.game_master_call
        )

    def test_a_stated_total_is_judged_as_a_rolled_one(self):
        cast = self.cast(total=0, is_game_master=True, stated_total=13, roller=None)
        self.assertEqual(cast.dice, f"{CASTING_DICE} stated by a Game Master = 13")
        self.assertTrue(cast.runaway)
        self.assertIn("the dice stated", cast.game_master_call)
        lowest, highest = dice_total_range(CASTING_DICE)
        for total in (lowest - 1, highest + 1):
            with self.subTest(total=total), self.assertRaises(CharacterChangeError):
                self.cast(is_game_master=True, stated_total=total, roller=None)

    def test_a_refund_never_returns_mana_that_was_not_spent(self):
        self.caster.mana_spent = 6
        cast = self.cast(HEAL, total=4, is_game_master=True)
        self.assertEqual(cast.tier, "Bright cast")
        self.assertEqual((cast.mana_spent, cast.mana_refund), (0, 0))
        self.assertEqual(self.caster.mana_spent, 6)

    def test_the_aim_after_a_stated_total_says_the_server_rolled_it(self):
        cast = self.cast(is_game_master=True, stated_total=9)
        self.assertTrue(cast.hit.startswith("to hit rolled by the server, 3d6: "))

    def test_a_game_master_calls_a_cast_for_an_unconscious_caster(self):
        self.caster.fatigue_damage = self.caster.fatigue
        cast = self.cast(total=9, is_game_master=True)
        self.assertIn("unconscious", cast.game_master_call)

    def test_an_ordinary_game_master_cast_names_no_call(self):
        self.assertEqual(self.cast(total=9, is_game_master=True).game_master_call, "")


class LoadedCanonCeilingTest(BaseTestCase):
    """A cast off the board is judged by the loaded canon's casting failure
    block: at the fumble floor it fails whatever the attribute, and one under
    it succeeds."""

    def test_a_cast_at_the_fumble_floor_fails_whatever_the_attribute(self):
        casting_failure = magic_rules().casting_failure
        if casting_failure is None:
            self.skipTest(
                "the loaded canon's casting_failure block is null: no total "
                "fails on its own"
            )
        caster = create_character(
            self.create_user("floor_caster"),
            name="Floor Caster",
            intelligence=30,
            free_spell_points=9,
            spells={BOLT.key: 1},
        )
        floor = casting_failure.fumble_floor
        cast = cast_in_story(
            caster, BOLT.key, is_game_master=False, roller=faces_for(floor)
        )
        self.assertFalse(cast.succeeded)
        self.assertEqual(cast.ceiling, floor - 1)
        expected_spent = BOLT.level if floor in casting_failure.mana_lost_totals else 0
        self.assertEqual(cast.mana_spent, expected_spent)
        self.assertEqual(cast.runaway, floor == casting_failure.runaway_total)
        under = cast_in_story(
            caster, BOLT.key, is_game_master=False, roller=faces_for(floor - 1)
        )
        self.assertTrue(under.succeeded)
