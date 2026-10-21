"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

import json
import time

from django.test import TestCase

from characters.models import Character
from characters.refusal_words import refusal_words
from characters.rules import character_rules
from common.test_helpers import BaseTestCase


class SkillsFunctionsTest(TestCase):
    """Tests for skills.py functions."""

    def test_points_for_level(self):
        """points_for_level returns correct triangular numbers."""
        from characters.skills import points_for_level

        self.assertEqual(points_for_level(0), 0)
        self.assertEqual(points_for_level(1), 1)
        self.assertEqual(points_for_level(2), 3)
        self.assertEqual(points_for_level(3), 6)
        self.assertEqual(points_for_level(4), 10)
        self.assertEqual(points_for_level(5), 15)

    def test_skill_points_from_xp_follow_the_published_example(self):
        """prior-experience.md: "at 3,000 XP you would have earned 2 skill
        points (at 1,000 and 2,000 XP)"."""
        from characters.skills import calculate_skill_points_from_xp

        self.assertEqual(calculate_skill_points_from_xp(0), 0)
        self.assertEqual(calculate_skill_points_from_xp(1_000), 1)
        self.assertEqual(calculate_skill_points_from_xp(3_000), 2)
        self.assertEqual(calculate_skill_points_from_xp(512_000), 25)

    def test_skill_points_keep_accruing_past_the_tables_last_row(self):
        """Level 10's row is a rate, not a terminus (issue #656).

        The published table stops at 512,000 XP with "Skill Points Every
        51,200", and the page says points are granted cumulatively as XP is
        earned without naming a stop. Every total at or above 512,000 used to
        yield exactly 25 points, with no explanation anywhere, while total_xp
        stayed editable up to 2,147,483,647 for a Game Master's grants.
        """
        from characters.skills import calculate_skill_points_from_xp

        # One more 51,200 past the top of the table is one more point.
        self.assertEqual(calculate_skill_points_from_xp(512_000 + 51_200), 26)
        self.assertEqual(calculate_skill_points_from_xp(512_000 + 102_400), 27)
        # 10,000,000 XP: 25 by the table, plus (10,000,000 - 512,000) // 51,200.
        self.assertEqual(calculate_skill_points_from_xp(10_000_000), 210)

    def test_the_rules_page_says_the_grant_does_not_stop_at_the_last_row(self):
        """A player reading the table must be told the rate keeps running."""
        from pathlib import Path

        page = Path(
            "reference/content/public-rules/characters/prior-experience.md"
        ).read_text()

        self.assertIn("past 512,000 XP the level-10 rate keeps running", page)

    def test_points_for_level_is_uncapped(self):
        """Levels past V keep costing N more points each: no ceiling."""
        from characters.skills import points_for_level

        self.assertEqual(points_for_level(6), 21)
        self.assertEqual(points_for_level(7), 28)
        self.assertEqual(points_for_level(10), 55)
        # Each level costs exactly its own number in additional points.
        for level in range(1, 30):
            with self.subTest(level=level):
                self.assertEqual(
                    points_for_level(level) - points_for_level(level - 1), level
                )

    def test_get_skill_level(self):
        """get_skill_level returns correct level from points."""
        from characters.skills import get_skill_level

        self.assertEqual(get_skill_level(0), 0)
        self.assertEqual(get_skill_level(1), 1)
        self.assertEqual(get_skill_level(2), 1)
        self.assertEqual(get_skill_level(3), 2)
        self.assertEqual(get_skill_level(5), 2)
        self.assertEqual(get_skill_level(6), 3)
        self.assertEqual(get_skill_level(10), 4)
        self.assertEqual(get_skill_level(15), 5)
        self.assertEqual(get_skill_level(20), 5)

    def test_get_skill_level_is_uncapped(self):
        """Above 15 points the ladder keeps going — it is not clamped at V."""
        from characters.skills import get_skill_level, points_for_level

        self.assertEqual(get_skill_level(21), 6)
        self.assertEqual(get_skill_level(27), 6)
        self.assertEqual(get_skill_level(28), 7)
        self.assertEqual(get_skill_level(55), 10)
        # get_skill_level inverts points_for_level exactly, at every level.
        for level in range(1, 40):
            cost = points_for_level(level)
            with self.subTest(level=level):
                self.assertEqual(get_skill_level(cost), level)
                self.assertEqual(get_skill_level(cost - 1), level - 1)

    def test_get_excess_points(self):
        """get_excess_points returns points beyond current level."""
        from characters.skills import get_excess_points

        self.assertEqual(get_excess_points(0), 0)
        self.assertEqual(get_excess_points(1), 0)
        self.assertEqual(get_excess_points(2), 1)
        self.assertEqual(get_excess_points(3), 0)
        self.assertEqual(get_excess_points(5), 2)
        self.assertEqual(get_excess_points(6), 0)

    def test_format_skill_level_renders_the_level_only(self):
        """The edit screen's "Level ..." label gets the numeral and any excess."""
        from characters.skills import format_skill_level

        self.assertEqual(format_skill_level(0, "sword"), "0")
        self.assertEqual(format_skill_level(1, "sword"), "I")
        self.assertEqual(format_skill_level(2, "sword"), "I (+1)")
        self.assertEqual(format_skill_level(3, "sword"), "II")
        self.assertEqual(format_skill_level(5, "sword"), "II (+2)")
        self.assertEqual(format_skill_level(15, "sword"), "V")
        # Uncapped levels keep getting their own numeral.
        self.assertEqual(format_skill_level(21, "sword"), "VI")
        self.assertEqual(format_skill_level(23, "sword"), "VI (+2)")
        self.assertEqual(format_skill_level(55, "sword"), "X")
        # Arcane costs double, so 21 points is level IV with 1 point spare.
        self.assertEqual(format_skill_level(1, "arcane"), "0 (+1)")
        self.assertEqual(format_skill_level(2, "arcane"), "I")
        self.assertEqual(format_skill_level(21, "arcane"), "IV (+1)")

    def test_arcane_costs_double_at_every_level(self):
        """Issue #154: reaching level N in Arcane costs N(N+1), not N(N+1)/2."""
        from characters.skills import get_skill_level, points_for_level

        for level, cost in ((1, 2), (2, 6), (3, 12), (4, 20), (5, 30), (6, 42)):
            with self.subTest(level=level):
                self.assertEqual(points_for_level(level, "Arcane"), cost)
                self.assertEqual(get_skill_level(cost, "Arcane"), level)
                # One point short of the cost is still the level below.
                self.assertEqual(get_skill_level(cost - 1, "Arcane"), level - 1)

    def test_arcane_level_is_the_ordinary_ladder_at_half_the_points(self):
        """The doubled schedule is exactly the triangular one at points // 2."""
        from characters.skills import get_skill_level

        for points in range(0, 120):
            with self.subTest(points=points):
                self.assertEqual(
                    get_skill_level(points, "arcane"), get_skill_level(points // 2)
                )

    def test_arcane_is_matched_by_normalized_name(self):
        """Case, whitespace, and a trailing numeral all still name Arcane."""
        from characters.skills import (
            ARCANE_SKILL_NAME,
            get_skill_level,
            normalize_skill_name,
            skill_cost_multiplier,
        )

        self.assertEqual(normalize_skill_name("Arcane"), ARCANE_SKILL_NAME)
        for spelling in ("Arcane", "ARCANE", "  arcane  ", "Arcane III"):
            with self.subTest(spelling=spelling):
                self.assertEqual(skill_cost_multiplier(spelling), 2)
                self.assertEqual(get_skill_level(6, spelling), 2)

    def test_only_arcane_costs_double(self):
        """A different skill that merely contains the word pays ordinary cost."""
        from characters.skills import get_skill_level, points_for_level

        for ordinary in ("sword", "arcane lore", "lesser arcane", None):
            with self.subTest(skill=ordinary):
                self.assertEqual(points_for_level(5, ordinary), 15)
                self.assertEqual(get_skill_level(15, ordinary), 5)
                self.assertEqual(get_skill_level(6, ordinary), 3)

    def test_normalize_skill_name(self):
        """normalize_skill_name handles various formats."""
        from characters.skills import normalize_skill_name

        self.assertEqual(normalize_skill_name("Sword"), "sword")
        self.assertEqual(normalize_skill_name("  Navigation  "), "navigation")
        self.assertEqual(normalize_skill_name("Shield Parry II"), "shield parry")
        self.assertEqual(normalize_skill_name("ACCOUNTING"), "accounting")
        self.assertEqual(normalize_skill_name("Sword III"), "sword")
        # Test parenthetical modifiers - extracts "to hit" suffix
        self.assertEqual(normalize_skill_name("Sword (+1 to hit)"), "sword to hit")

    def test_calculate_attribute_modifier_matches_rules_table(self):
        """The modifier must match the published attributes.md table exactly."""
        from characters.skills import calculate_attribute_modifier

        # The full 3-18 table from reference/content/.../attributes.md.
        rules_table = {
            3: -5,
            4: -4,
            5: -3,
            6: -2,
            7: -1,
            8: 0,
            9: 0,
            10: 0,
            11: 0,
            12: 0,
            13: 0,
            14: 1,
            15: 2,
            16: 3,
            17: 4,
            18: 5,
        }
        for value, expected in rules_table.items():
            self.assertEqual(calculate_attribute_modifier(value), expected, value)

    def test_calculate_attribute_modifier_stops_where_the_table_stops(self):
        """Past 18 the modifier is the table's last row (issue #806).

        This test used to pin 19 -> +6 and 30 -> +17, a slope the rules do
        not state.
        """
        from characters.skills import calculate_attribute_modifier

        self.assertEqual(calculate_attribute_modifier(19), 5)
        self.assertEqual(calculate_attribute_modifier(30), 5)

    def test_get_best_modifier(self):
        """get_best_modifier returns highest modifier."""
        from characters.skills import get_best_modifier

        # All 10s = modifier 0
        self.assertEqual(get_best_modifier(10, 10, 10, 10, 10, 10), 0)
        # One 14 = modifier +1
        self.assertEqual(get_best_modifier(14, 10, 10, 10, 10, 10), 1)
        # Best of several: 16 -> +3
        self.assertEqual(get_best_modifier(14, 16, 12, 10, 10, 10), 3)

    def test_get_skill_for_year(self):
        """get_skill_for_year returns correct skill from track table."""
        from characters.skills import get_skill_for_year

        # Year 1 of merchant is Weapon
        self.assertEqual(get_skill_for_year("merchant", 1), "Weapon")
        # Positions past the 20-entry table clamp to the last skill (defensive).
        self.assertEqual(get_skill_for_year("merchant", 20), "Alert")
        self.assertEqual(get_skill_for_year("merchant", 21), "Alert")
        self.assertEqual(get_skill_for_year("merchant", 25), "Alert")
        # A track the canon does not have is refused: it used to fall back to
        # Laborer, a track a canon need not carry (issue #70).
        with self.assertRaisesMessage(ValueError, "Unknown track: invalid_track"):
            get_skill_for_year("invalid_track", 1)

    def test_get_random_track(self):
        """get_random_track returns valid track."""
        from characters.skills import get_random_track, track_keys

        track = get_random_track()
        self.assertIn(track, track_keys())

    def test_simulate_experience_years(self):
        """simulate_experience_years returns valid term result."""
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        # Mock survival to always succeed so all 4 years complete
        with patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)):
            result = simulate_experience_years(
                years_requested=4,
                years_completed=0,
                current_age=16,
                track="merchant",
                strength=10,
                dexterity=10,
                intelligence=10,
                wisdom=10,
                constitution=10,
                charisma=10,
            )

        self.assertEqual(result["years_requested"], 4)
        self.assertEqual(result["track"], "merchant")
        self.assertEqual(len(result["years"]), 4)
        # Merchant track: 750 XP per year * 4 years = 3000 XP
        self.assertEqual(result["xp_awarded"], 3000)
        self.assertIn("money_earned", result)
        self.assertIn("survival_check", result)
        self.assertIn("success", result["survival_check"])
        self.assertIn("skills_gained", result)

    def test_simulate_experience_years_aging(self):
        """Aging lands when the run reaches the age that carries it (issue #221)."""
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        # Mock survival to always succeed
        with patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)):
            # Ages 29-32: still short of the first aging step at 36.
            result = simulate_experience_years(
                years_requested=4,
                years_completed=12,
                current_age=28,
                track="merchant",
                strength=10,
                dexterity=10,
                intelligence=10,
                wisdom=10,
                constitution=10,
                charisma=10,
            )
            self.assertIsNone(result["aging"])

            # Ages 33-36: the run reaches 36, the first aging step.
            result = simulate_experience_years(
                years_requested=4,
                years_completed=16,
                current_age=32,
                track="merchant",
                strength=10,
                dexterity=10,
                intelligence=10,
                wisdom=10,
                constitution=10,
                charisma=10,
            )
            self.assertIsNotNone(result["aging"])
            self.assertEqual(result["aging"]["strength"], -1)

    def test_simulate_experience_years_aging_escalates_past_the_table(self):
        """Aging keeps escalating by formula past the aging table's last age."""
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        with patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)):
            result = simulate_experience_years(
                years_requested=4,
                years_completed=56,
                current_age=72,
                track="merchant",
                strength=18,
                dexterity=18,
                intelligence=18,
                wisdom=18,
                constitution=18,
                charisma=18,
            )
            self.assertEqual(
                result["aging"],
                {
                    "strength": -4,
                    "dexterity": -4,
                    "intelligence": -2,
                    "wisdom": -2,
                    "constitution": -4,
                },
            )
            # Stored in the table's order, as a tabulated stage is (issue #70).
            self.assertEqual(
                list(result["aging"]),
                ["strength", "dexterity", "intelligence", "wisdom", "constitution"],
            )

    def test_get_aging_penalties_table_range_unchanged(self):
        """Terms 4-13 still come straight from the canon's table, unchanged."""
        from characters.skill_rules import skill_rules

        aging = skill_rules().aging
        self.assertIsNone(aging.penalties_for(4))  # term 4 predates aging start
        for row in aging.stages[1:]:
            self.assertEqual(aging.penalties_for(row.stage), row.penalties)

    def test_get_aging_penalties_escalates_uncapped_past_term_13(self):
        """Terms 14+ escalate by formula: -1 STR/DEX/CON per term, -1 INT/WIS
        every second term (15, 17, 19, ...), with no ceiling."""
        from characters.skill_rules import skill_rules

        expected = {
            13: {
                "strength": -2,
                "dexterity": -2,
                "intelligence": -1,
                "wisdom": -1,
                "constitution": -2,
            },
            14: {
                "strength": -3,
                "dexterity": -3,
                "intelligence": -1,
                "wisdom": -1,
                "constitution": -3,
            },
            15: {
                "strength": -4,
                "dexterity": -4,
                "intelligence": -2,
                "wisdom": -2,
                "constitution": -4,
            },
            16: {
                "strength": -5,
                "dexterity": -5,
                "intelligence": -2,
                "wisdom": -2,
                "constitution": -5,
            },
            17: {
                "strength": -6,
                "dexterity": -6,
                "intelligence": -3,
                "wisdom": -3,
                "constitution": -6,
            },
            19: {
                "strength": -8,
                "dexterity": -8,
                "intelligence": -4,
                "wisdom": -4,
                "constitution": -8,
            },
        }
        for term, penalties in expected.items():
            with self.subTest(term=term):
                self.assertEqual(skill_rules().aging.penalties_for(term), penalties)

    def test_simulate_experience_years_survival_failure(self):
        """simulate_experience_years can result in death."""
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        # Mock survival roll to fail (roll 2 vs target 4)
        with patch("characters.skills.roll_survival_dice", return_value=(1, 1, 1, 3)):
            result = simulate_experience_years(
                years_requested=4,
                years_completed=0,
                current_age=16,
                track="merchant",
                strength=10,
                dexterity=10,
                intelligence=10,
                wisdom=10,
                constitution=10,
                charisma=10,
            )
            self.assertFalse(result["alive"])
            self.assertFalse(result["survival_check"]["success"])

    def test_natural_3_auto_death_overrides_a_passing_threshold(self):
        """Triple ones kill even when modifier + target would otherwise pass."""
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        # laborer survivability target = 2; all-18 stats -> best_mod = +5, so the
        # normal check (3 + 5 = 8 >= 2) would PASS. Only auto_death kills here.
        with patch("characters.skills.roll_survival_dice", return_value=(1, 1, 1, 3)):
            result = simulate_experience_years(
                years_requested=4,
                years_completed=0,
                current_age=16,
                track="laborer",
                strength=18,
                dexterity=18,
                intelligence=18,
                wisdom=18,
                constitution=18,
                charisma=18,
            )
        check = result["survival_check"]
        self.assertTrue(check["auto_death"])
        self.assertGreaterEqual(check["total"], check["target"])  # would have passed
        self.assertFalse(check["success"])
        self.assertFalse(result["alive"])

    def test_natural_18_survives_a_target_the_ordinary_check_would_fail(self):
        """Triple sixes always survive, whatever the target (issue #223).

        The natural-3 test above manufactures a state where the ordinary
        check would pass and shows the override winning. This is the mirror,
        and it did not used to be: against the published targets (9 at the
        worst) an 18 clears every one of them on the arithmetic alone even at
        the worst modifier a character can carry, so the old test asserted an
        outcome nothing could make fail, and deleting the auto_success branch
        would have failed only the flag assertion (issue #693).

        So the track's target is stubbed past anything 18 plus a modifier can
        reach. Only the override survives it.
        """
        from dataclasses import replace
        from unittest.mock import patch

        from characters.skill_rules import skill_rules
        from characters.skills import simulate_experience_years
        from characters.tests.rules_fixtures import seated_skill_rules

        loaded = skill_rules()
        tracks = dict(loaded.tracks.tracks)
        tracks["campaigner"] = replace(tracks["campaigner"], survival_target=30)
        unsurvivable = replace(loaded, tracks=replace(loaded.tracks, tracks=tracks))
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)),
            seated_skill_rules(unsurvivable),
        ):
            result = simulate_experience_years(
                years_requested=4,
                years_completed=0,
                current_age=16,
                track="campaigner",
                strength=3,
                dexterity=3,
                intelligence=3,
                wisdom=3,
                constitution=3,
                charisma=3,
            )
        check = result["survival_check"]
        self.assertEqual(check["roll"], 18)
        self.assertEqual(check["modifier"], -5)
        # 18 - 5 = 13, well short of the stubbed target: the ordinary
        # comparison fails and only the natural 18 saves the character.
        self.assertEqual(check["target"], 30)
        self.assertLess(check["total"], check["target"])
        self.assertTrue(check["auto_success"])
        self.assertFalse(check["auto_death"])
        self.assertTrue(check["success"])
        self.assertTrue(result["alive"])

    def test_simulate_experience_years_money_floors_at_level_0(self):
        """A skill-less character still earns the track's base rate (x1)."""
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        # Laborer position 10 is "+1 STR" -- a special result that never adds
        # to any skill's points, so highest_skill_level stays 0 all term.
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)),
            patch("characters.skills.roll_d20", return_value=10),
        ):
            result = simulate_experience_years(
                years_requested=4,
                years_completed=0,
                current_age=16,
                track="laborer",
                strength=10,
                dexterity=10,
                intelligence=10,
                wisdom=10,
                constitution=10,
                charisma=10,
                starting_skills={},
            )

        # Laborer multiplier is 25; every year should earn 25 * 1, not 0.
        for year in result["years"]:
            self.assertEqual(year["money_earned"], 25)
        self.assertEqual(result["money_earned"], 100)

    def test_simulate_experience_years_recalculates_money_year_by_year(self):
        """A skill point gained mid-term raises pay for later years of it."""
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        # Laborer position 1 is "Weapon" every year, so points accumulate
        # 1, 2, 3, 4 across the term -> levels 1, 1, 2, 2 (level 2 needs 3
        # points). Money should rise once the level does, within the term.
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)),
            patch("characters.skills.roll_d20", return_value=1),
        ):
            result = simulate_experience_years(
                years_requested=4,
                years_completed=0,
                current_age=16,
                track="laborer",
                strength=10,
                dexterity=10,
                intelligence=10,
                wisdom=10,
                constitution=10,
                charisma=10,
                starting_skills={},
            )

        money_by_year = [year["money_earned"] for year in result["years"]]
        self.assertEqual(money_by_year, [25, 25, 50, 50])

    def test_simulate_experience_years_uncapped_skill_level_scales_money(self):
        """A skill level above 5 (uncapped, see #119) still multiplies pay."""
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        # 21 points is level 6 (points_for_level(6) == 21). Keep it constant
        # across the term via the "+1 STR" special (position 10) so only the
        # term-start snapshot is in play.
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)),
            patch("characters.skills.roll_d20", return_value=10),
        ):
            result = simulate_experience_years(
                years_requested=4,
                years_completed=0,
                current_age=16,
                track="laborer",
                strength=10,
                dexterity=10,
                intelligence=10,
                wisdom=10,
                constitution=10,
                charisma=10,
                starting_skills={"expertise": 21},
            )

        # Laborer multiplier is 25; level 6 must not be clamped to 5.
        for year in result["years"]:
            self.assertEqual(year["money_earned"], 25 * 6)

    def test_simulate_experience_years_pays_arcane_at_its_doubled_level(self):
        """Issue #154: 20 Arcane points is level 4, not the level 5 it would
        buy in any other skill — and pay follows the real level."""
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        # Position 10 on the laborer track is the "+1 STR" special, so no
        # skill points move and the starting snapshot drives every year's pay.
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)),
            patch("characters.skills.roll_d20", return_value=10),
        ):
            result = simulate_experience_years(
                years_requested=4,
                years_completed=0,
                current_age=16,
                track="laborer",
                strength=10,
                dexterity=10,
                intelligence=10,
                wisdom=10,
                constitution=10,
                charisma=10,
                starting_skills={"arcane": 20},
            )

        for year in result["years"]:
            self.assertEqual(year["money_earned"], 25 * 4)

    def test_simulate_experience_years_uses_maker_skill_for_craft_track(self):
        """Craft track pay tracks Maker Skill specifically, not just any skill."""
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        # Craft position 13 ("Maker Skill" on the craft track's table) grants a
        # maker skill point every year: 1, 2, 3, 4 -> levels 1, 1, 2, 2.
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)),
            patch("characters.skills.roll_d20", return_value=13),
        ):
            result = simulate_experience_years(
                years_requested=4,
                years_completed=0,
                current_age=16,
                track="craft",
                strength=10,
                dexterity=10,
                intelligence=10,
                wisdom=10,
                constitution=10,
                charisma=10,
                starting_skills={"sword": 21},  # unrelated high skill, ignored
            )

        # Craft multiplier is 75; must follow Maker Skill (1, 1, 2, 2), not
        # the unrelated level-6 "sword" skill.
        money_by_year = [year["money_earned"] for year in result["years"]]
        self.assertEqual(money_by_year, [75, 75, 150, 150])


class SurvivalModifierTest(BaseTestCase):
    """How the survival roll's attribute modifier is figured (issue #222).

    Spencer's report: maluses and bonuses were mishandled. Two things were
    wrong. The modifier was computed once per term from the attributes as they
    stood when the term began, so an attribute gained during the term and an
    aging penalty reached during it both went unnoticed for the rest of it --
    and four one-year rolls therefore did not agree with one four-year roll.
    (The second half, a negative modifier printing as "+ -2", is pinned in
    test_experience_display.py.)
    """

    def _run(self, *, years, age, track, attributes, skills=None, d20=10):
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        with (
            patch("characters.skills.roll_survival_dice", return_value=(4, 4, 4, 12)),
            patch("characters.skills.roll_d20", return_value=d20),
        ):
            return simulate_experience_years(
                years_requested=years,
                years_completed=0,
                current_age=age,
                track=track,
                starting_skills=skills or {},
                **attributes,
            )

    @staticmethod
    def _modifiers(result):
        return [year["survival_check"]["modifier"] for year in result["years"]]

    def test_the_best_modifier_is_the_single_highest_attributes(self):
        """Six modifiers, one is used -- the largest, per survivability.md."""
        from characters.skills import get_best_modifier

        self.assertEqual(get_best_modifier(10, 10, 10, 10, 10, 10), 0)
        self.assertEqual(get_best_modifier(3, 3, 3, 3, 3, 16), 3)
        # One weak attribute alongside an average one changes nothing: the
        # malus is not the best modifier, so it never reaches the roll.
        self.assertEqual(get_best_modifier(3, 10, 10, 10, 10, 10), 0)

    def test_an_all_weak_character_rolls_at_a_malus(self):
        """Every attribute below 8 -> the best modifier is itself a penalty."""
        result = self._run(
            years=4,
            age=16,
            track="laborer",
            attributes={
                "strength": 5,
                "dexterity": 6,
                "intelligence": 4,
                "wisdom": 3,
                "constitution": 5,
                "charisma": 4,
            },
            d20=1,  # laborer position 1 is "Weapon" -- a plain skill, no bonus
        )
        # 6 is the best of them: modifier -2, and it is subtracted, not added.
        self.assertEqual(self._modifiers(result), [-2, -2, -2, -2])
        check = result["years"][0]["survival_check"]
        self.assertEqual(check["roll"], 12)
        self.assertEqual(check["total"], 10)

    def test_an_attribute_gained_mid_run_raises_later_years_modifiers(self):
        """A "+1 STR" year counts from the next year's survival roll on.

        Laborer position 10 is the "+1 STR" special, so every year of this run
        raises Strength by one. Starting at 13 (modifier 0), the character is
        14 (+1) from the second year, 15 (+2) from the third, and so on -- the
        modifier used to sit at 0 for the whole term.
        """
        result = self._run(
            years=4,
            age=16,
            track="laborer",
            attributes={
                "strength": 13,
                "dexterity": 10,
                "intelligence": 10,
                "wisdom": 10,
                "constitution": 10,
                "charisma": 10,
            },
        )
        self.assertEqual(self._modifiers(result), [0, 1, 2, 3])

    def test_aging_reached_mid_run_lowers_later_years_modifiers(self):
        """Crossing age 36 costs STR/DEX from the following year's roll.

        Ages 34-37: the character turns 36 at the end of the second year, so
        the third and fourth years roll at the aged score. The malus used to
        arrive only after the whole term had been rolled.
        """
        result = self._run(
            years=4,
            age=33,
            track="merchant",
            attributes={
                "strength": 16,
                "dexterity": 10,
                "intelligence": 10,
                "wisdom": 10,
                "constitution": 10,
                "charisma": 10,
            },
            d20=1,
        )
        # STR 16 is +3; aging's -1 at 36 makes it 15, or +2.
        self.assertEqual(self._modifiers(result), [3, 3, 3, 2])

    def test_one_year_at_a_time_matches_one_long_run(self):
        """Four single-year runs leave the same sheet as one four-year run.

        This is the property that makes "Roll 1 year" and "Roll 4 years" the
        same offer (issue #221), and it is the reason the modifier has to be
        recomputed per year rather than snapshotted per run.

        Driven through ``services.apply_experience_years`` and saved, not
        through the simulator alone: this test used to compare two simulator
        results and stand in for the write-back with ``running["strength"]
        += 1`` by hand, so it stayed green through issue #629, where the
        write-back raised Strength by one however many "+1 STR" years the run
        aggregated. Laborer position 10 is "+1 STR", so every forced year
        here rolls one.
        """
        from unittest.mock import patch

        from characters import services
        from common.test_helpers import create_character

        owner = self.create_user("one-year-at-a-time")
        attributes = {
            "strength": 13,
            "dexterity": 10,
            "intelligence": 10,
            "wisdom": 10,
            "constitution": 10,
            "charisma": 10,
        }
        long_run_character = create_character(
            owner, name="Four At Once", age=16, **attributes
        )
        year_by_year_character = create_character(
            owner, name="One At A Time", age=16, **attributes
        )

        with (
            patch("characters.skills.roll_survival_dice", return_value=(4, 4, 4, 12)),
            patch("characters.skills.roll_d20", return_value=10),
        ):
            _, long_run = services.apply_experience_years(
                long_run_character, "laborer", 4
            )
            long_run_character.save()

            one_at_a_time = []
            for _ in range(4):
                _, single = services.apply_experience_years(
                    year_by_year_character, "laborer", 1
                )
                year_by_year_character.save()
                one_at_a_time.extend(self._modifiers(single))

        self.assertEqual(self._modifiers(long_run), one_at_a_time)
        # And the sheets the modifiers were computed from must agree too.
        long_run_character.refresh_from_db()
        year_by_year_character.refresh_from_db()
        self.assertEqual(long_run_character.strength, 17)
        self.assertEqual(year_by_year_character.strength, 17)
        self.assertEqual(long_run_character.age, year_by_year_character.age)
        self.assertEqual(long_run_character.total_xp, year_by_year_character.total_xp)

    def test_rolling_a_skill_the_character_already_has_carries_its_points_forward(
        self,
    ):
        """Points on the sheet before the run count toward the level (#685).

        skill-tracks.md: "If you already have a skill and roll it again, you
        get +1 toward the next skill level." No simulator test passed a
        starting_skills entry for a skill the forced roll would land on, so
        the branch was only ever exercised from zero within a single run --
        test_skills.py's one starting_skills case deliberately passes an
        unrelated skill to prove it is ignored, which is the opposite case.

        Laborer position 1 is "Weapon". Three points already held plus this
        year's one is four, which is level 2, and a laborer's pay is the
        track's 25 a level -- so the year earns 50, not the 25 a first point
        would earn.
        """
        attributes = {
            "strength": 10,
            "dexterity": 10,
            "intelligence": 10,
            "wisdom": 10,
            "constitution": 10,
            "charisma": 10,
        }
        carried_forward = self._run(
            years=1,
            age=16,
            track="laborer",
            attributes=attributes,
            skills={"weapon": 3},
            d20=1,
        )
        from_scratch = self._run(
            years=1, age=16, track="laborer", attributes=attributes, d20=1
        )

        # The run reports the point it added, not the total; the sheet keeps
        # the running total, and apply_experience_run adds them together.
        self.assertEqual(carried_forward["skills_gained"], {"weapon": 1})
        self.assertEqual(carried_forward["years"][0]["money_earned"], 50)
        self.assertEqual(from_scratch["years"][0]["money_earned"], 25)

    def test_an_attribute_gain_stops_at_the_storage_cap(self):
        """The running score respects the same ceiling the endpoint applies."""
        result = self._run(
            years=3,
            age=16,
            track="laborer",
            attributes={
                "strength": character_rules().attributes.maximum,
                "dexterity": 10,
                "intelligence": 10,
                "wisdom": 10,
                "constitution": 10,
                "charisma": 10,
            },
        )
        # The table's last row (issue #806); this used to pin 30 - 13 = +17.
        capped_modifier = 5
        self.assertEqual(self._modifiers(result), [capped_modifier] * 3)

    def test_an_aged_out_attribute_floors_at_zero(self):
        """A penalty larger than the score leaves 0, never a negative score."""
        from characters.skills import _effective_attributes_at_age

        effective = _effective_attributes_at_age(
            {"strength": 3, "charisma": 12}, age=100
        )
        self.assertEqual(effective["strength"], 0)
        self.assertEqual(effective["charisma"], 12)


class AgingByAgeTest(TestCase):
    """Aging is keyed to age, not to how experience was bought (issue #221)."""

    def test_the_stage_advances_every_four_years_from_the_start_age(self):
        from characters.skills import age_for_aging_stage, aging_stage_for_age

        self.assertEqual(aging_stage_for_age(16), 0)
        self.assertEqual(aging_stage_for_age(35), 4)
        self.assertEqual(aging_stage_for_age(36), 5)
        self.assertEqual(aging_stage_for_age(39), 5)
        self.assertEqual(aging_stage_for_age(40), 6)
        self.assertEqual(age_for_aging_stage(5), 36)
        self.assertEqual(age_for_aging_stage(13), 68)

    def test_the_schedule_is_the_same_at_every_age_between_steps(self):
        from characters.skills import get_aging_penalties_for_age

        for age in range(16, 36):
            self.assertIsNone(get_aging_penalties_for_age(age), age)
        for age in range(36, 40):
            penalties = get_aging_penalties_for_age(age)
            assert penalties is not None
            self.assertEqual(penalties["strength"], -1, age)
            self.assertEqual(penalties["constitution"], 0, age)
        at_forty = get_aging_penalties_for_age(40)
        assert at_forty is not None
        self.assertEqual(at_forty["constitution"], -1)

    def test_the_next_change_names_the_age_it_lands_at(self):
        from characters.skills import next_aging_change_after_age

        self.assertEqual(
            next_aging_change_after_age(16, 20),
            (36, {"strength": -1, "dexterity": -1}),
        )
        self.assertEqual(
            next_aging_change_after_age(36, 20),
            (40, {"strength": -1, "dexterity": -1, "constitution": -1}),
        )

    def test_there_is_no_next_change_past_the_horizon(self):
        from characters.skills import next_aging_change_after_age

        self.assertIsNone(next_aging_change_after_age(96, 20))

    def test_a_run_reports_aging_only_when_the_totals_move(self):
        from unittest.mock import patch

        from characters.skills import simulate_experience_years

        def run(age):
            with patch(
                "characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)
            ):
                return simulate_experience_years(
                    years_requested=4,
                    years_completed=0,
                    current_age=age,
                    track="merchant",
                    strength=10,
                    dexterity=10,
                    intelligence=10,
                    wisdom=10,
                    constitution=10,
                    charisma=10,
                )

        # 28 -> 32 crosses no penalty, 32 -> 36 brings the first one, and
        # 40 -> 44 changes nothing that 40 did not already carry.
        self.assertIsNone(run(28)["aging"])
        self.assertEqual(run(32)["aging"]["strength"], -1)
        self.assertIsNone(run(40)["aging"])


class CharacterSkillsModelTest(BaseTestCase):
    """Tests for Character model skills-related properties."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = Character.objects.create(
            user=self.user,
            name="Test",
            strength=14,
            dexterity=12,
            intelligence=16,
            wisdom=10,
            constitution=13,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
        )

    def test_effective_attributes_no_penalties(self):
        """Effective attributes equal base when no penalties."""
        self.assertEqual(self.character.effective_strength, 14)
        self.assertEqual(self.character.effective_dexterity, 12)
        self.assertEqual(self.character.effective_intelligence, 16)
        self.assertEqual(self.character.effective_wisdom, 10)
        self.assertEqual(self.character.effective_constitution, 13)

    def test_effective_attributes_with_penalties(self):
        """Effective attributes are reduced by the aging the character's age carries.

        Age is the lever, not the stored columns: aging is keyed to age and
        the columns are reconciled from it on save (issue #680). aging.md's
        age-68 column is STR -2, DEX -2, INT -1, WIS -1, CON -2.
        """
        self.character.age = 68
        self.character.save()

        self.assertEqual(self.character.effective_strength, 12)
        self.assertEqual(self.character.effective_dexterity, 10)
        self.assertEqual(self.character.effective_intelligence, 15)
        self.assertEqual(self.character.effective_wisdom, 9)
        self.assertEqual(self.character.effective_constitution, 11)

    def test_effective_attributes_floor_at_zero(self):
        """Effective attributes floor at 0, never go negative.

        aging.md's escalation past 68 is uncapped ("there is no floor on the
        penalty itself, though a character's effective attribute ... cannot go
        below 0"), so a great enough age outruns any base score: at 120 the
        STR total is -15, well past this character's 14.
        """
        self.character.age = 120
        self.character.save()

        self.assertEqual(self.character.aging_str_penalty, 15)
        self.assertEqual(self.character.effective_strength, 0)

    def test_total_allocated_skill_points(self):
        """total_allocated_skill_points sums all skill points."""
        self.character.skills = {"sword": 3, "navigation": 2}
        self.character.save()

        self.assertEqual(self.character.total_allocated_skill_points, 5)

    def test_total_allocated_skill_points_empty(self):
        """total_allocated_skill_points returns 0 for empty skills."""
        self.character.skills = {}
        self.assertEqual(self.character.total_allocated_skill_points, 0)

        self.character.skills = None
        self.assertEqual(self.character.total_allocated_skill_points, 0)


class SkillsViewsTest(BaseTestCase):
    """Tests for skills-related views."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = Character.objects.create(
            user=self.user,
            name="Test",
            strength=14,
            dexterity=12,
            intelligence=16,
            wisdom=10,
            constitution=13,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
        )

    def test_add_experience_term_response_carries_rendered_levels(self):
        """The term response ships each skill's level string, so the page
        never recomputes the ladder client-side (issues #146, #154)."""
        from unittest.mock import patch

        self.character.skills = {"sword": 21, "arcane": 6}
        self.character.save()

        self.login_as(self.user)
        # Laborer position 10 is the "+1 STR" special, so no skill points move
        # and the two seeded skills keep their totals through the term.
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)),
            patch("characters.skills.roll_d20", return_value=10),
        ):
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/add-years/",
                {"track": "laborer"},
            )

        self.assertEqual(response.status_code, 200)
        skills = response.json()["character"]["skills"]
        self.assertEqual(skills["sword"], {"points": 21, "level": 6, "display": "VI"})
        self.assertEqual(skills["arcane"], {"points": 6, "level": 2, "display": "II"})

    def test_add_experience_years_defaults_to_a_single_year(self):
        """A body that names only a track plays one year (issue #221)."""
        from unittest.mock import patch

        self.login_as(self.user)
        with patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)):
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/add-years/",
                {"track": "merchant"},
            )

        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(self.character.age, 17)
        self.assertEqual(self.character.experience_years, 1)
        self.assertEqual(self.character.total_xp, 750)
        self.assertEqual(len(response.json()["run_result"]["years"]), 1)

    def test_add_experience_years_rejects_a_count_outside_the_bounds(self):
        from characters.skills import MAX_EXPERIENCE_YEARS_PER_RUN

        self.login_as(self.user)
        for bad_years in (0, -3, MAX_EXPERIENCE_YEARS_PER_RUN + 1):
            with self.subTest(years=bad_years):
                response = self.post_json(
                    f"/tarmar/characters/{self.character.pk}/add-years/",
                    {"track": "merchant", "years": bad_years},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("between 1 and", response.json()["error"])

    def test_add_experience_years_rejects_a_non_numeric_count(self):
        self.login_as(self.user)
        for bad_years in ("many", True, 2.5, None):
            with self.subTest(years=bad_years):
                response = self.post_json(
                    f"/tarmar/characters/{self.character.pk}/add-years/",
                    {"track": "merchant", "years": bad_years},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("whole number", response.json()["error"])

    def test_add_experience_term_success(self):
        """A four-year run updates the character exactly as a term used to."""
        from unittest.mock import patch

        self.login_as(self.user)
        # Mock survival to always succeed so all 4 years complete
        with patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)):
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/add-years/",
                {"track": "merchant", "years": 4},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("run_result", data)
        self.assertIn("character", data)
        # Response should include level and money_earned
        self.assertIn("level", data["character"])
        self.assertIn("money_earned", data["character"])
        # run_result records the free skill points this run granted (for the
        # live-updated Experience History log).
        self.assertIn("free_points_awarded", data["run_result"])
        self.assertEqual(
            data["run_result"]["free_points_awarded"],
            data["character"]["free_skill_points"],
        )

        # Verify character was updated
        self.character.refresh_from_db()
        self.assertEqual(self.character.age, 20)  # 16 + 4
        self.assertEqual(self.character.experience_years, 4)
        # Merchant track: 750 XP per year * 4 years = 3000 XP
        self.assertEqual(self.character.total_xp, 3000)
        # At 3000 XP: 2 skill points from XP thresholds (at 1000 and 2000)
        self.assertGreaterEqual(self.character.free_skill_points, 2)

    def test_add_experience_term_response_carries_derived_stats(self):
        """Aging can move STR/DEX/CON, which moves fatigue/body, the injury-
        thresholds table, and the encumbrance/movement readout the same way
        any other attribute edit does. The add-term response now carries all
        of it (matching _build_field_update_response's shape for the first
        three, and _get_equipment_response's for movement) so the modal's
        success path can refresh them in place instead of going stale until
        Close reloads the page (issue #163)."""
        from unittest.mock import patch

        self.login_as(self.user)
        with patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)):
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/add-years/",
                {"track": "merchant"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.character.refresh_from_db()

        character_data = data["character"]
        self.assertEqual(character_data["fatigue"], self.character.fatigue)
        self.assertEqual(character_data["body"], self.character.body)
        thresholds = self.character.injury_thresholds
        assert thresholds is not None
        self.assertIn(
            f'id="injury-fatigue-survival_save_at">'
            f"{thresholds['fatigue']['survival_save_at']}</td>",
            character_data["injury_thresholds_html"],
        )
        # A run can grant +1 STR/DEX/CON and can cross an aging stage, so the
        # Core Attributes inputs and Physical/Mental move with it. The
        # attributes were sent and never read; the derived pair was not sent
        # at all (issue #638).
        self.assertEqual(character_data["strength"], self.character.strength)
        self.assertEqual(character_data["dexterity"], self.character.dexterity)
        self.assertEqual(character_data["constitution"], self.character.constitution)
        self.assertEqual(character_data["physical"], self.character.physical)
        self.assertEqual(character_data["mental"], self.character.mental)
        # The five aging_*_penalty values have no cell on the sheet; they used
        # to be sent to a reader that did not exist.
        self.assertNotIn("aging_str_penalty", character_data)

        self.assertIn("movement", data)
        movement = data["movement"]
        self.assertEqual(movement["carried_weight"], self.character.carried_weight)
        self.assertEqual(
            movement["encumbrance_level"], self.character.encumbrance_level
        )
        self.assertEqual(movement["movement_penalty"], self.character.movement_penalty)
        self.assertEqual(movement["gait_readout"], self.character.gait_readout)

    def test_add_experience_term_random_track(self):
        """Adding experience term with random track works."""
        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/add-years/",
            {"track": "random"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn(
            data["run_result"]["track"],
            [
                "merchant",
                "campaigner",
                "laborer",
                "magic",
                "underworld",
                "civil_service",
                "craft",
                "hunter_gatherer",
            ],
        )

    def test_add_experience_term_requires_login(self):
        """Adding experience term requires authentication."""
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/add-years/",
            {"track": "merchant"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_add_experience_term_requires_ownership(self):
        """Users can only add terms to their own characters."""
        other_user = self.create_user("other")
        self.login_as(other_user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/add-years/",
            {"track": "merchant"},
        )
        self.assertEqual(response.status_code, 403)

    def test_add_experience_term_dead_character(self):
        """Cannot add terms to deceased character."""
        self.character.is_alive = False
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/add-years/",
            {"track": "merchant"},
        )

        self.assertEqual(response.status_code, 400)
        # The one refusal every write gives a dead character (issue #932).
        self.assertIn("is dead", response.json()["error"])
        self.assertIn("bring them back to life", response.json()["error"])

    def test_add_experience_term_invalid_track(self):
        """Adding experience term with invalid track fails."""
        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/add-years/",
            {"track": "invalid_track"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown track", response.json()["error"])

    def test_allocate_skill_point_success(self):
        """Allocating skill point works.

        This used to start "Navigation", a name no rule knows, as the
        character's player: issue #862, a player starting any skill. A
        player starts one the skills glossary names; a Game Master's free
        name is in test_allocation_names.py.
        """
        self.character.free_skill_points = 5
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/allocate-skill/",
            {"skill": "Perception"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["skill"], "perception")
        self.assertEqual(data["points"], 1)
        self.assertEqual(data["free_skill_points"], 4)

        self.character.refresh_from_db()
        self.assertEqual(self.character.skills["perception"], 1)

    def test_allocate_skill_point_existing_skill(self):
        """Allocating to existing skill increases points."""
        self.character.free_skill_points = 5
        self.character.skills = {"sword": 2}
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/allocate-skill/",
            {"skill": "sword"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["points"], 3)

    def test_allocate_skill_point_no_free_points(self):
        """Cannot allocate without free points."""
        self.character.free_skill_points = 0
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/allocate-skill/",
            {"skill": "Navigation"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("No free skill points", response.json()["error"])

    def test_allocate_skill_point_past_level_five(self):
        """Issue #146: 15 points is not a ceiling — spending continues past V."""
        self.character.free_skill_points = 7
        self.character.skills = {"sword": 15}  # Level V
        self.character.save()

        self.login_as(self.user)
        for expected_points in range(16, 22):
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/allocate-skill/",
                {"skill": "sword"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["points"], expected_points)

        # The 21st point is level VI, and the response says so.
        data = response.json()
        self.assertEqual(data["points"], 21)
        self.assertEqual(data["level"], 6)
        self.assertEqual(data["display"], "VI")

    def test_allocate_skill_point_arcane_level_is_doubled(self):
        """Issue #154: Arcane's response reports the doubled-cost level."""
        self.character.free_skill_points = 1
        self.character.skills = {"arcane": 5}
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/allocate-skill/",
            {"skill": "Arcane"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["skill"], "arcane")
        self.assertEqual(data["points"], 6)
        self.assertEqual(data["level"], 2)
        self.assertEqual(data["display"], "II")

    def test_allocate_skill_point_stops_at_the_storage_ceiling(self):
        """The only remaining ceiling is the storage bound import shares."""
        from characters.skills import MAX_SKILL_POINTS

        self.character.free_skill_points = 5
        self.character.skills = {"sword": MAX_SKILL_POINTS}
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/allocate-skill/",
            {"skill": "sword"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("maximum storable points", response.json()["error"])

    def test_allocate_skill_point_empty_name(self):
        """Cannot allocate with empty skill name."""
        self.character.free_skill_points = 5
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/allocate-skill/",
            {"skill": ""},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Skill name required", response.json()["error"])

    def test_character_edit_shows_skills_section(self):
        """Character edit page shows skills section."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Skills & Experience")
        self.assertContains(response, "track-select")
        self.assertContains(response, "roll-years-btn")

    def test_character_edit_context_includes_skill_constants(self):
        """Character edit page context includes skill/aging constants from Python.

        These used to be interpolated into an inline <script>; since issue #63
        they ride in the page's json_script config block, which is where
        static/js/character-edit.js reads them. Same guarantee, new spelling:
        the numbers stay single-sourced in skills.py.
        """
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")

        self.assertEqual(response.status_code, 200)
        # Survival targets from Python
        self.assertContains(response, '"survivalTargets"')
        self.assertContains(response, '"merchant": 4')
        # Aging penalties from Python
        self.assertContains(response, '"agingByAge"')

    def test_character_edit_keeps_allocating_past_level_v(self):
        """Issue #146: no level is terminal, so no row gets a MAX badge."""
        self.character.skills = {"sword": 21, "shield": 6}
        self.character.free_skill_points = 3
        self.character.save()

        self.login_as(self.user)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        body = response.content.decode()

        self.assertNotIn("max-badge", body)
        for skill_name in ("sword", "shield"):
            with self.subTest(skill=skill_name):
                row_start = body.index(f'data-skill="{skill_name}"')
                row = body[row_start : body.index("</div>", row_start)]
                self.assertIn("allocate-btn", row)

    def test_character_edit_context_carries_server_computed_levels(self):
        """The page renders levels from skills.py, never from JavaScript math."""
        self.character.skills = {"sword": 21, "arcane": 6, "shield": 5}
        self.character.save()

        self.login_as(self.user)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")

        by_name = {row["name"]: row for row in response.context["skills_display"]}
        self.assertEqual(by_name["sword"]["points"], 21)
        self.assertEqual(by_name["sword"]["level"], 6)
        self.assertEqual(by_name["sword"]["display"], "VI")
        # Arcane costs double: 6 points is level II, not level III (issue #154).
        self.assertEqual(by_name["arcane"]["level"], 2)
        self.assertEqual(by_name["arcane"]["display"], "II")
        self.assertEqual(by_name["shield"]["display"], "II (+2)")

        # And those strings are what the rows actually print.
        body = response.content.decode()
        for skill_name, printed in (
            ("sword", "Level VI"),
            ("arcane", "Level II"),
            ("shield", "Level II (+2)"),
        ):
            with self.subTest(skill=skill_name):
                row_start = body.index(f'data-skill="{skill_name}"')
                row = body[row_start : body.index("</div>", row_start)]
                self.assertIn(printed, row)

    def test_character_edit_ships_no_level_ladder_to_javascript(self):
        """The client must not re-derive levels — that is skills.py's job."""
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        body = response.content.decode()

        self.assertNotIn("skillThresholds", body)
        self.assertNotIn("function getSkillLevel", body)
        self.assertNotIn("function formatSkillLevel", body)

    def test_add_experience_term_invalid_json(self):
        """Adding experience term with invalid JSON returns error."""
        self.login_as(self.user)
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/add-years/",
            "not valid json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid JSON", response.json()["error"])

    def test_add_experience_term_json_array_body(self):
        """A well-formed JSON array (not an object) body returns 400, not a 500.

        Regression test for issue #148: raw json.loads + .get() on a JSON
        array body raised AttributeError before this endpoint used
        _parse_json_body like its sibling endpoints.
        """
        self.login_as(self.user)
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/add-years/",
            json.dumps(["laborer"]),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid JSON", response.json()["error"])

    def test_add_experience_term_missing_character_is_json_404(self):
        """A pk with no character answers JSON, not Django's 404 page.

        These two endpoints are only ever reached by fetch(), so the front
        end needs a body it can read. characters/views/access.py keeps that
        answer separate from the HTML 404 the page endpoints raise.
        """
        self.login_as(self.user)
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk + 9999}/add-years/",
            json.dumps({"track": "laborer"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["error"], refusal_words().views_access.not_found
        )

    def test_allocate_skill_point_missing_character_is_json_404(self):
        """The skill-point endpoint answers a missing character the same way."""
        self.login_as(self.user)
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk + 9999}/allocate-skill/",
            json.dumps({"skill": "Brawling"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["error"], refusal_words().views_access.not_found
        )

    def test_add_experience_term_with_special_skills(self):
        """Adding term applies special skills like attribute bonuses."""
        from unittest.mock import patch

        self.login_as(self.user)

        # Mock to return special skills like +1 STR
        # Laborer track: 500 XP/year * 4 = 2000 XP
        mock_result = {
            "years_requested": 1,
            "track": "laborer",
            "years": [{"year": 4, "age": self.character.age + 4}],
            "skills_gained": {"+1 str": 1, "skill point": 2, "spell point": 1},
            "xp_awarded": 2000,
            "money_earned": 0,
            "survival_check": {"success": True},
            "alive": True,
            "aging": None,
        }
        with patch(
            "characters.services.simulate_experience_years",
            return_value=mock_result,
        ):
            original_str = self.character.strength
            original_free = self.character.free_skill_points
            original_spell = self.character.free_spell_points
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/add-years/",
                {"track": "laborer"},
            )

        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        # +1 STR should increase strength
        self.assertEqual(self.character.strength, original_str + 1)
        # skill point (2) + 2 from XP thresholds = 4; the spell point routes to
        # its own pool, not skill points.
        self.assertEqual(self.character.free_skill_points, original_free + 4)
        self.assertEqual(self.character.free_spell_points, original_spell + 1)

    def test_add_experience_term_with_dex_bonus(self):
        """Adding term with +1 DEX increases dexterity."""
        from unittest.mock import patch

        self.login_as(self.user)

        mock_result = {
            "years_requested": 1,
            "track": "laborer",
            "years": [{"year": 4, "age": self.character.age + 4}],
            "skills_gained": {"+1 dex": 1},
            "xp_awarded": 2000,
            "money_earned": 0,
            "survival_check": {"success": True},
            "alive": True,
            "aging": None,
        }
        with patch(
            "characters.services.simulate_experience_years",
            return_value=mock_result,
        ):
            original_dex = self.character.dexterity
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/add-years/",
                {"track": "laborer"},
            )

        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(self.character.dexterity, original_dex + 1)

    def test_add_experience_term_with_con_bonus(self):
        """Adding term with +1 CON increases constitution."""
        from unittest.mock import patch

        self.login_as(self.user)

        mock_result = {
            "years_requested": 1,
            "track": "laborer",
            "years": [{"year": 4, "age": self.character.age + 4}],
            "skills_gained": {"+1 con": 1},
            "xp_awarded": 2000,
            "money_earned": 0,
            "survival_check": {"success": True},
            "alive": True,
            "aging": None,
        }
        with patch(
            "characters.services.simulate_experience_years",
            return_value=mock_result,
        ):
            original_con = self.character.constitution
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/add-years/",
                {"track": "laborer"},
            )

        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(self.character.constitution, original_con + 1)

    def test_add_experience_term_passes_base_attributes_and_age(self):
        """The endpoint hands the simulator base scores plus the current age.

        Aging is a pure function of age since issue #221, so the simulator --
        not the view -- decides what an aged character rolls with, and it can
        keep deciding as the run crosses a birthday.
        """
        from unittest.mock import patch

        self.login_as(self.user)
        self.character.aging_str_penalty = 3
        self.character.aging_dex_penalty = 2
        self.character.aging_int_penalty = 4
        self.character.aging_wis_penalty = 1
        self.character.aging_con_penalty = 2
        self.character.save()

        mock_result = {
            "years_requested": 1,
            "track": "laborer",
            "years": [{"year": 4, "age": self.character.age + 4}],
            "skills_gained": {},
            "xp_awarded": 2000,
            "money_earned": 0,
            "survival_check": {"success": True},
            "alive": True,
            "aging": None,
        }
        with patch(
            "characters.services.simulate_experience_years",
            return_value=mock_result,
        ) as mock_simulate:
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/add-years/",
                {"track": "laborer"},
            )

        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_simulate.call_args
        # Base scores go in; the simulator ages them itself, year by year, from
        # the age each year starts at (issues #221, #222). Passing already
        # penalized scores would have double-counted aging once the simulator
        # started applying it.
        self.assertEqual(kwargs["strength"], self.character.strength)
        self.assertEqual(kwargs["dexterity"], self.character.dexterity)
        self.assertEqual(kwargs["intelligence"], self.character.intelligence)
        self.assertEqual(kwargs["wisdom"], self.character.wisdom)
        self.assertEqual(kwargs["constitution"], self.character.constitution)
        self.assertEqual(kwargs["charisma"], self.character.charisma)
        self.assertEqual(kwargs["current_age"], self.character.age)

    def test_add_experience_term_with_aging(self):
        """Adding term applies aging penalties after term 5."""
        from unittest.mock import patch

        self.login_as(self.user)
        self.character.experience_terms = 4
        self.character.age = 32
        self.character.save()

        mock_result = {
            "years_requested": 4,
            "track": "merchant",
            "years": [{"year": 20, "age": 36}],
            "skills_gained": {},
            "xp_awarded": 3000,
            "money_earned": 0,
            "survival_check": {"success": True},
            "alive": True,
            "aging": {"strength": -1, "dexterity": -1},
        }
        with patch(
            "characters.services.simulate_experience_years",
            return_value=mock_result,
        ):
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/add-years/",
                {"track": "merchant"},
            )

        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(self.character.aging_str_penalty, 1)
        self.assertEqual(self.character.aging_dex_penalty, 1)

    def test_add_experience_term_death(self):
        """Character can die from failed survival check."""
        from unittest.mock import patch

        self.login_as(self.user)
        age_before_term = self.character.age

        mock_result = {
            "years_requested": 1,
            "track": "campaigner",
            # Died in year 2 of the term: simulate_experience_years stops
            # appending once the survival check fails, so `years` only has
            # the two years actually played.
            "years": [
                {"year": 1, "age": age_before_term + 1},
                {"year": 2, "age": age_before_term + 2},
            ],
            "skills_gained": {},
            "xp_awarded": 2400,  # Campaigner: 1200 * 2 years played
            "money_earned": 0,
            "survival_check": {"success": False, "roll": 2, "target": 9},
            "alive": False,
            "aging": None,
        }
        with patch(
            "characters.services.simulate_experience_years",
            return_value=mock_result,
        ):
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/add-years/",
                {"track": "campaigner"},
            )

        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_alive)
        self.assertIn("survival check", self.character.death_cause)
        # Regression guard (issue #147): age-at-death must match the year the
        # character actually died in (year 2 here, age_before_term + 2), not
        # a flat +4 for the whole term -- that used to overstate age at
        # death by up to 3 years.
        self.assertEqual(self.character.age, age_before_term + 2)
        self.assertEqual(response.json()["character"]["age"], age_before_term + 2)

    def test_allocate_skill_point_invalid_json(self):
        """Allocating skill with invalid JSON returns error."""
        self.character.free_skill_points = 5
        self.character.save()

        self.login_as(self.user)
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/allocate-skill/",
            "not valid json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid JSON", response.json()["error"])

    def test_allocate_skill_point_requires_ownership(self):
        """Users can only allocate points to their own characters."""
        self.character.free_skill_points = 5
        self.character.save()

        other_user = self.create_user("other")
        self.login_as(other_user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/allocate-skill/",
            {"skill": "sword"},
        )
        self.assertEqual(response.status_code, 403)

    def test_dm_allocating_skill_point_does_not_mark_played(self):
        """A DM allocating a point on someone else's sheet doesn't flip their
        "Played" status (issue #91), matching the fix applied to the
        equipment endpoints: is_played means the owner touched the sheet,
        not a DM correcting it."""
        self.character.free_skill_points = 5
        self.character.is_played = False
        self.character.save()

        dm = self.create_dm()
        self.login_as(dm)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/allocate-skill/",
            {"skill": "sword"},
        )
        self.assertEqual(response.status_code, 200)

        self.character.refresh_from_db()
        self.assertFalse(self.character.is_played)

    def test_rename_skill_success(self):
        """Renaming a skill updates the character.

        A Game Master's rename (issue #768): a player may no longer rename a
        skill into another one, which this test used to do as the owner.
        """
        self.character.skills = {"sword": 3, "navigation": 2}
        self.character.save()

        self.login_as(self.create_dm())
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"old_name": "sword", "new_name": "longsword"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["skill"], "longsword")
        self.assertEqual(data["points"], 3)

        self.assertEqual(data["level"], 2)
        self.assertEqual(data["display"], "II")

        self.character.refresh_from_db()
        self.assertNotIn("sword", self.character.skills)
        self.assertEqual(self.character.skills["longsword"], 3)
        self.assertEqual(self.character.skills["navigation"], 2)

    def test_rename_skill_to_arcane_reprices_the_level(self):
        """Issue #154: the points stay, but Arcane buys half as much level.

        A Game Master's rename: a player's rename into Arcane is the #768 bug.
        """
        self.character.skills = {"sword": 6}
        self.character.save()

        self.login_as(self.create_dm())
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"old_name": "sword", "new_name": "Arcane"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["skill"], "arcane")
        self.assertEqual(data["points"], 6)
        # 6 points was level III as a sword; as Arcane it is level II.
        self.assertEqual(data["level"], 2)
        self.assertEqual(data["display"], "II")

    def test_rename_skill_same_name(self):
        """Renaming to same name returns unchanged."""
        self.character.skills = {"sword": 3}
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"old_name": "sword", "new_name": "Sword"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertTrue(data.get("unchanged"))

    def test_rename_skill_not_found(self):
        """Renaming non-existent skill fails."""
        self.character.skills = {"sword": 3}
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"old_name": "archery", "new_name": "bow"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not found", response.json()["error"])

    def test_rename_skill_already_exists(self):
        """Cannot rename to a skill name that already exists."""
        self.character.skills = {"sword": 3, "navigation": 2}
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"old_name": "sword", "new_name": "navigation"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("already exists", response.json()["error"])

    def test_rename_skill_requires_login(self):
        """Renaming skill requires authentication."""
        self.character.skills = {"sword": 3}
        self.character.save()

        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"old_name": "sword", "new_name": "longsword"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_rename_skill_requires_ownership(self):
        """Users can only rename skills on their own characters."""
        self.character.skills = {"sword": 3}
        self.character.save()

        other_user = self.create_user("other")
        self.login_as(other_user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"old_name": "sword", "new_name": "longsword"},
        )
        self.assertEqual(response.status_code, 403)

    def test_rename_skill_missing_old_name(self):
        """Renaming requires old name."""
        self.character.skills = {"sword": 3}
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"new_name": "longsword"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Original skill name required", response.json()["error"])

    def test_rename_skill_missing_new_name(self):
        """Renaming requires new name."""
        self.character.skills = {"sword": 3}
        self.character.save()

        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            {"old_name": "sword"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("New skill name required", response.json()["error"])

    def test_rename_skill_invalid_json(self):
        """Renaming with invalid JSON returns error."""
        self.character.skills = {"sword": 3}
        self.character.save()

        self.login_as(self.user)
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/rename-skill/",
            "not valid json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid JSON", response.json()["error"])


class NormalizeSkillNamePatternsTest(TestCase):
    """normalize_skill_name's patterns, rewritten for linear time (issue #11).

    A skill name comes from a player's request, an imported file or the
    model. These pin what the function makes of one, so the rewrite changed
    how long it takes and nothing else. Some expectations below are odd; they
    record what the function has always done with an odd name. Issue #52
    changed one kind of odd name, one with parentheses nested or unbalanced
    inside a bonus, which used to come apart; those cases say so below.
    """

    def test_parenthetical_bonuses(self):
        from characters.skills import normalize_skill_name

        cases = {
            "Sword (+1 to hit)": "sword to hit",
            "Sword\t(+1   to hit)\n": "sword to hit",
            "Bow (2 vs armour) (masterwork)": "bow vs armour",
            "Bow (masterwork) (+2 vs armour)": "bow vs armour",
            "Climb (+1)": "climb (+1)",
            "Knowledge (history)": "knowledge (history)",
            "Sword (+1 to hit": "sword (+1 to hit",
            "Sword (+1 to hit) II": "sword ii to hit",
            # Issue #52: the bonus is removed and the parentheses balanced
            # inside it stay. These two used to come out as "lore tales)
            # (old" and "sword x) )".
            "Lore (+1 (old) tales)": "lore (old) tales",
            "Sword (+1 ) x)": "sword (+1 ) x)",
            # An unclosed "(" after the last ")" stays as it was.
            "Sword (+1 to hit) (": "sword ( to hit",
            "Axe (+1 to hit) (((": "axe ((( to hit",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_skill_name(raw), expected)

    def test_nested_parentheses_stay_balanced(self):
        """Issue #52: a name with parentheses inside parentheses is not torn."""
        from characters.skills import normalize_skill_name

        cases = {
            "Lore (+1 (old) tales)": "lore (old) tales",
            "Lore (+1 (old (older)) tales) (rare)": "lore (old (older)) tales",
            "Lore (rare) (+2 (old) tales)": "lore (old) tales",
            "Lore (+1 (old tales)": "lore (+1 (old tales)",
            "Lore (+1 (old) tales": "lore (+1 (old) tales",
            # A bonus inside another group is not the name's bonus.
            "Lore (old (+1 tales))": "lore (old (+1 tales))",
            "Lore (old (rare))": "lore (old (rare))",
            "Lore (+1 (old) tales) II": "lore ii (old) tales",
            # Unbalanced: the "(" before the bonus never closes, so the
            # balanced group inside it is the name's only group.
            "Sword (x (+1 y)": "sword (x y",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_skill_name(raw), expected)

    def test_top_level_parentheses_pairs_only_balanced_groups(self):
        from characters.skill_names import top_level_parentheses

        cases = {
            "": [],
            "no groups": [],
            "a (b) c": [(2, 4)],
            "a (b (c)) (d)": [(2, 8), (10, 12)],
            ") a (b) (": [(4, 6)],
            "(a (b) c": [(3, 5)],
            "(a (b) c)": [(0, 8)],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(top_level_parentheses(text), expected)

    def test_roman_numeral_suffixes(self):
        from characters.skills import normalize_skill_name

        cases = {
            "Shield Parry IV": "shield parry",
            "Survival\tXI ": "survival",
            "Vix": "vix",
            "Sword  iii  ": "sword",
            "Track II III": "track ii",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_skill_name(raw), expected)

    def test_long_names_are_read(self):
        from characters.skills import normalize_skill_name

        words = "a " * 5000
        self.assertEqual(
            normalize_skill_name(f"Sword (+1 {words}to hit) II"),
            f"sword ii {words}to hit",
        )
        self.assertEqual(normalize_skill_name("(1 " * 3000), "(1 " * 2999 + "(1")

    def test_the_skill_name_patterns_stay_fast_on_crafted_input(self):
        # The tests above check what is made of a name; this one checks how
        # long it takes. Each input the old code was slow on is sized so it
        # took 13 to 20 s there, so code that backtracks again fails here
        # within the minute rather than hanging the run, while the new code
        # takes a few milliseconds; an imported file may hold a name of up to
        # 256 KB. The first group goes through the function, which is how
        # every caller reaches the patterns; the second gives each pattern
        # whitespace runs the function would have collapsed, so each stays
        # linear on its own (the parenthesis scanner is not a pattern, but is
        # held to the same bound). The inputs marked "guard" were fast for the old
        # code too; they hold the new code's own paths to the same bound.
        from characters.skill_names import (
            _BONUS_CONTENT,
            _ROMAN_NUMERAL_SUFFIX,
            normalize_skill_name,
            top_level_parentheses,
        )

        through_the_function = {
            "unclosed bonuses": "(1 " * 47_000,
            "a bonus then unclosed parentheses": "(1 a)" + "(" * 250_000,
            "a bonus then spaced unclosed parentheses": "(1 a)" + " (" * 125_000,
            "guard: a bonus then a long text": "(+1 " + "a " * 100_000 + ")",
            # Issue #52: nesting is paired by a stack, not matched by backtracking.
            "deeply nested groups": "(" * 100_000 + ")" * 100_000,
            "a bonus around deeply nested groups": (
                "(+1 " + "(a " * 100_000 + ")" * 100_000 + ")"
            ),
            "deeply nested unclosed groups": "(+1 " * 100_000 + ")" * 50_000,
            "many groups before a bonus": "(a) " * 60_000 + "(+1 b)",
            "whitespace before a group": "x" + " " * 160_000 + "(+1 y)",
            "many stray closing parentheses": ")" * 250_000 + "(+1 a)",
            "empty groups side by side": "()" * 130_000,
            "guard: roman numerals and spaces": "a " + "i " * 100_000 + "z",
        }
        for label, raw in through_the_function.items():
            with self.subTest(input=label, pattern="normalize_skill_name"):
                started = time.perf_counter()
                normalize_skill_name(raw)
                self.assertLess(time.perf_counter() - started, 1.0)
        on_their_own = {
            ("bonus", "spaces then a word"): (
                _BONUS_CONTENT.fullmatch,
                "+1" + " " * 80_000 + "y",
            ),
            ("bonus", "guard: spaces then nothing"): (
                _BONUS_CONTENT.fullmatch,
                "+1" + " " * 80_000,
            ),
            ("bonus", "spaces then a newline"): (
                _BONUS_CONTENT.fullmatch,
                "+1" + " " * 80_000 + "\n",
            ),
            ("parentheses", "spaces then a word"): (
                top_level_parentheses,
                "a" + " " * 160_000 + "y",
            ),
            ("parentheses", "a long run of openers then closers"): (
                top_level_parentheses,
                "(" * 130_000 + ")" * 130_000,
            ),
            ("parentheses", "empty groups side by side"): (
                top_level_parentheses,
                "()" * 130_000,
            ),
            ("parentheses", "alternating nested openers and closers"): (
                top_level_parentheses,
                "(a (b " * 40_000 + ")" * 40_000 + "(c) " * 20_000,
            ),
            ("roman numeral", "spaces then a word"): (
                lambda text: _ROMAN_NUMERAL_SUFFIX.sub("", text),
                "a" + " " * 60_000 + "y",
            ),
            ("roman numeral", "spaces then numerals then a word"): (
                lambda text: _ROMAN_NUMERAL_SUFFIX.sub("", text),
                "a" + " " * 30_000 + "iv" + " " * 30_000 + "y",
            ),
        }
        for (pattern_label, input_label), (apply_pattern, text) in on_their_own.items():
            with self.subTest(input=input_label, pattern=pattern_label):
                started = time.perf_counter()
                apply_pattern(text)
                self.assertLess(time.perf_counter() - started, 1.0)


class SkillMaxDynamicTest(TestCase):
    """Tests for dynamic skill max calculation."""

    def test_each_track_has_one_skill_per_d20_position(self):
        """Every track has exactly 20 skills — one per reachable d20 roll."""
        from characters.skill_rules import skill_rules

        for track_name, track in skill_rules().tracks.tracks.items():
            track_skills = track.skills
            self.assertEqual(
                len(track_skills), 20, f"{track_name} should have 20 skills"
            )

    def test_position_past_table_clamps_to_last_skill(self):
        """A position beyond the 20-entry table clamps to that track's last skill."""
        from characters.skill_rules import skill_rules
        from characters.skills import get_skill_for_year

        for track, rules in skill_rules().tracks.tracks.items():
            skill = get_skill_for_year(track, 21)
            self.assertEqual(skill, rules.skills[-1], f"{track} position 21")


class IsSpecialExperienceResultTest(TestCase):
    """Tests for the special-vs-real-skill check (issue #224).

    Both the experience simulator and the skill-suggestion corpus need to
    tell a real skill apart from a special roll result (an attribute bonus or
    a bonus point), so the check lives in exactly one place.
    """

    def test_attribute_bonuses_are_special(self):
        from characters.skills import is_special_experience_result

        self.assertTrue(is_special_experience_result("+1 str"))
        self.assertTrue(is_special_experience_result("+1 dex"))
        self.assertTrue(is_special_experience_result("+1 con"))

    def test_bonus_points_are_special(self):
        from characters.skills import is_special_experience_result

        self.assertTrue(is_special_experience_result("skill point"))
        self.assertTrue(is_special_experience_result("spell point"))

    def test_real_skills_are_not_special(self):
        from characters.skills import is_special_experience_result

        self.assertFalse(is_special_experience_result("weapon"))
        self.assertFalse(is_special_experience_result("maker skill"))
        self.assertFalse(is_special_experience_result("weather/path"))


class GrantedSkillNamesTest(TestCase):
    """Tests for the type-ahead corpus's single source of truth (issue #224).

    ``granted_skill_names`` enumerates every real skill any track's d20 table
    can grant, straight from the canon's track tables -- so the corpus can never drift
    from what the prior-experience generator actually rolls.
    """

    def test_includes_every_non_special_track_entry(self):
        from characters.skill_rules import skill_rules
        from characters.skills import (
            granted_skill_names,
            is_special_experience_result,
            normalize_skill_name,
        )

        names = granted_skill_names()
        for track in skill_rules().tracks.tracks.values():
            for raw in track.skills:
                normalized = normalize_skill_name(raw)
                if is_special_experience_result(normalized):
                    continue
                self.assertIn(normalized, names)

    def test_excludes_special_roll_results(self):
        from characters.skills import granted_skill_names

        names = granted_skill_names()
        for special in ("+1 str", "+1 dex", "+1 con", "skill point", "spell point"):
            self.assertNotIn(special, names)

    def test_is_sorted_and_deduplicated(self):
        from characters.skills import granted_skill_names

        names = granted_skill_names()
        self.assertEqual(names, sorted(set(names)))

    def test_matches_the_skills_glossary_count(self):
        """37 real skills are documented (reference/content/public-rules/
        characters/prior-experience/skills-glossary.md, the "Combat skills"
        through "Wilderness, survival & labor skills" tables) -- the same 37
        come straight out of the track data."""
        from characters.skills import granted_skill_names

        self.assertEqual(len(granted_skill_names()), 37)
