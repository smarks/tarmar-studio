"""What a run of experience leaves on the saved character (issue #684).

The simulator group in ``test_skills.py`` asserts against the dictionary
``simulate_experience_years`` returns. That is half the story: the other half
is ``Character.apply_experience_run``, which writes the run onto the row, and
two bugs lived there unseen -- #629 (a "+1 STR" rolled three times raised
Strength by one) and #654 (a run that ended in death advanced the age and
skipped the aging those years earned). Every test here drives a real
``Character`` through ``characters.services`` and reads the row afterwards.

The aging invariant tests are issue #685 item 1, and the rule they assert is
``reference/content/public-rules/characters/prior-experience/aging.md``:
"Aging is keyed to **age**, not to how experience was bought ... Every number
in the table below is a **cumulative lifetime total** -- the full penalty
carried at that age".
"""

from unittest.mock import patch

from characters import services
from common.test_helpers import BaseTestCase, create_character

#: aging.md's table, read straight off the published page as
#: ``age -> (STR, DEX, INT, WIS, CON)`` magnitudes. Literals, not a call to
#: ``get_aging_penalties_for_age``: a test that recomputed its expectation
#: from the code under test would move with it (issue #682).
PUBLISHED_AGING_TOTALS = {
    16: (0, 0, 0, 0, 0),
    31: (0, 0, 0, 0, 0),
    32: (0, 0, 0, 0, 0),
    36: (1, 1, 0, 0, 0),
    39: (1, 1, 0, 0, 0),
    40: (1, 1, 0, 0, 1),
    64: (1, 1, 0, 0, 1),
    68: (2, 2, 1, 1, 2),
    # "For example, age 76 totals STR/DEX/CON at -4 and INT/WIS at -2."
    76: (4, 4, 2, 2, 4),
    92: (8, 8, 4, 4, 8),
}

#: Attribute scores high enough that no penalty in the table floors them at 0,
#: so every assertion below reads the penalty rather than the floor.
ROBUST_ATTRIBUTES = {
    "strength": 16,
    "dexterity": 16,
    "intelligence": 16,
    "wisdom": 16,
    "constitution": 16,
    "charisma": 16,
}


def _stored_aging(character) -> tuple[int, int, int, int, int]:
    """The five aging columns, in the order aging.md's table prints them."""
    return (
        character.aging_str_penalty,
        character.aging_dex_penalty,
        character.aging_int_penalty,
        character.aging_wis_penalty,
        character.aging_con_penalty,
    )


class StoredAgingMatchesAgeTest(BaseTestCase):
    """The penalty columns are a cache of age, never a second truth (#680)."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("aging-invariant-owner")

    def test_stored_aging_penalties_always_match_the_characters_age(self):
        """Every age in aging.md's table, saved, carries that row's totals."""
        for age, published in PUBLISHED_AGING_TOTALS.items():
            with self.subTest(age=age):
                character = create_character(
                    self.owner, name=f"Aged {age}", age=age, **ROBUST_ATTRIBUTES
                )
                self.assertEqual(_stored_aging(character), published)
                character.refresh_from_db()
                self.assertEqual(_stored_aging(character), published)

    def test_setting_the_age_alone_brings_the_penalties_with_it(self):
        """A character aged after creation does not keep the younger totals.

        This is the shape #680 reported: nothing but a run that crossed a
        four-year boundary ever wrote these columns, so a character could be
        old and carry none of the penalties, permanently.
        """
        character = create_character(
            self.owner, name="Suddenly Old", age=16, **ROBUST_ATTRIBUTES
        )
        self.assertEqual(_stored_aging(character), (0, 0, 0, 0, 0))

        character.age = 71
        character.save()
        character.refresh_from_db()

        # 71 sits between the 68 and 72 columns, so it carries the age-68 row.
        self.assertEqual(_stored_aging(character), (2, 2, 1, 1, 2))
        self.assertEqual(character.effective_strength, 14)

    def test_an_imported_character_cannot_arrive_old_and_unpenalised(self):
        """Import writes age and the penalty columns from the same payload.

        A Game Master's import, which keeps age and aging as given (#744).
        """
        from characters import import_service

        imported = import_service.ImportedCharacter(
            name="Ancient",
            fields={
                "age": 71,
                "aging_str_penalty": 0,
                "aging_dex_penalty": 0,
                "aging_int_penalty": 0,
                "aging_wis_penalty": 0,
                "aging_con_penalty": 0,
                **ROBUST_ATTRIBUTES,
            },
        )
        character = import_service.save_imported_character(
            self.create_dm("aging-dm"), imported
        )

        character.refresh_from_db()
        self.assertEqual(character.age, 71)
        self.assertEqual(_stored_aging(character), (2, 2, 1, 1, 2))


class ExperienceRunWriteBackTest(BaseTestCase):
    """What ``apply_experience_run`` leaves on the row it was handed."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("write-back-owner")

    def _character(self, **overrides):
        fields = {
            "strength": 13,
            "dexterity": 10,
            "intelligence": 10,
            "wisdom": 10,
            "constitution": 10,
            "charisma": 10,
            "age": 16,
        }
        fields.update(overrides)
        return create_character(self.owner, name="Runner", **fields)

    def test_an_attribute_rolled_three_times_rises_by_three(self):
        """Issue #629: the run's aggregated count reaches the row intact.

        Laborer position 10 is "+1 STR", so a three-year run at that position
        aggregates to ``{"+1 str": 3}``. It used to raise Strength by one.
        """
        character = self._character()
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 5, 17)),
            patch("characters.skills.roll_d20", return_value=10),
        ):
            _, run = services.apply_experience_years(character, "laborer", 3)
        character.save()
        character.refresh_from_db()

        self.assertEqual(run["skills_gained"], {"+1 str": 3})
        self.assertEqual(character.strength, 16)
        self.assertEqual(character.age, 19)

    def test_the_whole_row_a_run_writes(self):
        """Every field apply_experience_run touches, on the saved character.

        The simulator group asserts against the returned dictionary; this is
        the row. A laborer year is 500 XP and, at position 10, a "+1 STR" --
        no plain skill, so no skill points from the roll, and 1500 XP earns
        one free point at the 1,000 threshold.
        """
        character = self._character()
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 5, 17)),
            patch("characters.skills.roll_d20", return_value=10),
        ):
            services.apply_experience_years(character, "laborer", 3)
        character.save()
        character.refresh_from_db()

        self.assertEqual(character.strength, 16)
        self.assertEqual(character.dexterity, 10)
        self.assertEqual(character.constitution, 10)
        self.assertEqual(character.age, 19)
        self.assertEqual(character.total_xp, 1500)
        self.assertEqual(character.free_skill_points, 1)
        self.assertEqual(character.free_spell_points, 0)
        self.assertEqual(character.experience_terms, 1)
        self.assertEqual(character.skills, {})
        self.assertEqual(_stored_aging(character), (0, 0, 0, 0, 0))
        self.assertTrue(character.is_alive)
        self.assertEqual(len(character.experience_log), 1)
        self.assertEqual(len(character.experience_log[0]["years"]), 3)

    def test_an_attribute_gain_still_stops_at_the_storage_cap(self):
        """Three points at once cannot carry a score past MAX_ATTRIBUTE_VALUE."""
        from characters.constants import MAX_ATTRIBUTE_VALUE

        character = self._character(strength=MAX_ATTRIBUTE_VALUE - 1)
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 5, 17)),
            patch("characters.skills.roll_d20", return_value=10),
        ):
            services.apply_experience_years(character, "laborer", 3)
        character.save()
        character.refresh_from_db()

        self.assertEqual(character.strength, MAX_ATTRIBUTE_VALUE)

    def test_a_run_that_crosses_an_age_boundary_stores_that_ages_penalties(self):
        """Ages 34 to 38 crosses 36, which aging.md gives STR -1 and DEX -1."""
        character = self._character(age=34, strength=16, dexterity=16)
        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 5, 17)),
            patch("characters.skills.roll_d20", return_value=1),
        ):
            services.apply_experience_years(character, "laborer", 4)
        character.save()
        character.refresh_from_db()

        self.assertEqual(character.age, 38)
        self.assertEqual(_stored_aging(character), (1, 1, 0, 0, 0))
        self.assertEqual(character.effective_strength, 15)

    def test_a_run_that_ends_in_death_still_applies_the_aging_it_earned(self):
        """Issue #654: dying in the second year still ages the character.

        The simulator returns as soon as a survival check fails, so the run
        reported no aging at all while ``apply_experience_run`` advanced the
        age regardless -- leaving a character of 36 carrying nothing, which a
        Game Master's Revive (#403) then handed back with attributes too high.
        """
        character = self._character(age=34, strength=16, dexterity=16)
        with (
            patch(
                "characters.skills.roll_survival_dice",
                side_effect=[(6, 6, 5, 17), (1, 1, 1, 3)],
            ),
            patch("characters.skills.roll_d20", return_value=1),
        ):
            _, run = services.apply_experience_years(character, "merchant", 4)
        character.save()
        character.refresh_from_db()

        self.assertFalse(run["alive"])
        self.assertFalse(character.is_alive)
        self.assertEqual(character.age, 36)
        self.assertEqual(_stored_aging(character), (1, 1, 0, 0, 0))
        self.assertEqual(character.effective_strength, 15)
        # And the run's own log line says so, rather than staying silent.
        self.assertEqual(
            run["aging"],
            {
                "strength": -1,
                "dexterity": -1,
                "intelligence": 0,
                "wisdom": 0,
                "constitution": 0,
            },
        )

        # Bringing them back keeps the aging: it is a function of age.
        services.apply_revive(character)
        character.save()
        character.refresh_from_db()
        self.assertTrue(character.is_alive)
        self.assertEqual(character.effective_strength, 15)


class CollectionGrowthCapTest(BaseTestCase):
    """The JSON growth bounds hold wherever the writes happen (issue #655).

    They are attached as field validators, and Django's ``save()`` does not
    run field validators -- only ``full_clean`` does, and only the import path
    called it. So a character could be grown past the caps through the editing
    endpoints and was then refused by its own export/import round trip: the
    cap that is meant to "prevent runaway writes from bloating rows" ran
    nowhere the writes actually happen.
    """

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("growth-cap-owner")
        self.character = create_character(self.owner, name="Packrat")

    def test_saving_past_the_skill_cap_is_refused(self):
        from django.core.exceptions import ValidationError

        from characters.models import MAX_SKILL_ENTRIES

        self.character.skills = {
            f"skill {index}": 1 for index in range(MAX_SKILL_ENTRIES + 1)
        }
        with self.assertRaises(ValidationError) as refusal:
            self.character.save()
        self.assertIn("Too many skills", str(refusal.exception))

    def test_saving_past_the_equipment_cap_is_refused(self):
        from django.core.exceptions import ValidationError

        from characters.models import MAX_EQUIPMENT_ITEMS

        self.character.equipment = [
            {"item_id": "dagger", "quantity": 1} for _ in range(MAX_EQUIPMENT_ITEMS + 1)
        ]
        with self.assertRaises(ValidationError) as refusal:
            self.character.save()
        self.assertIn("Too many equipment items", str(refusal.exception))

    def test_saving_past_the_experience_log_cap_is_refused(self):
        from django.core.exceptions import ValidationError

        from characters.models import MAX_EXPERIENCE_LOG_ENTRIES

        self.character.experience_log = [
            {"track": "laborer"} for _ in range(MAX_EXPERIENCE_LOG_ENTRIES + 1)
        ]
        with self.assertRaises(ValidationError) as refusal:
            self.character.save()
        self.assertIn("Too many experience entries", str(refusal.exception))

    def test_a_run_trims_the_experience_log_rather_than_growing_past_the_cap(self):
        """A career longer than the cap keeps its most recent runs.

        apply_experience_run appended without bound, so a character built past
        200 runs saved fine and could never be re-imported. combat_log and
        history are sliced the same way.
        """
        from characters.models import MAX_EXPERIENCE_LOG_ENTRIES

        self.character.experience_log = [
            {"track": "laborer", "marker": index}
            for index in range(MAX_EXPERIENCE_LOG_ENTRIES)
        ]
        self.character.save()

        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 5, 17)),
            patch("characters.skills.roll_d20", return_value=1),
        ):
            services.apply_experience_years(self.character, "laborer", 1)
        self.character.save()
        self.character.refresh_from_db()

        self.assertEqual(len(self.character.experience_log), MAX_EXPERIENCE_LOG_ENTRIES)
        # The newest run is the last entry, and the oldest fell off the front.
        self.assertEqual(self.character.experience_log[-1]["track"], "laborer")
        self.assertNotIn("marker", self.character.experience_log[-1])
        self.assertEqual(self.character.experience_log[0]["marker"], 1)
