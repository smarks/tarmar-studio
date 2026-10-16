"""Reading a rule out of the code so the GM may state it (issue #605).

#370 was the model inventing a survival target — "the campaigner track
requires a survival roll of 3d6 <= 12" — and calling a natural 18 a death,
when the rules make it an automatic survival. The prohibition that followed
told the GM never to state a rules number it was not given, and it works.

What it cost was the ordinary case: asked whether the campaigner check is
"12 or under", the GM declined to say anything, so the player kept a rule
that is backwards. These tests are about the answer coming from the code
instead, which is what the prohibition asks for.
"""

from django.test import SimpleTestCase

from characters.skill_rules import skill_rules
from characters.skills import survival_targets
from characters.tests.rules_fixtures import seated_skill_rules
from characters.tests.test_skill_rules import INVENTED_SKILL_RULES
from gm import rules_lookup
from gm.engine import section_plan_schema


class LookUpTest(SimpleTestCase):
    def test_every_subject_the_schema_offers_can_be_looked_up(self):
        """The model is told what it may ask for; all of it must answer."""
        for subject in rules_lookup.subjects():
            with self.subTest(subject=subject):
                self.assertTrue(rules_lookup.look_up(subject))
                self.assertIn(subject, section_plan_schema())

    def test_a_track_gets_its_own_target_from_the_rules(self):
        for track, target in survival_targets().items():
            with self.subTest(track=track):
                said = rules_lookup.look_up("survival", track)
                self.assertIn(str(target), said)
                self.assertIn(track, said)

    def test_the_answer_says_which_way_the_roll_goes(self):
        """#370 was a rule stated backwards; the direction is the point."""
        said = rules_lookup.look_up("survival", "campaigner").casefold()

        self.assertIn("high", said)
        self.assertIn("or more", said)
        self.assertNotIn("or under", said)
        self.assertNotIn("or less", said)

    def test_the_answer_carries_the_natural_roll_overrides(self):
        said = rules_lookup.look_up("survival")

        survival_roll = skill_rules().survival_roll
        self.assertIn(str(survival_roll.automatic_survival_total), said)
        self.assertIn(str(survival_roll.automatic_death_total), said)
        self.assertIn("never a death", said)

    def test_the_answer_is_built_from_the_rules_not_typed_here(self):
        """A target changed in the rules changes the answer, with no edit."""
        with seated_skill_rules(INVENTED_SKILL_RULES):
            said = rules_lookup.look_up("survival", "smith")
        self.assertIn("target is 25, so on 2d10", said)
        self.assertIn("A natural 20 is never a death and a natural 2", said)

    def test_an_unknown_subject_says_what_is_known(self):
        with self.assertRaises(rules_lookup.UnknownRule) as refused:
            rules_lookup.look_up("the price of fish")

        for subject in rules_lookup.subjects():
            self.assertIn(subject, str(refused.exception))

    def test_an_unknown_track_still_answers_with_every_target(self):
        """A lookup always answers (issue #605).

        The model puts the question's own words in the narrowing field --
        "base TN Piercing vs None" arrived as a survival track once -- and a
        refusal left the GM narrating an apology at the player instead of the
        rule. The general answer carries every target, so it answers anyway.
        """
        said = rules_lookup.look_up("survival", "wizarding")

        self.assertIn("campaigner", said)
        for target in survival_targets().values():
            self.assertIn(str(target), said)

    def test_an_unknown_weapon_class_still_answers_with_the_whole_matrix(self):
        said = rules_lookup.look_up("combat", "base TN Piercing vs None")

        self.assertIn("Piercing", said)
        self.assertIn("11", said)

    def test_an_unknown_subject_is_still_refused(self):
        """A subject that names no rule is a real mismatch, not a phrasing."""
        with self.assertRaises(rules_lookup.UnknownRule):
            rules_lookup.look_up("the price of fish")

    def test_asking_it_for_a_character_figure_says_where_that_lives(self):
        """It reached for this to answer "how many skill points?" (#614).

        Skill points are a record and are already in the character list the
        turn is given, so the refusal has to say so or the GM is left with
        nothing to state.
        """
        with self.assertRaises(rules_lookup.UnknownRule) as refused:
            rules_lookup.look_up("skill_points", "laborer")

        said = str(refused.exception)
        self.assertIn("is a record, not a rule", said)
        self.assertIn("character list", said)

    def test_a_subject_is_read_however_it_is_spelled(self):
        for spelling in ("Survival", " survival ", "survival-targets"):
            with self.subTest(spelling=spelling):
                self.assertTrue(rules_lookup.look_up(spelling))

    def test_the_combat_answer_comes_from_the_matrix(self):
        said = rules_lookup.look_up("combat")

        self.assertIn("d20", said)
        self.assertIn("target number", said.casefold())


class SkillCheckRuleTest(SimpleTestCase):
    """The skill check is a subject the Games Master can look up (#843)."""

    def test_the_mechanic_comes_from_the_check_itself(self):
        checks = skill_rules().skill_check

        said = rules_lookup.look_up("skill check")

        self.assertIn(f"roll {checks.dice}, add", said)
        self.assertIn(f"natural {checks.natural_success} always succeeds", said)
        self.assertIn(f"natural {checks.natural_failure} always fails", said)

    def test_a_named_skill_gets_its_governing_attribute(self):
        said = rules_lookup.look_up("skill_check", "Stealth")

        governing = skill_rules().skill_check.governing_attributes["stealth"]
        assert governing is not None
        self.assertIn(f"Stealth rides on {governing.title()}", said)

    def test_an_unnamed_skill_gets_the_whole_governs_column(self):
        said = rules_lookup.look_up("skill_check")

        for skill, attribute in skill_rules().skill_check.governing_attributes.items():
            if attribute is not None:
                with self.subTest(skill=skill):
                    self.assertIn(f"{skill} {attribute}", said)


class AttributeCheckRuleTest(SimpleTestCase):
    """The attribute check is a subject the Games Master can look up (#911)."""

    def test_the_answer_says_which_way_the_roll_goes(self):
        said = rules_lookup.look_up("attribute check")

        self.assertIn("3d6", said)
        self.assertIn("at or under the attribute's aged score", said)

    def test_the_injury_bands_lower_the_target_as_the_action_does(self):
        from characters.constants import (
            MINOR_INJURY_ROLL_PENALTY,
            SEVERE_INJURY_BAND_TOP,
            SEVERE_INJURY_ROLL_PENALTY,
        )

        said = rules_lookup.look_up("attribute_check")

        self.assertIn("is lowered", said)
        self.assertIn(
            f"{rules_lookup.signed(MINOR_INJURY_ROLL_PENALTY)} with a pool down "
            f"to half (but above {SEVERE_INJURY_BAND_TOP})",
            said,
        )
        self.assertIn(
            f"{rules_lookup.signed(SEVERE_INJURY_ROLL_PENALTY)} with a pool at 1 "
            f"to {SEVERE_INJURY_BAND_TOP}",
            said,
        )

    def test_a_game_master_may_state_a_total_3d6_can_make(self):
        check = skill_rules().attribute_check

        said = rules_lookup.look_up("attribute_check")

        self.assertIn(
            f"Game Master may state a total ({check.minimum} to {check.maximum})",
            said,
        )
