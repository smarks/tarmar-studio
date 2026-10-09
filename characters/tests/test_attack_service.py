"""``characters.attack.resolve`` — the service that finally calls the engine.

Every die here comes from :class:`ScriptedRoller`, so each branch (plain hit,
plain miss, natural 20 confirmed and not, natural 1 with its subtable, the §8
hybrid bypass) is checked against an exact expected number rather than a range.
No HTTP and no ``random``: the endpoint tests cover the request layer, and this
file covers the arithmetic.
"""

from django.test import TestCase

from characters import attack, combat
from characters.equipment import WEAPONS
from characters.models import Character
from common.test_helpers import TEST_PASSWORD

CHARACTER_DEFAULTS = {
    "strength": 12,
    "dexterity": 14,
    "intelligence": 12,
    "wisdom": 12,
    "constitution": 12,
    "charisma": 12,
    "appearance_roll": "3",
    "height_roll": "3",
    "weight_roll": "3",
    "fatigue_roll": 7,
}


class ScriptedRoller:
    """A ``characters.dice`` stand-in that hands out a pre-written sequence.

    ``d20s``/``d6s`` are consumed in order; every damage roll returns
    ``damage`` (as a single die and as the total), so a crit's repeated rolls
    are trivially predictable. Running a queue dry raises rather than falling
    back to a random number — a test that rolls more dice than it scripted has
    a bug, and should say so loudly.
    """

    def __init__(self, d20s=(), d6s=(), damage=4):
        self.d20s = list(d20s)
        self.d6s = list(d6s)
        self.damage = damage
        self.damage_rolls = 0

    def _next(self, queue, label):
        if not queue:
            raise AssertionError(f"ScriptedRoller ran out of {label}")
        return queue.pop(0)

    def roll_d20_logged(self):
        value = self._next(self.d20s, "d20s")
        return value, f"d20: {value}"

    def roll_d6_logged(self):
        value = self._next(self.d6s, "d6s")
        return value, f"1d6: {value}"

    def roll_expression_logged(self, expression):
        self.damage_rolls += 1
        return (
            [self.damage],
            self.damage,
            f"{expression}: [{self.damage}] = {self.damage}",
        )


class FacesRoller(ScriptedRoller):
    """A roller that honours the expression it is asked to roll (#875).

    :class:`ScriptedRoller` hands back one flat number whatever the
    expression, so no test ever rolled an expression with a modifier and the
    sheet counted a mace's -1 on every critical roll unseen. This one reads
    the expression the way :func:`characters.dice.roll_expression` does:
    every die shows ``face``, and the expression's own ``±M`` is folded into
    the total. Each expression it was asked for is kept, in order.
    """

    def __init__(self, d20s=(), d6s=(), face=3):
        super().__init__(d20s=d20s, d6s=d6s)
        self.face = face
        self.expressions: list[str] = []

    def roll_expression_logged(self, expression):
        from characters.dice import parse_dice_expression

        self.damage_rolls += 1
        self.expressions.append(expression)
        count, _sides, modifier = parse_dice_expression(expression)
        faces = [self.face] * count
        total = sum(faces) + modifier
        return faces, total, f"{expression}: {faces} = {total}"


class AttackServiceTestBase(TestCase):
    def setUp(self):
        from accounts.models import User

        self.user = User.objects.create_user(username="swinger", password=TEST_PASSWORD)
        self.character = Character.objects.create(
            user=self.user,
            name="Swinger",
            # A Weapon skill taken for the saber, level 3 (issue #753).
            skills={"weapon (saber)": 6},
            equipment=[
                {"item_id": "saber", "quantity": 1},
                {"item_id": "great_hammer", "quantity": 1},
                {"item_id": "backpack", "quantity": 1},
                {"item_id": "mystery blade", "quantity": 1, "category": "weapons"},
                {"item_id": "arrows_20", "quantity": 20},
                {"item_id": "club", "quantity": 1},
            ],
            **CHARACTER_DEFAULTS,
        )

    def resolve(self, roller, **params):
        base = {"weapon_index": 0, "armour_tier": "None"}
        base.update(params)
        return attack.resolve(self.character, base, roller=roller)


class AttackValidationTest(AttackServiceTestBase):
    def test_index_outside_the_equipment_list_is_refused(self):
        with self.assertRaises(attack.AttackValidationError):
            self.resolve(ScriptedRoller(), weapon_index=99)

    def test_non_integer_index_is_refused(self):
        with self.assertRaises(attack.AttackValidationError):
            self.resolve(ScriptedRoller(), weapon_index="dagger")

    def test_a_non_weapon_item_is_refused(self):
        with self.assertRaises(attack.AttackValidationError) as caught:
            self.resolve(ScriptedRoller(), weapon_index=2)
        self.assertIn("not a weapon", str(caught.exception))

    def test_a_custom_weapon_has_no_class_and_is_refused_cleanly(self):
        """The design's explicit case: a 400, not a MATRIX KeyError."""
        with self.assertRaises(attack.AttackValidationError) as caught:
            self.resolve(ScriptedRoller(), weapon_index=3)
        self.assertIn("no combat class", str(caught.exception))

    def test_ammunition_is_a_catalog_weapon_with_no_class_and_is_refused(self):
        """Arrows sit in the weapons group but carry no ``weapon_class``."""
        with self.assertRaises(attack.AttackValidationError) as caught:
            self.resolve(ScriptedRoller(), weapon_index=4)
        self.assertIn("no combat class", str(caught.exception))

    def test_a_weapon_whose_damage_does_not_parse_is_refused_not_crashed(self):
        """Club's damage is the string "varies" — a 400, never a 500.

        These weapons have a real ``weapon_class``, so every earlier check
        passes and the request reaches the damage roll. Without this guard
        ``parse_dice_expression`` raises a bare ``ValueError``, which no caller
        catches, and a landed hit 500s.
        """
        with self.assertRaises(attack.AttackValidationError) as caught:
            self.resolve(ScriptedRoller(d20s=[19]), weapon_index=5)
        self.assertIn("GM-adjudicated", str(caught.exception))

    def test_every_unrollable_catalog_weapon_is_recognised_as_such(self):
        """The five the catalog actually carries, checked against the catalog.

        Asserted by parsing rather than by a hardcoded list, so a catalog entry
        gaining or losing a real damage expression shows up here.
        """
        unrollable = {
            item_id
            for item_id, data in WEAPONS.items()
            if data.get("weapon_class") and not attack.damage_is_rollable(data)
        }
        self.assertEqual(
            unrollable, {"club", "cestus", "lasso", "torch_weapon", "bola"}
        )

    def test_a_bola_with_no_damage_at_all_is_refused_rather_than_rolling_zero(self):
        self.character.equipment.append({"item_id": "bola", "quantity": 1})
        self.character.save()
        with self.assertRaises(attack.AttackValidationError):
            self.resolve(ScriptedRoller(d20s=[19]), weapon_index=6)

    def test_a_normal_weapon_is_rollable(self):
        self.assertTrue(attack.damage_is_rollable(WEAPONS["saber"]))

    def test_unknown_armour_tier_is_refused(self):
        with self.assertRaises(attack.AttackValidationError):
            self.resolve(ScriptedRoller(), armour_tier="Adamantine")

    def test_a_named_skill_cannot_change_the_bonus(self):
        """Issue #753: the request used to pick the skill, so Knowledge V added
        +5 to a broadsword. The record decides now; a named skill is not read."""
        self.character.skills = {"knowledge": 15, "weapon (saber)": 1}
        self.character.save()
        named = self.resolve(ScriptedRoller(d20s=[10]), skill_name="knowledge")
        self.assertEqual(named["skill"]["name"], "weapon (saber)")
        self.assertEqual(named["to_hit"]["skill_bonus"], 1)

    def test_unknown_shield_is_refused(self):
        with self.assertRaises(attack.AttackValidationError):
            self.resolve(ScriptedRoller(), shield_item_id="hubcap")

    def test_a_non_shield_catalog_item_is_not_a_shield(self):
        with self.assertRaises(attack.AttackValidationError):
            self.resolve(ScriptedRoller(), shield_item_id="saber")

    def test_a_requested_skill_weapon_is_not_read(self):
        """Which weapon the skill was taken for is the record's, not the
        request's: claiming the saber skill was taken for a longbow changes
        nothing."""
        result = self.resolve(ScriptedRoller(d20s=[10]), skill_weapon_id="longbow")
        self.assertEqual(result["skill"]["weapon_id"], "saber")
        self.assertEqual(result["to_hit"]["skill_bonus"], 3)

    def test_dodge_outside_its_band_is_refused(self):
        for bad in (-1, attack.MAX_DEFENDER_DODGE + 1):
            with self.assertRaises(attack.AttackValidationError):
                self.resolve(ScriptedRoller(), defender_dodge=bad)

    def test_situational_outside_its_band_is_refused(self):
        for bad in (-attack.MAX_SITUATIONAL - 1, attack.MAX_SITUATIONAL + 1):
            with self.assertRaises(attack.AttackValidationError):
                self.resolve(ScriptedRoller(), situational=bad)

    def test_unparseable_situational_is_refused_not_coerced(self):
        with self.assertRaises(attack.AttackValidationError):
            self.resolve(ScriptedRoller(), situational="lots")

    def test_stops_outside_the_band_are_refused(self):
        with self.assertRaises(attack.AttackValidationError):
            self.resolve(ScriptedRoller(), defender_stops=999)

    def test_empty_optional_numbers_mean_zero(self):
        """The panel sends "" for a cleared number input; that is not an error."""
        result = self.resolve(
            ScriptedRoller(d20s=[10]),
            defender_dodge="",
            situational="",
            defender_stops=None,
        )
        self.assertEqual(result["to_hit"]["situational"], 0)
        self.assertEqual(result["defender"]["dodge"], 0)
        self.assertEqual(result["damage"]["stops"], 0)


class AttackArithmeticTest(AttackServiceTestBase):
    def test_target_number_is_itemized_and_sums(self):
        result = self.resolve(
            ScriptedRoller(d20s=[10]),
            armour_tier="Medium",
            shield_item_id="large_shield",
            defender_dodge=2,
        )
        target_number = result["target_number"]
        # Saber is Striking; Striking vs Medium is 16 in the §6 matrix.
        self.assertEqual(target_number["base"], combat.MATRIX["Striking"]["Medium"])
        self.assertEqual(target_number["shield_bonus"], 2)
        self.assertEqual(target_number["defender_dodge"], 2)
        self.assertEqual(
            target_number["total"],
            target_number["base"] + 2 + 2,
        )

    def test_a_skill_taken_for_another_weapon_of_the_class_transfers_half(self):
        """A level-3 Weapon skill taken for the broadsword gives +3 with a
        broadsword and +1 (half, rounded down) with any other Striking weapon
        — the saber here. The record says which weapon (issue #753)."""
        full = self.resolve(ScriptedRoller(d20s=[10]))
        self.character.skills = {"weapon (broadsword)": 6}  # 6 points is level 3
        self.character.save()
        transferred = self.resolve(ScriptedRoller(d20s=[10]))
        self.assertEqual(full["to_hit"]["skill_bonus"], 3)
        self.assertEqual(transferred["skill"]["weapon_id"], "broadsword")
        self.assertEqual(
            transferred["to_hit"]["skill_bonus"], full["to_hit"]["skill_bonus"] // 2
        )

    def test_a_skill_taken_for_another_class_transfers_nothing(self):
        self.character.skills = {"weapon (longbow)": 6}
        self.character.save()
        result = self.resolve(ScriptedRoller(d20s=[10]))
        self.assertEqual(result["to_hit"]["skill_bonus"], 0)
        self.assertIsNone(result["skill"]["name"])

    def test_to_hit_parts_sum_to_the_total(self):
        result = self.resolve(ScriptedRoller(d20s=[10]), situational=3)
        to_hit = result["to_hit"]
        self.assertEqual(
            to_hit["total"],
            to_hit["dex_modifier"]
            + to_hit["skill_bonus"]
            + to_hit["strength_fit_penalty"]
            + to_hit["situational"]
            + to_hit["weapon_mod"],
        )
        self.assertEqual(to_hit["situational"], 3)

    def test_the_selected_items_own_modifier_is_used_not_the_inventory_sum(self):
        """The design's found-in-passing bug must not be inherited.

        Here the great hammer carries +5 and the saber +1; swinging the
        saber must be +1, not +6. This used to assert the sheet's
        ``weapon_to_hit_mod`` was 6, the sum across every weapon carried,
        which was issue #846; the tile now reads the weapon in hand, the
        saber, carried first.
        """
        self.character.equipment[0]["to_hit_mod"] = 1
        self.character.equipment[0]["damage_mod"] = 2
        self.character.equipment[1]["to_hit_mod"] = 5
        self.character.equipment[1]["damage_mod"] = 7
        self.character.save()
        self.assertEqual(self.character.weapon_in_hand_index, 0)
        self.assertEqual(self.character.weapon_to_hit_mod, 1)

        result = self.resolve(ScriptedRoller(d20s=[19], damage=4))
        self.assertEqual(result["to_hit"]["weapon_mod"], 1)
        self.assertEqual(result["damage"]["weapon_mod"], 2)

    def test_a_plain_hit_rolls_damage_once(self):
        roller = ScriptedRoller(d20s=[19], damage=5)
        result = self.resolve(roller, armour_tier="None")
        self.assertTrue(result["attack_roll"]["hit"])
        self.assertEqual(result["attack_roll"]["outcome"], "hit")
        self.assertEqual(result["damage"]["multiplier"], 1)
        self.assertEqual(roller.damage_rolls, 1)
        self.assertEqual(result["damage"]["raw_total"], 5)
        # A plain hit applies to Fatigue first (attack-rolls.md, Damage and
        # Armour): only a severe critical reaches Body. This used to assert
        # ``assertTrue`` here, pinning the #293 bug where every damaging hit
        # was reported as reaching Body.
        self.assertFalse(result["reaches_body"])

    def test_a_miss_rolls_no_damage(self):
        roller = ScriptedRoller(d20s=[2])
        result = self.resolve(roller, armour_tier="Heavy")
        self.assertFalse(result["attack_roll"]["hit"])
        self.assertEqual(result["damage"]["multiplier"], 0)
        self.assertEqual(roller.damage_rolls, 0)
        self.assertEqual(result["damage"]["final"], 0)
        self.assertFalse(result["reaches_body"])

    def test_stops_come_off_the_summed_total_once(self):
        roller = ScriptedRoller(d20s=[20, 20], damage=5)
        result = self.resolve(roller, armour_tier="Medium", defender_stops=3)
        # Severe crit: three rolls of 5 = 15 raw, minus 3 stops once = 12.
        self.assertEqual(
            result["damage"]["multiplier"], combat.SEVERE_CRIT_DAMAGE_ROLLS
        )
        self.assertEqual(roller.damage_rolls, 3)
        self.assertEqual(result["damage"]["raw_total"], 15)
        self.assertEqual(result["damage"]["applied_stops"], 3)
        self.assertEqual(result["damage"]["final"], 12)

    def test_the_hybrid_bypass_halves_stops_for_a_heavy_weapon_vs_heavy_armour(self):
        # Great Hammer is Heavy Striking; index 1.
        result = self.resolve(
            ScriptedRoller(d20s=[19], damage=6),
            weapon_index=1,
            armour_tier="Heavy",
            defender_stops=5,
        )
        self.assertTrue(result["damage"]["hybrid_bypass"])
        self.assertEqual(result["damage"]["applied_stops"], 2)
        self.assertEqual(result["damage"]["final"], 4)

    def test_no_hybrid_bypass_for_a_light_weapon(self):
        result = self.resolve(
            ScriptedRoller(d20s=[19], damage=6), armour_tier="Heavy", defender_stops=5
        )
        self.assertFalse(result["damage"]["hybrid_bypass"])
        self.assertEqual(result["damage"]["applied_stops"], 5)

    def test_the_weapon_damage_modifier_is_added_once_not_per_crit_roll(self):
        """A +2 blade is +2 on a severe crit, not +6: the dice multiply, not the mod."""
        self.character.equipment[0]["damage_mod"] = 2
        self.character.save()
        result = self.resolve(
            ScriptedRoller(d20s=[20, 20], damage=5), armour_tier="None"
        )
        self.assertEqual(result["damage"]["raw_total"], 15 + 2)


class CriticalDamageModifierTest(AttackServiceTestBase):
    """A critical rolls the damage *dice* again; the expression's ±M counts once.

    Issues #865 and #875: the sheet rolled the whole catalog expression on
    every critical repetition, so a saber's ``2d6-2`` counted -2 twice on a
    critical and three times on a severe one. tarmar-engine 0.9.5 fixed the
    battle board (#824); these pin the sheet to the same arithmetic.
    """

    def test_an_ordinary_critical_counts_the_expressions_modifier_once(self):
        roller = FacesRoller(d20s=[20, 2], face=3)
        result = self.resolve(roller, armour_tier="Heavy")
        self.assertEqual(result["damage"]["multiplier"], combat.CRIT_DAMAGE_ROLLS)
        # First roll 2d6-2 = 6 - 2; second is the dice alone, 6. Was 4 + 4 = 8.
        self.assertEqual(roller.expressions, ["2d6-2", "2d6"])
        self.assertEqual(result["damage"]["raw_total"], 10)
        self.assertEqual(result["damage"]["rolls"], [3, 3, 3, 3])

    def test_a_severe_critical_counts_the_expressions_modifier_once(self):
        roller = FacesRoller(d20s=[20, 20], face=3)
        result = self.resolve(roller, armour_tier="None")
        self.assertEqual(
            result["damage"]["multiplier"], combat.SEVERE_CRIT_DAMAGE_ROLLS
        )
        self.assertEqual(roller.expressions, ["2d6-2", "2d6", "2d6"])
        # 4 + 6 + 6, where the old loop made it 4 + 4 + 4 = 12.
        self.assertEqual(result["damage"]["raw_total"], 16)

    def test_a_positive_modifier_is_not_multiplied_either(self):
        # The great hammer is 2d6+2: a critical is 8 + 6, not 8 + 8.
        roller = FacesRoller(d20s=[20, 2], face=3)
        result = self.resolve(roller, weapon_index=1, armour_tier="Heavy")
        self.assertEqual(roller.expressions, ["2d6+2", "2d6"])
        self.assertEqual(result["damage"]["raw_total"], 14)

    def test_the_items_own_modifier_is_still_added_once_beside_it(self):
        self.character.equipment[0]["damage_mod"] = 2
        self.character.save()
        roller = FacesRoller(d20s=[20, 20], face=3)
        result = self.resolve(roller, armour_tier="None")
        self.assertEqual(result["damage"]["raw_total"], 16 + 2)

    def test_a_plain_hit_rolls_the_whole_expression_once(self):
        roller = FacesRoller(d20s=[19], face=3)
        result = self.resolve(roller, armour_tier="None")
        self.assertEqual(roller.expressions, ["2d6-2"])
        self.assertEqual(result["damage"]["raw_total"], 4)

    def test_the_dice_log_says_the_later_rolls_are_dice_only(self):
        roller = FacesRoller(d20s=[20, 2], face=3)
        result = self.resolve(roller, armour_tier="Heavy")
        damage_lines = [
            line for line in result["dice_log"] if line.startswith("damage")
        ]
        self.assertEqual(len(damage_lines), 2)
        self.assertNotIn("dice only", damage_lines[0])
        self.assertIn("dice only: the modifier counts once", damage_lines[1])


class AttackNaturalsTest(AttackServiceTestBase):
    def test_a_natural_twenty_rolls_a_confirmation(self):
        roller = ScriptedRoller(d20s=[20, 18], damage=4)
        result = self.resolve(roller, armour_tier="None")
        self.assertEqual(result["attack_roll"]["outcome"], "severe critical")
        self.assertIsNotNone(result["confirm"])
        self.assertTrue(result["confirm"]["hit"])
        self.assertEqual(result["confirm"]["die"], 18)
        self.assertIn("d20: 18", result["confirm"]["log"])
        self.assertTrue(result["severe"])
        self.assertTrue(result["bleeding"])
        # Severe critical + damage actually rolled: this is the one case
        # where the blow reaches Body as well as Fatigue.
        self.assertGreater(result["damage"]["final"], 0)
        self.assertTrue(result["reaches_body"])

    def test_an_ordinary_critical_does_not_reach_body(self):
        """Confirmed but not severe: damage lands, but only against Fatigue."""
        roller = ScriptedRoller(d20s=[20, 2], damage=4)
        result = self.resolve(roller, armour_tier="None")
        self.assertFalse(result["severe"])
        self.assertGreater(result["damage"]["final"], 0)
        self.assertFalse(result["reaches_body"])

    def test_a_failed_confirmation_stays_an_ordinary_critical(self):
        roller = ScriptedRoller(d20s=[20, 2], damage=4)
        result = self.resolve(roller, armour_tier="Heavy")
        self.assertEqual(result["attack_roll"]["outcome"], "critical")
        self.assertFalse(result["confirm"]["hit"])
        self.assertFalse(result["severe"])
        self.assertFalse(result["bleeding"])
        self.assertEqual(result["damage"]["multiplier"], combat.CRIT_DAMAGE_ROLLS)

    def test_a_natural_one_rolls_the_fumble_subtable(self):
        roller = ScriptedRoller(d20s=[1], d6s=[6])
        result = self.resolve(roller)
        self.assertTrue(result["attack_roll"]["fumble"])
        self.assertEqual(result["fumble"]["key"], "weapon_stress")
        self.assertEqual(result["fumble"]["die"], 6)
        self.assertIn("1d6: 6", result["fumble"]["log"])
        self.assertEqual(result["damage"]["multiplier"], 0)

    def test_a_natural_one_maps_every_fumble_face(self):
        expected = {
            1: "off_balance",
            3: "off_balance",
            4: "drop_weapon",
            6: "weapon_stress",
        }
        for face, key in expected.items():
            result = self.resolve(ScriptedRoller(d20s=[1], d6s=[face]))
            self.assertEqual(result["fumble"]["key"], key)

    def test_a_plain_roll_has_neither_confirmation_nor_fumble(self):
        result = self.resolve(ScriptedRoller(d20s=[11]))
        self.assertIsNone(result["confirm"])
        self.assertIsNone(result["fumble"])


class AttackDiceLogTest(AttackServiceTestBase):
    def test_the_log_names_the_roll_the_bonus_and_the_target_number(self):
        result = self.resolve(
            ScriptedRoller(d20s=[12], damage=4), armour_tier="None", defender_stops=1
        )
        joined = "\n".join(result["dice_log"])
        self.assertIn("d20: 12", joined)
        self.assertIn(f"vs TN {result['target_number']['total']}", joined)
        self.assertIn("armour:", joined)

    def test_the_log_records_the_confirmation_line(self):
        result = self.resolve(ScriptedRoller(d20s=[20, 17], damage=4))
        self.assertTrue(any("confirm" in line for line in result["dice_log"]))

    def test_the_log_records_the_fumble_line(self):
        result = self.resolve(ScriptedRoller(d20s=[1], d6s=[4]))
        self.assertTrue(any("fumble" in line for line in result["dice_log"]))


class AttackLogEntryTest(AttackServiceTestBase):
    def test_the_stored_entry_carries_only_server_decided_values(self):
        result = self.resolve(ScriptedRoller(d20s=[19], damage=5), armour_tier="Light")
        entry = attack.log_entry(result, "swinger", "2026-01-01T00:00:00+00:00")
        self.assertEqual(entry["by"], "swinger")
        self.assertEqual(entry["weapon"], "Saber")
        self.assertEqual(entry["armour_tier"], "Light")
        self.assertEqual(entry["die"], 19)
        self.assertEqual(entry["target_number"], result["target_number"]["total"])
        self.assertEqual(entry["damage"], result["damage"]["final"])
        self.assertEqual(entry["fumble"], "")

    def test_a_fumbled_entry_names_the_subtable_result(self):
        result = self.resolve(ScriptedRoller(d20s=[1], d6s=[5]))
        entry = attack.log_entry(result, "swinger", "2026-01-01T00:00:00+00:00")
        self.assertEqual(entry["fumble"], "Drop weapon")


class AttackDefaultRollerTest(AttackServiceTestBase):
    def test_the_service_rolls_real_dice_when_no_roller_is_injected(self):
        """The production path: ``characters.dice`` is the default argument."""
        result = attack.resolve(
            self.character, {"weapon_index": 0, "armour_tier": "None"}
        )
        self.assertIn(result["attack_roll"]["die"], range(1, combat.DIE_FACES + 1))
