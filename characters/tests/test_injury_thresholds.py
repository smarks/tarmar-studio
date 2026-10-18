"""Injury and death thresholds on the character sheet (issue #130).

The rules live in
``reference/content/public-rules/characters/injury-thresholds-death.md``; the
drift guard that ties these numbers back to that file is in
``test_rules_consistency.py``.
"""

from characters.models import Character, survival_save_penalty
from common.test_helpers import BaseTestCase


class InjuryThresholdMathTest(BaseTestCase):
    """Threshold values for each pool, including the rounding of odd pools."""

    def _thresholds(self) -> dict:
        """The fixture's thresholds; it is complete, so they are known."""
        thresholds = self.character.injury_thresholds
        assert thresholds is not None
        return thresholds

    def setUp(self):
        super().setUp()
        self.user = self.create_user("injury-math")
        # CON 15 + WIS 13 + INT 10 + max(DEX 12, STR 14) + roll 7 = Fatigue 59
        # (odd), and Body = ceil(59 * 2/3) = 40 (even).
        self.character = Character.objects.create(
            user=self.user,
            name="Injury Math",
            strength=14,
            dexterity=12,
            intelligence=10,
            wisdom=13,
            constitution=15,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
        )

    def test_matches_the_rulebook_example(self):
        """Body 30: unconscious at 0, saves at -15, penalized saves at -30."""
        thresholds = self.character._pool_injury_thresholds(30)
        self.assertEqual(thresholds["pool"], 30)
        self.assertEqual(thresholds["minus_one_at"], 15)
        self.assertEqual(thresholds["minus_two_at"], 5)
        self.assertEqual(thresholds["unconscious_at"], 0)
        self.assertEqual(thresholds["survival_save_at"], -15)
        self.assertEqual(thresholds["penalized_save_at"], -30)

    def test_even_pool_halves_exactly(self):
        thresholds = self.character._pool_injury_thresholds(40)
        self.assertEqual(thresholds["minus_one_at"], 20)
        self.assertEqual(thresholds["survival_save_at"], -20)
        self.assertEqual(thresholds["penalized_save_at"], -40)

    def test_odd_pool_rounds_toward_the_value_not_yet_reached(self):
        """Pool 31: -1 starts at 15 (16 is still above 15.5), saves at -16."""
        thresholds = self.character._pool_injury_thresholds(31)
        self.assertEqual(thresholds["minus_one_at"], 15)
        self.assertEqual(thresholds["survival_save_at"], -16)
        self.assertEqual(thresholds["penalized_save_at"], -31)

    def test_odd_pool_of_59_from_the_fixture(self):
        thresholds = self._thresholds()["fatigue"]
        self.assertEqual(thresholds["pool"], 59)
        self.assertEqual(thresholds["minus_one_at"], 29)
        self.assertEqual(thresholds["survival_save_at"], -30)
        self.assertEqual(thresholds["penalized_save_at"], -59)

    def test_both_pools_are_reported(self):
        thresholds = self._thresholds()
        self.assertEqual(thresholds["fatigue"]["pool"], 59)
        self.assertEqual(thresholds["body"]["pool"], 40)
        self.assertEqual(thresholds["body"]["minus_one_at"], 20)
        self.assertEqual(thresholds["body"]["survival_save_at"], -20)
        self.assertEqual(thresholds["body"]["penalized_save_at"], -40)

    def test_minus_one_band_disappears_when_half_falls_inside_the_1_to_5_band(self):
        """A pool of 10 halves to 5, which is already the -2 band."""
        self.assertIsNone(self.character._pool_injury_thresholds(10)["minus_one_at"])
        self.assertIsNone(self.character._pool_injury_thresholds(11)["minus_one_at"])
        self.assertEqual(self.character._pool_injury_thresholds(12)["minus_one_at"], 6)

    def test_survival_save_target_is_effective_constitution(self):
        self.assertEqual(self._thresholds()["body"]["survival_save_target"], 15)
        self.character.aging_con_penalty = 4
        self.assertEqual(self._thresholds()["body"]["survival_save_target"], 11)

    def test_thresholds_shrink_with_aging_penalties(self):
        """Pools derive from effective attributes, so the breakpoints move too."""
        self.character.aging_con_penalty = 5
        self.character.aging_wis_penalty = 3
        self.character.aging_int_penalty = 2
        self.character.aging_str_penalty = 4
        self.character.aging_dex_penalty = 6
        # Fatigue drops to 45 (see test_models.test_fatigue_uses_effective_attributes).
        thresholds = self._thresholds()["fatigue"]
        self.assertEqual(thresholds["pool"], 45)
        self.assertEqual(thresholds["minus_one_at"], 22)
        self.assertEqual(thresholds["survival_save_at"], -23)
        self.assertEqual(thresholds["penalized_save_at"], -45)


class InjuryThresholdSheetTest(BaseTestCase):
    """The sheet renders the thresholds as a static reference table."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("injury-sheet")
        self.character = Character.objects.create(
            user=self.user,
            name="Injury Sheet",
            strength=14,
            dexterity=12,
            intelligence=10,
            wisdom=13,
            constitution=15,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
        )
        self.login_as(self.user)

    def _get_page(self):
        return self.client.get(f"/tarmar/characters/{self.character.pk}/")

    def test_section_is_rendered(self):
        response = self._get_page()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Injury Thresholds")

    def test_fatigue_column_shows_the_computed_values(self):
        response = self._get_page()
        self.assertContains(response, '<td id="injury-fatigue-minus_one_at">29</td>')
        self.assertContains(response, '<td id="injury-fatigue-minus_two_at">5</td>')
        self.assertContains(response, '<td id="injury-fatigue-unconscious_at">0</td>')
        self.assertContains(
            response, '<td id="injury-fatigue-survival_save_at">-30</td>'
        )
        self.assertContains(
            response, '<td id="injury-fatigue-penalized_save_at">-59</td>'
        )

    def test_body_column_shows_the_computed_values(self):
        response = self._get_page()
        self.assertContains(response, '<td id="injury-body-minus_one_at">20</td>')
        self.assertContains(response, '<td id="injury-body-survival_save_at">-20</td>')
        self.assertContains(response, '<td id="injury-body-penalized_save_at">-40</td>')

    def test_survival_save_target_is_shown(self):
        response = self._get_page()
        self.assertContains(
            response, '<span id="injury-fatigue-survival_save_target">15</span>'
        )

    def test_absent_minus_one_band_renders_a_dash(self):
        """Tiny pools have no -1 range; the cell must not print a wrong number."""
        tiny = Character.objects.create(
            user=self.user,
            name="Tiny Pools",
            strength=1,
            dexterity=1,
            intelligence=1,
            wisdom=1,
            constitution=1,
            charisma=1,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=2,
        )
        response = self.client.get(f"/tarmar/characters/{tiny.pk}/")
        self.assertContains(response, '<td id="injury-fatigue-minus_one_at">—</td>')
        self.assertContains(response, '<td id="injury-body-minus_one_at">—</td>')


class InjuryThresholdFieldUpdateTest(BaseTestCase):
    """Editing an attribute returns the moved thresholds with the new pools."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("injury-update")
        self.character = Character.objects.create(
            user=self.user,
            name="Injury Update",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=6,
        )
        self.login_as(self.user)

    def test_constitution_edit_returns_thresholds(self):
        # An attribute is a Game Master's to type (issue #748).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("thresholds-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "constitution", "value": 10},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # Fatigue 46 (even), Body ceil(46 * 2/3) = 31 (odd).
        self.assertEqual(data["fatigue"], 46)
        self.assertEqual(data["body"], 31)
        # The response carries the readout as the page renders it (issue
        # #639): the table appears or gives way to "not known yet" as the
        # character crosses completeness, which no per-cell write could do.
        readout = data["injury_thresholds_html"]
        self.assertIn('id="injury-fatigue-minus_one_at">23</td>', readout)
        self.assertIn('id="injury-fatigue-survival_save_at">-23</td>', readout)
        self.assertIn('id="injury-body-minus_one_at">15</td>', readout)
        self.assertIn('id="injury-body-survival_save_at">-16</td>', readout)
        self.assertIn('id="injury-body-penalized_save_at">-31</td>', readout)

    def test_unrelated_edit_omits_thresholds(self):
        # Not the name: the survival save's confirm names the character, so a
        # rename re-renders the readout (issue #903).
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "height_roll", "value": "5"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("injury_thresholds_html", response.json())


class SurvivalSavePenaltyTest(BaseTestCase):
    """How far past the full threshold the pool has gone (issue #685 item 2).

    injury-thresholds-death.md: "**Survival Save Penalty:** Once past negative
    full starting value, the penalty equals how far past the threshold you've
    gone", with the worked example "A character with Body 30 goes unconscious
    at 0. At -15, they begin making survival saves (3d6 <= CON). At -30, saves
    continue but are now penalized. At -35, the penalty is -5 (3d6 <= CON -
    5)."

    Nothing computed this before: the sheet gives the breakpoints and the
    save target and stops, and the worked example lived only as prose in
    ``characters.help_text``, which no test read. A player at -35 had to do
    the arithmetic themselves off a table that does not show it.
    """

    def test_survival_save_penalty_is_how_far_past_the_full_threshold(self):
        """The published Body 30 example, value by value."""
        self.assertEqual(survival_save_penalty(30, -35), -5)
        # At the threshold itself nothing is yet past it.
        self.assertEqual(survival_save_penalty(30, -30), 0)
        self.assertEqual(survival_save_penalty(30, -31), -1)
        self.assertEqual(survival_save_penalty(30, -60), -30)

    def test_an_unpenalized_save_carries_no_penalty(self):
        """Above minus the full pool the save is unmodified, all the way up."""
        for current_value in (-29, -15, -1, 0, 5, 30):
            with self.subTest(value=current_value):
                self.assertEqual(survival_save_penalty(30, current_value), 0)

    def test_the_penalty_is_keyed_to_the_pool_it_is_measured_against(self):
        """A smaller pool reaches its penalized band sooner."""
        self.assertEqual(survival_save_penalty(10, -15), -5)
        self.assertEqual(survival_save_penalty(60, -15), 0)

    def test_the_help_text_states_the_published_example(self):
        """The prose a player reads must match what the function computes."""
        from characters.help_text import help_text

        self.assertIn(
            "at -35 with a starting 30 you need 3d6 \u2264 CON - 5",
            help_text()["survival_save"],
        )
        self.assertEqual(survival_save_penalty(30, -35), -5)
