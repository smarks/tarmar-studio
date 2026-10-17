"""Tests for movement gaits and the movement modifier (issue #143).

See reference/content/public-rules/combat/action-options/movement.md:

    Movement Modifier: CON modifier + STR modifier + DEX modifier
    Sprint 18+modifier / Run 12+modifier / Jog 7+modifier / Walk 4+modifier

and the Encumbrance table's Overloaded row: "Walk only, 1 hex max".

Every case runs on the default canon's equipment rules and catalog, seated
whichever canon is loaded (issue #75): the numbers are the public pages'.
"""

from django.test import SimpleTestCase

from characters.models import Character
from characters.movement import (
    compute_gaits,
    gait_distance,
    movement_modifier,
)
from characters.tests.rules_fixtures import (
    DEFAULT_EQUIPMENT_RULES,
    seated_equipment_catalog,
    seated_equipment_rules,
)
from common.test_helpers import BaseTestCase


class DefaultEquipmentSeated(SimpleTestCase):
    """Seats the default canon's equipment rules and catalog for each test."""

    def setUp(self):
        super().setUp()
        self.enterContext(seated_equipment_catalog())
        self.enterContext(seated_equipment_rules())


class MovementModifierFunctionTest(SimpleTestCase):
    """Pure-function tests for characters.movement.movement_modifier."""

    def test_sums_the_three_attribute_modifiers(self):
        self.assertEqual(movement_modifier(1, 0, 0), 1)
        self.assertEqual(movement_modifier(1, 2, -1), 2)
        self.assertEqual(movement_modifier(0, 0, 0), 0)
        self.assertEqual(movement_modifier(-2, -1, -1), -4)


class GaitDistanceFunctionTest(DefaultEquipmentSeated):
    """Pure-function tests for characters.movement.gait_distance."""

    def test_worked_example_from_movement_md(self):
        """CON 14 (+1), STR 10 (0), DEX 12 (0): walk 5, jog 8, run 13, sprint 19."""
        modifier = movement_modifier(1, 0, 0)
        self.assertEqual(modifier, 1)
        self.assertEqual(gait_distance("walk", modifier), 5)
        self.assertEqual(gait_distance("jog", modifier), 8)
        self.assertEqual(gait_distance("run", modifier), 13)
        self.assertEqual(gait_distance("sprint", modifier), 19)

    def test_adds_movement_penalty(self):
        self.assertEqual(gait_distance("walk", 1, movement_penalty=-1), 4)
        self.assertEqual(gait_distance("jog", 1, movement_penalty=-2), 6)

    def test_floors_at_zero(self):
        self.assertEqual(gait_distance("walk", -10, movement_penalty=-4), 0)


class ComputeGaitsFunctionTest(DefaultEquipmentSeated):
    """Pure-function tests for characters.movement.compute_gaits."""

    def test_no_restrictions_computes_every_gait(self):
        gaits = compute_gaits(1, 0, [])
        self.assertEqual(gaits, {"sprint": 19, "run": 13, "jog": 8, "walk": 5})

    def test_restricted_gaits_map_to_none(self):
        gaits = compute_gaits(1, -2, ["run", "sprint"])
        self.assertIsNone(gaits["run"])
        self.assertIsNone(gaits["sprint"])
        self.assertEqual(gaits["jog"], 6)
        self.assertEqual(gaits["walk"], 3)

    def test_walk_limit_overrides_the_formula(self):
        gaits = compute_gaits(1, 0, ["jog", "run", "sprint"], walk_limit=1)
        self.assertEqual(gaits, {"sprint": None, "run": None, "jog": None, "walk": 1})

    def test_walk_limit_does_not_apply_when_walk_itself_is_restricted(self):
        """Defensive: a hypothetical restriction on Walk still wins over the cap."""
        gaits = compute_gaits(1, 0, ["walk"], walk_limit=1)
        self.assertIsNone(gaits["walk"])

    def test_result_covers_every_gait_in_order(self):
        gaits = compute_gaits(0, 0, [])
        self.assertEqual(list(gaits), list(DEFAULT_EQUIPMENT_RULES.gaits.order))


class CharacterMovementModifierTest(DefaultEquipmentSeated, BaseTestCase):
    """Character.movement_modifier from effective attributes (issue #143)."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = Character.objects.create(
            user=self.user,
            name="Mover",
            strength=15,  # +2
            dexterity=12,  # 0
            intelligence=10,
            wisdom=13,
            constitution=14,  # +1
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
            equipment=[],
        )

    def test_movement_modifier_sums_con_str_dex(self):
        self.assertEqual(self.character.movement_modifier, 3)

    def test_movement_modifier_uses_effective_attributes(self):
        """Aging penalties feed the modifier via effective_* attributes.

        Driven by the character's age, which is what aging is keyed to
        (issue #680): aging.md gives a 40-year-old STR -1, DEX -1 and CON -1,
        so 15/12/14 are read as 14/11/13 and the modifiers +2/0/+1 become
        +1/0/0.
        """
        self.character.age = 40
        self.character.save()
        self.assertEqual(
            (
                self.character.effective_strength,
                self.character.effective_dexterity,
                self.character.effective_constitution,
            ),
            (14, 11, 13),
        )
        self.assertEqual(self.character.movement_modifier, 1)

    def test_unencumbered_gait_distances(self):
        self.assertEqual(self.character.gait_distances["sprint"], 21)
        self.assertEqual(self.character.gait_distances["run"], 15)
        self.assertEqual(self.character.gait_distances["jog"], 10)
        self.assertEqual(self.character.gait_distances["walk"], 7)

    def test_worked_example_matches_movement_md(self):
        """CON 14, STR 10, DEX 12 -> walk 5, jog 8, run 13, sprint 19."""
        self.character.strength = 10
        self.character.save()
        self.assertEqual(self.character.movement_modifier, 1)
        self.assertEqual(self.character.gait_distances["walk"], 5)
        self.assertEqual(self.character.gait_distances["jog"], 8)
        self.assertEqual(self.character.gait_distances["run"], 13)
        self.assertEqual(self.character.gait_distances["sprint"], 19)


class CharacterGaitRestrictionInteractionTest(DefaultEquipmentSeated, BaseTestCase):
    """Gait distances react to encumbrance and armor restrictions (issue #143)."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = Character.objects.create(
            user=self.user,
            name="Encumbered Mover",
            strength=15,
            dexterity=12,
            intelligence=10,
            wisdom=13,
            constitution=14,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
            equipment=[],
        )

    def test_light_encumbrance_reduces_every_gait_but_restricts_none(self):
        # STR 15: light is 16-22.5 lbs.
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Gear",
                "category": "gear",
                "quantity": 1,
                "weight": 20,
                "in_pack": False,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.encumbrance_level, "light")
        self.assertEqual(self.character.movement_penalty, -1)
        self.assertEqual(self.character.movement_restrictions, [])
        # Base (unencumbered) values are 21/15/10/7; light knocks 1 off each.
        self.assertEqual(self.character.gait_distances["sprint"], 20)
        self.assertEqual(self.character.gait_distances["run"], 14)
        self.assertEqual(self.character.gait_distances["jog"], 9)
        self.assertEqual(self.character.gait_distances["walk"], 6)

    def test_medium_encumbrance_forbids_run_and_sprint(self):
        # STR 15: medium is 22.5-30 lbs.
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Gear",
                "category": "gear",
                "quantity": 1,
                "weight": 28,
                "in_pack": False,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.encumbrance_level, "medium")
        self.assertIsNone(self.character.gait_distances["sprint"])
        self.assertIsNone(self.character.gait_distances["run"])
        # Jog and walk are still usable, reduced by the -2 movement penalty.
        self.assertEqual(self.character.gait_distances["jog"], 8)
        self.assertEqual(self.character.gait_distances["walk"], 5)

    def test_heavy_encumbrance_forbids_jog_run_and_sprint(self):
        # STR 15: heavy is 30-37.5 lbs.
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Gear",
                "category": "gear",
                "quantity": 1,
                "weight": 35,
                "in_pack": False,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.encumbrance_level, "heavy")
        self.assertIsNone(self.character.gait_distances["sprint"])
        self.assertIsNone(self.character.gait_distances["run"])
        self.assertIsNone(self.character.gait_distances["jog"])
        # Walk still works, reduced by the -4 movement penalty.
        self.assertEqual(self.character.gait_distances["walk"], 3)

    def test_armor_restriction_applies_even_when_unencumbered(self):
        """Plate armor forbids run/sprint on its own (issue #128), regardless
        of carried weight."""
        self.character.strength = 60  # stays unencumbered under the plate
        self.character.equipment = [
            {
                "item_id": "plate_armor",
                "quantity": 1,
                "in_pack": False,
                "equipped": True,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.encumbrance_level, "unencumbered")
        self.assertIsNone(self.character.gait_distances["sprint"])
        self.assertIsNone(self.character.gait_distances["run"])
        self.assertIsNotNone(self.character.gait_distances["jog"])
        self.assertIsNotNone(self.character.gait_distances["walk"])

    def test_overloaded_caps_walk_and_forbids_everything_else(self):
        """Overloaded: 'Walk only, 1 hex max' -- the level's walk_limit."""
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Boulder",
                "category": "gear",
                "quantity": 1,
                "weight": 100,
                "in_pack": False,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.encumbrance_level, "overloaded")
        self.assertIsNone(self.character.gait_distances["sprint"])
        self.assertIsNone(self.character.gait_distances["run"])
        self.assertIsNone(self.character.gait_distances["jog"])
        self.assertEqual(self.character.gait_distances["walk"], 1)

    def test_gait_readout_shape_and_na_flags(self):
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Boulder",
                "category": "gear",
                "quantity": 1,
                "weight": 100,
                "in_pack": False,
            },
        ]
        self.character.save()
        readout = {entry["key"]: entry for entry in self.character.gait_readout}
        self.assertEqual(set(readout), {"sprint", "run", "jog", "walk"})
        self.assertTrue(readout["sprint"]["na"])
        self.assertIsNone(readout["sprint"]["distance"])
        self.assertFalse(readout["walk"]["na"])
        self.assertEqual(readout["walk"]["distance"], 1)
        self.assertEqual(readout["walk"]["label"], "Walk")


class CharacterEditPageMovementDisplayTest(DefaultEquipmentSeated, BaseTestCase):
    """The character sheet renders the movement modifier and gaits (issue #143)."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = Character.objects.create(
            user=self.user,
            name="Sheet Mover",
            strength=15,
            dexterity=12,
            intelligence=10,
            wisdom=13,
            constitution=14,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
            equipment=[],
        )
        self.login_as(self.user)

    def test_renders_unrestricted_gaits(self):
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('class="gait" id="gait-sprint"', content)
        self.assertIn('class="gait" id="gait-run"', content)
        self.assertIn('class="gait" id="gait-jog"', content)
        self.assertIn('class="gait" id="gait-walk"', content)
        self.assertIn('<span class="gait-value">21</span>', content)  # sprint
        self.assertIn('<span class="gait-value">15</span>', content)  # run
        self.assertNotIn('class="gait gait-na"', content)

    def test_renders_restricted_gaits_as_na(self):
        self.character.equipment = [
            {
                "item_id": "plate_armor",
                "quantity": 1,
                "in_pack": False,
                "equipped": True,
            },
        ]
        self.character.save()
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('class="gait gait-na" id="gait-run"', content)
        self.assertIn('class="gait gait-na" id="gait-sprint"', content)
        self.assertIn('<span class="gait-value">n/a</span>', content)

    def test_renders_overloaded_walk_cap(self):
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Boulder",
                "category": "gear",
                "quantity": 1,
                "weight": 100,
                "in_pack": False,
            },
        ]
        self.character.save()
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        content = response.content.decode()
        self.assertIn('class="gait" id="gait-walk"', content)
        self.assertNotIn('class="gait gait-na" id="gait-walk"', content)
