"""The attribute check with no skill: 3d6 ≤ the attribute (issue #911).

attack-rolls.md: "An attribute check with no skill, survival saves, and
spellcasting still roll **3d6 ≤ attribute**." The rule reads one way, so the
injury table's -1/-2 lowers the target here, and nobody names a direction
it could be counted in backwards.
"""

from characters.refusal_words import game_master_call_note
from characters.services import CharacterChangeError
from characters.skill_checks import AttributeCheck, attribute_check
from characters.skill_rules import skill_rules
from common.test_helpers import BaseTestCase, FacesRoller, create_character


def hurt_to(character, *, fatigue_left: int) -> None:
    """Leave ``character`` with ``fatigue_left`` Fatigue and Body untouched."""
    fatigue = character.fatigue
    assert fatigue is not None
    character.fatigue_damage = fatigue - fatigue_left
    character.save()


class AttributeCheckTest(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.character = create_character(
            self.create_user(), name="Dain", constitution=12
        )

    def check(self, *faces: int, **options) -> AttributeCheck:
        return attribute_check(
            self.character,
            "constitution",
            roller=FacesRoller(*faces),
            **options,
        )

    def test_unhurt_the_target_is_the_score(self) -> None:
        check = self.check(4, 4, 4)
        self.assertEqual((check.target, check.passed), (12, True))
        self.assertEqual(
            check.line,
            "constitution check, 3d6: [4, 4, 4] = 12 ≤ 12 (CON 12): passes",
        )

    def test_each_injury_band_lowers_the_target(self) -> None:
        fatigue = self.character.fatigue
        assert fatigue is not None
        for fatigue_left, penalty in ((fatigue // 2, -1), (5, -2)):
            with self.subTest(penalty=penalty):
                hurt_to(self.character, fatigue_left=fatigue_left)
                check = self.check(4, 4, 4)
                self.assertEqual(check.injury_penalty, penalty)
                self.assertEqual(check.target, 12 + penalty)
                self.assertFalse(check.passed)
                self.assertIn(f"(CON 12 {penalty:+d} injured): fails", check.line)

    def test_a_player_may_not_state_the_dice(self) -> None:
        with self.assertRaisesMessage(CharacterChangeError, "Only a Game Master"):
            attribute_check(self.character, "constitution", stated_total=3)

    def test_a_game_masters_total_is_held_to_what_3d6_can_make(self) -> None:
        stated = attribute_check(
            self.character, "constitution", stated_total=13, is_game_master=True
        )
        self.assertEqual((stated.dice_total, stated.passed), (13, False))
        self.assertIn("3d6 stated by a Game Master = 13 > 12", stated.line)
        for impossible in (2, 19):
            with (
                self.subTest(total=impossible),
                self.assertRaisesMessage(CharacterChangeError, "between 3 and 18"),
            ):
                attribute_check(
                    self.character,
                    "constitution",
                    stated_total=impossible,
                    is_game_master=True,
                )

    def test_an_unknown_or_unrecorded_attribute_is_refused(self) -> None:
        with self.assertRaisesMessage(CharacterChangeError, "Unknown attribute"):
            attribute_check(self.character, "luck")
        self.character.constitution = None
        with self.assertRaisesMessage(CharacterChangeError, "not recorded yet"):
            attribute_check(self.character, "constitution")

    def test_the_dead_roll_nothing_for_anyone(self) -> None:
        self.character.is_alive = False
        for is_game_master in (False, True):
            with (
                self.subTest(is_game_master=is_game_master),
                self.assertRaisesMessage(CharacterChangeError, "dead"),
            ):
                attribute_check(
                    self.character, "constitution", is_game_master=is_game_master
                )

    def test_only_a_game_master_calls_an_unconscious_characters_check(self) -> None:
        hurt_to(self.character, fatigue_left=0)
        with self.assertRaisesMessage(CharacterChangeError, "unconscious"):
            self.check(1, 1, 1)
        called = self.check(1, 1, 1, is_game_master=True)
        self.assertTrue(called.line.endswith(game_master_call_note("unconscious")))


class AGameMastersModifierTest(BaseTestCase):
    """A Game Master's situational modifier on the target (#953).

    Spencer's ruling on #911: a situational modifier on the target, separate
    from a survival save's penalty, the Game Master's alone, applied after
    the injury penalty and named on the line as his.
    """

    def setUp(self) -> None:
        super().setUp()
        self.character = create_character(
            self.create_user(), name="Dain", constitution=12
        )

    def check(self, *faces: int, **options) -> AttributeCheck:
        return attribute_check(
            self.character,
            "constitution",
            roller=FacesRoller(*faces),
            **options,
        )

    def test_a_negative_modifier_lowers_the_target(self) -> None:
        check = self.check(3, 3, 3, modifier=-5, is_game_master=True)
        self.assertEqual((check.modifier, check.target, check.passed), (-5, 7, False))
        self.assertEqual(
            check.line,
            "constitution check, 3d6: [3, 3, 3] = 9 > 7 "
            "(CON 12 -5 modifier set by a Game Master): fails",
        )

    def test_a_positive_modifier_raises_the_target(self) -> None:
        check = self.check(5, 5, 4, modifier=3, is_game_master=True)
        self.assertEqual((check.target, check.passed), (15, True))
        self.assertIn(
            "= 14 ≤ 15 (CON 12 +3 modifier set by a Game Master): passes", check.line
        )

    def test_the_modifier_comes_after_the_injury_penalty(self) -> None:
        hurt_to(self.character, fatigue_left=5)
        check = self.check(4, 4, 1, modifier=-2, is_game_master=True)
        self.assertEqual((check.injury_penalty, check.target), (-2, 8))
        self.assertIn(
            "= 9 > 8 (CON 12 -2 injured -2 modifier set by a Game Master): fails",
            check.line,
        )

    def test_no_modifier_leaves_the_line_as_it_was(self) -> None:
        check = self.check(4, 4, 4, modifier=0, is_game_master=True)
        self.assertNotIn("modifier", check.line)

    def test_a_player_may_not_set_a_modifier(self) -> None:
        for modifier in (-5, 5):
            with (
                self.subTest(modifier=modifier),
                self.assertRaisesMessage(
                    CharacterChangeError,
                    "Only a Game Master can set a modifier or state the dice",
                ),
            ):
                self.check(4, 4, 4, modifier=modifier)

    def test_the_modifier_is_held_to_its_bound(self) -> None:
        limit = skill_rules().attribute_check.game_master_modifier_limit
        for edge in (-limit, limit):
            with self.subTest(modifier=edge):
                check = self.check(4, 4, 4, modifier=edge, is_game_master=True)
                self.assertEqual(check.target, 12 + edge)
        for past in (-limit - 1, limit + 1):
            with (
                self.subTest(modifier=past),
                self.assertRaisesMessage(
                    CharacterChangeError,
                    f"modifier must be a whole number from -{limit} to {limit}",
                ),
            ):
                self.check(4, 4, 4, modifier=past, is_game_master=True)
