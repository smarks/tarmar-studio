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

from characters.skills import (
    SURVIVAL_AUTO_DEATH_ROLL,
    SURVIVAL_AUTO_SURVIVE_ROLL,
    SURVIVAL_TARGETS,
)
from gm import rules_lookup
from gm.engine import SECTION_PLAN_SCHEMA


class LookUpTest(SimpleTestCase):
    def test_every_subject_the_schema_offers_can_be_looked_up(self):
        """The model is told what it may ask for; all of it must answer."""
        for subject in rules_lookup.subjects():
            with self.subTest(subject=subject):
                self.assertTrue(rules_lookup.look_up(subject))
                self.assertIn(subject, SECTION_PLAN_SCHEMA)

    def test_a_track_gets_its_own_target_from_the_rules(self):
        for track, target in SURVIVAL_TARGETS.items():
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

        self.assertIn(str(SURVIVAL_AUTO_SURVIVE_ROLL), said)
        self.assertIn(str(SURVIVAL_AUTO_DEATH_ROLL), said)
        self.assertIn("never a death", said)

    def test_the_answer_is_built_from_the_rules_not_typed_here(self):
        """A target changed in the rules changes the answer, with no edit."""
        track, target = next(iter(SURVIVAL_TARGETS.items()))
        with self.settings():
            SURVIVAL_TARGETS[track] = 99
            try:
                self.assertIn("99", rules_lookup.look_up("survival", track))
            finally:
                SURVIVAL_TARGETS[track] = target

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
        for target in SURVIVAL_TARGETS.values():
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
        from characters.skill_checks import NATURAL_FAILURE, NATURAL_SUCCESS

        said = rules_lookup.look_up("skill check")

        self.assertIn("2d6", said)
        self.assertIn(f"natural {NATURAL_SUCCESS} always succeeds", said)
        self.assertIn(f"natural {NATURAL_FAILURE} always fails", said)

    def test_a_named_skill_gets_its_governing_attribute(self):
        from characters.skill_checks import GOVERNING_ATTRIBUTES

        said = rules_lookup.look_up("skill_check", "Stealth")

        governing = GOVERNING_ATTRIBUTES["stealth"]
        assert governing is not None
        self.assertIn(f"Stealth rides on {governing.title()}", said)

    def test_an_unnamed_skill_gets_the_whole_governs_column(self):
        from characters.skill_checks import GOVERNING_ATTRIBUTES

        said = rules_lookup.look_up("skill_check")

        for skill, attribute in GOVERNING_ATTRIBUTES.items():
            if attribute is not None:
                with self.subTest(skill=skill):
                    self.assertIn(f"{skill} {attribute}", said)
