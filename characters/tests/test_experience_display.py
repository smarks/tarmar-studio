"""Survival-roll display in the experience history and death card (issue #175).

Each year of a run stores its own ``survival_check`` — faces included — and
these tests pin the per-year rendering plus the fatal-roll provenance shown
when a character died during prior experience.

The survival roll became 3d6 in issue #223. Checks stored while it was 2d6 are
still in players' logs, so most of the fixtures here deliberately hold two
dice: they are the regression guard that a legacy check renders as what it
actually was, never relabelled.
"""

from django.test import TestCase

from characters.models import Character
from characters.templatetags.dice_display import survival_roll
from common.test_helpers import BaseTestCase

CHARACTER_DEFAULTS = {
    "strength": 12,
    "dexterity": 12,
    "intelligence": 12,
    "wisdom": 12,
    "constitution": 12,
    "charisma": 12,
    "appearance_roll": "3",
    "height_roll": "3",
    "weight_roll": "3",
    "fatigue_roll": 7,
}


def _survival_check(dice, modifier, target, success):
    return {
        "dice": list(dice),
        "roll": sum(dice),
        "modifier": modifier,
        "total": sum(dice) + modifier,
        "target": target,
        "success": success,
        "auto_success": False,
        "auto_death": sum(dice) == 2,
    }


def _year(year, age, skill, roll, survival_check):
    return {
        "year": year,
        "age": age,
        "skill": skill,
        "skill_normalized": skill.lower(),
        "roll": roll,
        "xp_awarded": 250,
        "money_earned": 50,
        "survival_check": survival_check,
    }


def _survival_check_3d6(dice, modifier, target, success):
    """A check in the current 3d6 shape (issue #223)."""
    return {
        "dice": list(dice),
        "roll": sum(dice),
        "modifier": modifier,
        "total": sum(dice) + modifier,
        "target": target,
        "success": success,
        "auto_success": sum(dice) == 18,
        "auto_death": sum(dice) == 3,
    }


def _term(years, *, term=1, track="merchant", alive=True):
    return {
        "years_requested": len(years),
        "track": track,
        "years": years,
        "skills_gained": {},
        "xp_awarded": 250 * len(years),
        "money_earned": 50 * len(years),
        "free_points_awarded": 1,
        "survival_check": years[-1]["survival_check"],
        "aging": None,
        "alive": alive,
    }


class ExperienceHistorySurvivalDisplayTest(BaseTestCase):
    """The server-rendered Experience History shows every year's dice."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def _render_with_log(self, experience_log, **extra):
        character = Character.objects.create(
            user=self.user,
            name="Term Tester",
            experience_log=experience_log,
            **CHARACTER_DEFAULTS,
            **extra,
        )
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_each_year_shows_its_own_survival_faces(self):
        html = self._render_with_log(
            [
                _term(
                    [
                        _year(
                            1, 21, "Haggling", 4, _survival_check([3, 4], 1, 4, True)
                        ),
                        _year(2, 22, "Sword", 6, _survival_check([2, 3], 1, 4, True)),
                        _year(3, 23, "Riding", 2, _survival_check([6, 5], 1, 4, True)),
                        _year(
                            4, 24, "Haggling", 5, _survival_check([1, 3], 1, 4, True)
                        ),
                    ]
                )
            ]
        )
        self.assertIn("survival 2d6 [3, 4] + 1 = 8 vs 4: survived", html)
        self.assertIn("survival 2d6 [2, 3] + 1 = 6 vs 4: survived", html)
        self.assertIn("survival 2d6 [6, 5] + 1 = 12 vs 4: survived", html)
        self.assertIn("survival 2d6 [1, 3] + 1 = 5 vs 4: survived", html)

    def test_a_death_term_marks_the_fatal_year_and_uses_its_age_range(self):
        html = self._render_with_log(
            [
                _term(
                    [
                        _year(
                            1, 21, "Haggling", 4, _survival_check([3, 4], 1, 8, False)
                        ),
                    ],
                    alive=False,
                )
            ],
            is_alive=False,
            death_cause="Failed survival check during merchant term 1",
        )
        self.assertIn("survival 2d6 [3, 4] + 1 = 8 vs 8: died", html)
        # The summary line's age range ends at the fatal year, not a blank
        # ``years.3`` lookup.
        # A term cut short in its first year spans one age; render it as a
        # single age, not a degenerate "21-21" range (issue #217).
        self.assertIn("Merchant, Age 21", html)
        self.assertNotIn("Age 21&ndash;21", html)

    def test_a_legacy_year_without_a_survival_check_still_renders(self):
        year = _year(1, 21, "Haggling", 4, _survival_check([3, 4], 1, 4, True))
        del year["survival_check"]
        term = _term([_year(2, 22, "Sword", 6, _survival_check([2, 3], 1, 4, True))])
        term["years"].insert(0, year)
        html = self._render_with_log([term])
        self.assertIn("Haggling (rolled 4)", html)
        self.assertIn("survival 2d6 [2, 3] + 1 = 6 vs 4: survived", html)


class FatalSurvivalCheckTest(BaseTestCase):
    """``Character.fatal_survival_check`` and the deceased banner (issue #175)."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def _dead_character(self, experience_log, death_cause):
        return Character.objects.create(
            user=self.user,
            name="Late Lamented",
            experience_log=experience_log,
            is_alive=False,
            death_cause=death_cause,
            **CHARACTER_DEFAULTS,
        )

    def test_property_returns_the_last_years_check_of_a_fatal_term(self):
        fatal = _survival_check([1, 2], 0, 6, False)
        character = self._dead_character(
            [_term([_year(1, 21, "Haggling", 4, fatal)], alive=False)],
            "Failed survival check during merchant term 1",
        )
        self.assertEqual(character.fatal_survival_check, fatal)

    def test_property_is_none_for_a_living_character(self):
        character = Character.objects.create(
            user=self.user,
            name="Still Here",
            experience_log=[
                _term(
                    [_year(1, 21, "Haggling", 4, _survival_check([6, 6], 0, 4, True))]
                )
            ],
            **CHARACTER_DEFAULTS,
        )
        self.assertIsNone(character.fatal_survival_check)

    def test_property_is_none_when_death_did_not_come_from_experience(self):
        """A DM-marked death has no fatal roll — the last term was survived."""
        character = self._dead_character(
            [_term([_year(1, 21, "Haggling", 4, _survival_check([6, 6], 0, 4, True))])],
            "Eaten by a dragon",
        )
        self.assertIsNone(character.fatal_survival_check)

    def test_property_is_none_with_no_experience_log(self):
        character = self._dead_character([], "Eaten by a dragon")
        self.assertIsNone(character.fatal_survival_check)

    def test_deceased_banner_shows_the_fatal_roll(self):
        character = self._dead_character(
            [
                _term(
                    [_year(1, 21, "Haggling", 4, _survival_check([1, 2], 0, 6, False))],
                    alive=False,
                )
            ],
            "Failed survival check during merchant term 1",
        )
        html = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        self.assertIn("Failed survival check during merchant term 1", html)
        self.assertIn("Fatal roll: 2d6 [1, 2] + 0 = 3 vs 6", html)

    def test_deceased_banner_omits_the_fatal_roll_for_other_deaths(self):
        character = self._dead_character(
            [_term([_year(1, 21, "Haggling", 4, _survival_check([6, 6], 0, 4, True))])],
            "Eaten by a dragon",
        )
        html = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        self.assertIn("Eaten by a dragon", html)
        self.assertNotIn("Fatal roll:", html)


class SurvivalRollFilterEdgeTest(TestCase):
    """The natural-roll overrides must be visible in the roll string (issue
    #217): a fatal lowest roll that beats the target otherwise reads like the
    game cheated. With 3d6 (issue #223) those naturals are 3 and 18."""

    def test_triple_ones_is_labeled_always_fatal(self):
        check = _survival_check_3d6([1, 1, 1], 3, 2, False)
        self.assertEqual(
            survival_roll(check),
            "3d6 [1, 1, 1] + 3 = 6 vs 2 (triple ones — always fatal)",
        )

    def test_triple_sixes_is_labeled_always_survives(self):
        check = _survival_check_3d6([6, 6, 6], 0, 20, True)
        self.assertEqual(
            survival_roll(check),
            "3d6 [6, 6, 6] + 0 = 18 vs 20 (triple sixes — always survives)",
        )

    def test_ordinary_rolls_are_unchanged(self):
        check = _survival_check_3d6([3, 4, 5], 1, 4, True)
        self.assertEqual(survival_roll(check), "3d6 [3, 4, 5] + 1 = 13 vs 4")

    def test_a_negative_modifier_reads_as_a_subtraction(self):
        """Issue #222: a malus used to print as the nonsense "+ -2"."""
        check = _survival_check_3d6([3, 4, 5], -2, 4, True)
        self.assertEqual(survival_roll(check), "3d6 [3, 4, 5] - 2 = 10 vs 4")

    def test_a_zero_modifier_still_reads_as_a_plus(self):
        check = _survival_check_3d6([3, 4, 5], 0, 4, True)
        self.assertEqual(survival_roll(check), "3d6 [3, 4, 5] + 0 = 12 vs 4")

    def test_a_stored_2d6_check_still_renders_as_2d6(self):
        """A check recorded before issue #223 has two dice; say so (never 3d6)."""
        check = _survival_check([3, 4], 1, 4, True)
        self.assertEqual(survival_roll(check), "2d6 [3, 4] + 1 = 8 vs 4")

    def test_a_stored_2d6_natural_keeps_its_own_name(self):
        check = _survival_check([1, 1], 3, 2, False)
        self.assertEqual(
            survival_roll(check),
            "2d6 [1, 1] + 3 = 5 vs 2 (snake eyes — always fatal)",
        )
        boxcars = _survival_check([6, 6], 0, 14, True)
        boxcars["auto_success"] = True
        self.assertEqual(
            survival_roll(boxcars),
            "2d6 [6, 6] + 0 = 12 vs 14 (boxcars — always survives)",
        )

    def test_a_missing_check_renders_as_nothing(self):
        """Legacy log entries predate per-year checks and carry nothing."""
        self.assertEqual(survival_roll({}), "")


class TheAddYearsAnswerCarriesTheSurvivalSentenceTest(BaseTestCase):
    """The browser prints the server's sentence rather than writing its own.

    ``static/js/character-edit.js`` used to build the survival-roll string a
    second time, in JavaScript, so the line a player saw the moment a term was
    rolled came from a different implementation than the line the same page
    showed after a reload (issue #338 item 4). The response carries the text
    now, and these tests are what holds it there.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user("runner")
        self.character = Character.objects.create(
            user=self.user, name="Year Roller", **CHARACTER_DEFAULTS
        )
        self.login_as(self.user)

    def _run(self):
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/add-years/",
            data='{"track": "merchant", "years": 3}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["run_result"]

    def test_every_year_that_rolled_carries_the_rendered_sentence(self):
        years = self._run()["years"]
        self.assertTrue(years, "the run produced no years")
        for year in years:
            check = year.get("survival_check")
            if not check:
                continue
            with self.subTest(year=year["year"]):
                self.assertEqual(check["text"], survival_roll(check))
                self.assertIn(" vs ", check["text"])

    def test_the_sentence_is_not_written_back_onto_the_character(self):
        """A display string has no business being stored with the dice."""
        self._run()
        self.character.refresh_from_db()
        for entry in self.character.experience_log or []:
            for year in entry.get("years", []):
                check = year.get("survival_check") or {}
                with self.subTest(year=year.get("year")):
                    self.assertNotIn("text", check)


class NoJavaScriptRebuildsTheSurvivalSentenceTest(TestCase):
    """The guard on issue #338 item 4: one implementation, and it is Python."""

    def test_no_module_names_the_natural_roll_overrides(self):
        from pathlib import Path

        from django.conf import settings

        offenders = [
            str(path.relative_to(settings.BASE_DIR))
            for path in sorted((Path(settings.BASE_DIR) / "static" / "js").glob("*.js"))
            if "snake eyes" in path.read_text() or "triple sixes" in path.read_text()
        ]
        self.assertEqual(
            offenders,
            [],
            "The survival roll reads the same in one place: "
            "characters/templatetags/dice_display.py. These files build it again:\n  "
            + "\n  ".join(offenders),
        )
