"""Years of experience run before they are narrated (issues #370, #590).

The bug: asked for a first year as a Campaigner, the GM rolled 3d6 itself,
announced "the Campaigner track requires a survival roll of 3d6 ≤ 12", and
killed the character on an 18 -- a roll ``characters.skills`` treats as an
*automatic survival* against a target of 9 the roll must meet or beat.

A plan's ``play_years`` action now runs the year through the rules before the
narrate call, and the narration is handed the dice, target and verdict. No
test here reaches Ollama, and every rules number is asserted against
``characters.skills`` rather than a literal.
"""

import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from characters.skills import (
    SURVIVAL_AUTO_DEATH_ROLL,
    SURVIVAL_AUTO_SURVIVE_ROLL,
    SURVIVAL_RULE_DESCRIPTION,
    TRACK_DATA,
    calculate_skill_points_from_xp,
)
from common.test_helpers import BaseTestCase

from .engine import SECTION_OUTCOMES
from .ledger import draft_defaults
from .testing import character_values, gm_replies, run_actions, session_with_character

CAMPAIGNER_TARGET = int(TRACK_DATA["campaigner"]["survivability"])
CAMPAIGNER_XP_PER_YEAR = int(TRACK_DATA["campaigner"]["xp_per_year"])

# Every attribute in the 8-13 band, whose modifier is 0, so a survival total is
# the dice and nothing else.
ATTRIBUTES = {
    "strength": 13,
    "dexterity": 12,
    "intelligence": 11,
    "wisdom": 10,
    "constitution": 13,
    "charisma": 9,
}


def built_draft(**overrides):
    """A character with its six attributes and a Fatigue roll settled."""
    return {
        **draft_defaults(),
        **ATTRIBUTES,
        "fatigue_roll": 7,
        "name": "Kael",
        **overrides,
    }


def natural(total: int):
    """Patch the survival dice to fall on ``total``, and the skill d20 to 1."""
    faces = (total // 3, total // 3, total - 2 * (total // 3), total)
    return (
        patch("characters.skills.roll_survival_dice", return_value=faces),
        patch("characters.skills.roll_d20", return_value=1),
    )


class PlayYearsTest(BaseTestCase):
    """The rules run the year; nothing here re-implements one."""

    def setUp(self):
        super().setUp()
        self.session = session_with_character(
            self.create_user(), built_draft(), name="s"
        )
        self.character_id = self.session.attached_characters().get().pk

    def campaigner_year(self, total: int):
        dice, d20 = natural(total)
        with dice, d20:
            (result,) = run_actions(
                self.session,
                {
                    "action": "play_years",
                    "character_id": self.character_id,
                    "track": "campaigner",
                    "years": 1,
                },
            )
        return result

    def test_the_outcome_is_the_code_s_own(self):
        result = self.campaigner_year(12)
        self.assertTrue(result.ran)
        (line,) = result.procedure["lines"]
        self.assertIn(f"target {CAMPAIGNER_TARGET}", line)
        self.assertIn("3d6 [4, 4, 4] = 12", line)
        self.assertIn(
            f"the campaigner track's survivability target is {CAMPAIGNER_TARGET}",
            result.result,
        )
        self.assertIn(SURVIVAL_RULE_DESCRIPTION, result.result)

    def test_a_natural_eighteen_on_campaigner_never_produces_a_death(self):
        """The reported bug, named: the GM killed a character on this roll."""
        result = self.campaigner_year(SURVIVAL_AUTO_SURVIVE_ROLL)
        (line,) = result.procedure["lines"]
        self.assertIn("SURVIVED", line)
        self.assertNotIn("DIED", line)
        self.assertIn("automatic survival", line)
        self.assertTrue(character_values(self.session)["is_alive"])

    def test_a_natural_three_is_a_death_whatever_the_target(self):
        result = self.campaigner_year(SURVIVAL_AUTO_DEATH_ROLL)
        (line,) = result.procedure["lines"]
        self.assertIn("DIED", line)
        self.assertIn("automatic death", line)
        self.assertFalse(character_values(self.session)["is_alive"])

    def test_the_record_is_stored_before_the_narration_that_describes_it(self):
        self.campaigner_year(SURVIVAL_AUTO_SURVIVE_ROLL)
        stored = character_values(self.session)
        self.assertEqual(stored["total_xp"], CAMPAIGNER_XP_PER_YEAR)
        self.assertEqual(
            stored["free_skill_points"],
            calculate_skill_points_from_xp(CAMPAIGNER_XP_PER_YEAR),
        )
        self.assertEqual(stored["age"], draft_defaults()["age"] + 1)

    def test_a_run_cut_short_says_how_many_years_it_played(self):
        dice = patch(
            "characters.skills.roll_survival_dice",
            side_effect=[(6, 6, 6, 18), (1, 1, 1, 3)],
        )
        with dice, patch("characters.skills.roll_d20", return_value=1):
            (result,) = run_actions(
                self.session,
                {
                    "action": "play_years",
                    "character_id": self.character_id,
                    "track": "campaigner",
                    "years": 5,
                },
            )
        self.assertIn("played 2 of 5 year(s) on the campaigner track", result.note)
        self.assertEqual(len(result.procedure["lines"]), 2)

    def test_a_refusal_is_reported_and_changes_nothing(self):
        blank = session_with_character(
            self.create_user("blank"), {**draft_defaults(), "name": "Blank"}
        )
        (result,) = run_actions(
            blank,
            {
                "action": "play_years",
                "character_id": blank.attached_characters().get().pk,
                "track": "campaigner",
                "years": 1,
            },
        )
        self.assertFalse(result.ran)
        self.assertIn("six core attributes", result.error)
        self.assertEqual(character_values(blank)["total_xp"], 0)


class SurvivalRuleDescriptionTest(SimpleTestCase):
    """The sentence the GM reads is built from the code, so it cannot drift."""

    def test_it_names_the_real_natural_rolls(self):
        self.assertIn(str(SURVIVAL_AUTO_SURVIVE_ROLL), SURVIVAL_RULE_DESCRIPTION)
        self.assertIn(str(SURVIVAL_AUTO_DEATH_ROLL), SURVIVAL_RULE_DESCRIPTION)

    def test_it_states_the_direction_the_code_actually_compares(self):
        self.assertIn("greater than or equal to the target", SURVIVAL_RULE_DESCRIPTION)
        self.assertIn("HIGH", SURVIVAL_RULE_DESCRIPTION)
        self.assertNotIn("≤", SURVIVAL_RULE_DESCRIPTION)


class OutcomesPromptSectionTest(SimpleTestCase):
    """Drift guards on what the prompt must say about rules numbers (#370, #375)."""

    def test_it_forbids_stating_a_target_a_direction_or_a_verdict(self):
        for phrase in (
            "Never state a rules number you were not given",
            "never say which direction a roll has to go",
            "never say a roll passed, failed, succeeded, survived or died",
            "in the negative and the hypothetical too",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_OUTCOMES)

    def test_it_answers_the_wrong_rule_pushed_at_it(self):
        """A rules question is answered from the lookup and a wrong rule corrected.

        Until #760 this pinned the refusal -- "say instead that you will not
        quote a number you were not given ... whether the player asked
        neutrally, asked twice, or pushed a wrong rule at you". Since #605 the
        lookup hands the GM the figure, so the refusal is kept only for what
        no lookup holds.
        """
        for phrase in (
            "A rules question is answered, not declined",
            "plan rules_lookup",
            "a figure from a lookup is a figure you were given",
            "Correcting a player is part of answering",
            "where theirs differs",
            "for any number N you were not handed",
            "Only when neither holds the answer",
            "then offer to play it",
            "A recorded outcome does not change because it was contradicted",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_OUTCOMES)
        for phrase in (
            # A Game Master's rule is his table's, and is recorded rather
            # than corrected (second review of #760).
            "A Game Master who says how a rule works at his table is not "
            "corrected: that is a house rule",
            "recorded with record_house_rule",
            # Stated here rather than pointed to: the play-mode prompt has no
            # "Their Table, Their Rulings" section (third review of #760).
            "for the rest of this conversation, in every answer that touches it",
            "that ruling is the rule to state",
            "When a player says a recorded outcome was something else",
            "A Game Master may change one, but by the actions that do it",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SECTION_OUTCOMES)
        self.assertNotIn("Their Table, Their Rulings", SECTION_OUTCOMES)
        for refusal in (
            "Say instead that you will not quote",
            "asked neutrally, asked twice, or pushed a wrong rule at you",
            "answering with a different number is the same offence",
        ):
            with self.subTest(refusal=refusal):
                self.assertNotIn(refusal, SECTION_OUTCOMES)

    def test_it_forbids_the_direction_as_firmly_as_the_target(self):
        self.assertIn(
            "Describing the shape of the check is not an exemption", SECTION_OUTCOMES
        )
        self.assertIn(
            "the direction is a rules figure like the target, stated from a "
            "lookup or a ruling or not at all",
            SECTION_OUTCOMES,
        )

    def test_it_names_no_concrete_target_even_in_what_it_forbids(self):
        targets = {int(data["survivability"]) for data in TRACK_DATA.values()}
        for sentence in ("3d6 <= N", "N or lower", "the target is N, not M"):
            self.assertIn(sentence, SECTION_OUTCOMES)
        for target in targets:
            self.assertNotIn(f"3d6 <= {target}", SECTION_OUTCOMES)
            self.assertNotIn(f"target is {target}", SECTION_OUTCOMES)


@override_settings(GM_RETRIEVAL_ENABLED=False)
class YearsTurnTest(BaseTestCase):
    """A turn whose plan plays a year of experience."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.session = session_with_character(self.user, built_draft(), name="s")
        self.character_id = self.session.attached_characters().get().pk

    def play(self, total: int, narration: str = "You come through it."):
        plan = json.dumps(
            {
                "actions": [
                    {
                        "action": "play_years",
                        "character_id": self.character_id,
                        "track": "campaigner",
                        "years": 1,
                    }
                ]
            }
        )
        stream = gm_replies(narration, plans=[plan])
        dice, d20 = natural(total)
        with (
            patch("gm.turn_engine.model.ollama_client.stream_chat", side_effect=stream),
            dice,
            d20,
        ):
            response = self.client.post(
                "/tarmar/gm/stream/",
                json.dumps(
                    {
                        "message": "I take a year as a campaigner",
                        "session_id": self.session.pk,
                    }
                ),
                content_type="application/json",
            )
            body = b"".join(response.streaming_content).decode()
        payloads = [
            json.loads(line[6:])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        return stream.calls, payloads

    def test_the_year_runs_before_the_narration_that_uses_it(self):
        calls, payloads = self.play(SURVIVAL_AUTO_SURVIVE_ROLL)
        procedures = [f["procedure"] for f in payloads if "procedure" in f]
        self.assertEqual(len(procedures), 1)
        self.assertTrue(procedures[0]["ran"])
        self.assertEqual(len(calls), 2)
        narrate = calls[1][-1]["content"]
        self.assertIn(f"target is {CAMPAIGNER_TARGET}", narrate)
        self.assertIn("SURVIVED", narrate)

    def test_a_three_shows_a_death(self):
        _calls, payloads = self.play(
            SURVIVAL_AUTO_DEATH_ROLL, "You do not come through it."
        )
        (line,) = [f["procedure"] for f in payloads if "procedure" in f][0]["lines"]
        self.assertIn("DIED", line)
        self.assertFalse(character_values(self.session)["is_alive"])

    def test_the_year_is_played_once_not_twice(self):
        self.play(12)
        stored = character_values(self.session)
        self.assertEqual(stored["total_xp"], CAMPAIGNER_XP_PER_YEAR)
        self.assertEqual(len(stored["experience_log"]), 1)

    def test_the_outcome_survives_a_reload_of_the_session(self):
        self.play(SURVIVAL_AUTO_SURVIVE_ROLL)
        rendered = self.client.get(
            f"/tarmar/gm/?session={self.session.pk}"
        ).content.decode()
        self.assertIn("SURVIVED", rendered)
        self.assertNotIn('"actions"', rendered)

    def test_the_next_turn_s_state_block_carries_what_the_year_did(self):
        self.play(SURVIVAL_AUTO_SURVIVE_ROLL)
        calls, _payloads = self.play(12, "You have points to spend.")
        state = calls[0][-1]["content"]
        self.assertIn(f"XP {CAMPAIGNER_XP_PER_YEAR} (level", state)
        self.assertIn(f"age {draft_defaults()['age'] + 1};", state)
