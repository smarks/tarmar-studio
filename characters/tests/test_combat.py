"""Tests for the d20 combat-resolution engine (``characters.combat``).

Covers the modifier math, the §3.1 under-strength rule, the weapon-vs-armour
Target Numbers, catalog integration, and two drift guards:

  * the published hit-chance surface (no-skill + skill-level-6 grids) is locked to
    exact probabilities, so a matrix edit that shifts balance fails loudly;
  * the spec's §6 markdown table is asserted to match :data:`combat.MATRIX`
    (same pattern as ``test_rules_consistency``).
"""

from pathlib import Path

from django.test import SimpleTestCase

from characters import combat
from characters.equipment import (
    get_armour_tier,
    get_shield_bonus,
    get_weapon_class,
)
from characters.equipment_rules import equipment_catalog
from characters.models import Character
from reference.markdown_tables import split_table_row
from reference.paths import CONTENT_DIR

SPEC = CONTENT_DIR / "proposals" / "d20-combat-resolution-spec.md"

AMMUNITION = {"arrows_20", "quarrels_20"}


class ModifierTests(SimpleTestCase):
    def test_dex_modifier_floors_toward_negative(self):
        self.assertEqual(combat.dex_modifier(10), 0)
        self.assertEqual(combat.dex_modifier(14), 2)
        self.assertEqual(combat.dex_modifier(18), 4)
        self.assertEqual(combat.dex_modifier(9), -1)  # floor((9-10)/2) == -1

    def test_dodge_modifier_floors_at_zero(self):
        self.assertEqual(combat.dodge_modifier(8), 0)
        self.assertEqual(combat.dodge_modifier(16), 3)

    def test_skill_bonus_is_one_per_level(self):
        self.assertEqual(combat.skill_bonus(0), 0)
        self.assertEqual(combat.skill_bonus(1), 1)
        self.assertEqual(combat.skill_bonus(3), 3)
        self.assertEqual(combat.skill_bonus(-1), 0)

    def test_skill_bonus_is_uncapped(self):
        """No ceiling on level or bonus — level N is always +N."""
        for level in (6, 9, 17, 40):
            with self.subTest(level=level):
                self.assertEqual(combat.skill_bonus(level), level)

    def test_same_class_bonus_is_half_the_level_rounded_down(self):
        self.assertEqual(combat.same_class_skill_bonus(0), 0)
        self.assertEqual(combat.same_class_skill_bonus(1), 0)
        self.assertEqual(combat.same_class_skill_bonus(3), 1)
        self.assertEqual(combat.same_class_skill_bonus(4), 2)
        self.assertEqual(combat.same_class_skill_bonus(11), 5)
        self.assertEqual(combat.same_class_skill_bonus(-2), 0)


class WeaponSkillTransferTests(SimpleTestCase):
    """A Weapon skill is tied to one weapon; its class decides what transfers."""

    def test_full_bonus_with_the_trained_weapon(self):
        self.assertEqual(
            combat.weapon_skill_bonus(
                3, skill_weapon_id="broadsword", attack_weapon_id="broadsword"
            ),
            3,
        )

    def test_half_bonus_with_another_weapon_of_the_same_class(self):
        # Broadsword and mace are both Striking: level 3 -> floor(3/2) = +1.
        self.assertEqual(get_weapon_class("broadsword"), get_weapon_class("mace"))
        self.assertEqual(
            combat.weapon_skill_bonus(
                3, skill_weapon_id="broadsword", attack_weapon_id="mace"
            ),
            1,
        )
        self.assertEqual(
            combat.weapon_skill_bonus(
                4, skill_weapon_id="broadsword", attack_weapon_id="war_ax"
            ),
            2,
        )

    def test_no_bonus_across_classes(self):
        # Broadsword (Striking) teaches nothing about a longbow or a dagger.
        self.assertNotEqual(get_weapon_class("broadsword"), get_weapon_class("longbow"))
        self.assertEqual(
            combat.weapon_skill_bonus(
                5, skill_weapon_id="broadsword", attack_weapon_id="longbow"
            ),
            0,
        )
        self.assertEqual(
            combat.weapon_skill_bonus(
                5, skill_weapon_id="broadsword", attack_weapon_id="dagger"
            ),
            0,
        )

    def test_unclassed_items_transfer_nothing(self):
        # Ammunition (and anything off-catalog) has no class to share.
        self.assertEqual(
            combat.weapon_skill_bonus(
                4, skill_weapon_id="arrows_20", attack_weapon_id="quarrels_20"
            ),
            0,
        )
        self.assertEqual(
            combat.weapon_skill_bonus(
                4, skill_weapon_id="__nope__", attack_weapon_id="broadsword"
            ),
            0,
        )

    def test_omitted_weapon_ids_credit_the_skill_in_full(self):
        self.assertEqual(combat.weapon_skill_bonus(4), 4)
        self.assertEqual(combat.weapon_skill_bonus(4, skill_weapon_id="broadsword"), 4)
        self.assertEqual(combat.weapon_skill_bonus(4, attack_weapon_id="mace"), 4)

    def test_to_hit_bonus_applies_the_same_class_half(self):
        # DEX 14 (+2), skill 5 taken on a broadsword, swinging a mace (+2).
        self.assertEqual(
            combat.to_hit_bonus(
                effective_dexterity=14,
                skill_level=5,
                effective_strength=12,
                str_req=11,
                skill_weapon_id="broadsword",
                attack_weapon_id="mace",
            ),
            4,
        )

    def test_to_hit_bonus_gives_nothing_for_a_different_class(self):
        self.assertEqual(
            combat.to_hit_bonus(
                effective_dexterity=14,
                skill_level=5,
                effective_strength=12,
                str_req=11,
                skill_weapon_id="broadsword",
                attack_weapon_id="longbow",
            ),
            2,
        )


class UnderStrengthRuleTests(SimpleTestCase):
    """§3.1: you may always wield, but -1 to hit per point of STR shortfall."""

    def test_no_penalty_when_requirement_met(self):
        self.assertEqual(combat.strength_fit_penalty(12, 12), 0)
        self.assertEqual(
            combat.strength_fit_penalty(16, 12), 0
        )  # excess gives no bonus

    def test_penalty_equals_the_shortfall(self):
        self.assertEqual(combat.strength_fit_penalty(11, 14), -3)
        self.assertEqual(combat.strength_fit_penalty(10, 15), -5)

    def test_no_requirement_never_penalises(self):
        self.assertEqual(combat.strength_fit_penalty(3, None), 0)
        self.assertEqual(combat.strength_fit_penalty(3, 0), 0)

    def test_to_hit_bonus_combines_all_sources(self):
        # STR 11 vs str_req 14 (-3), DEX 12 (+1), skill 0, no situational.
        bonus = combat.to_hit_bonus(
            effective_dexterity=12, skill_level=0, effective_strength=11, str_req=14
        )
        self.assertEqual(bonus, -2)


class TargetNumberTests(SimpleTestCase):
    def test_base_from_matrix(self):
        self.assertEqual(combat.target_number("Piercing", "Heavy"), 22)
        self.assertEqual(combat.target_number("Striking", "Medium"), 16)

    def test_shield_and_dodge_raise_the_wall(self):
        self.assertEqual(
            combat.target_number("Striking", "None", shield_bonus=2, defender_dodge=3),
            13 + 2 + 3,
        )

    def test_unknown_class_or_tier_raises(self):
        with self.assertRaises(KeyError):
            combat.target_number("Nonsense", "Heavy")
        with self.assertRaises(KeyError):
            combat.target_number("Piercing", "Plasteel")


class ResolveTests(SimpleTestCase):
    def test_natural_twenty_always_hits_and_crits(self):
        result = combat.resolve_attack(20, a_target_number=99, bonus=0)
        self.assertTrue(result["hit"])
        self.assertTrue(result["critical"])
        self.assertEqual(result["outcome"], "critical")

    def test_natural_one_always_misses_and_fumbles(self):
        result = combat.resolve_attack(1, a_target_number=2, bonus=10)
        self.assertFalse(result["hit"])
        self.assertTrue(result["fumble"])

    def test_ordinary_hit_and_miss(self):
        self.assertTrue(combat.resolve_attack(12, a_target_number=15, bonus=4)["hit"])
        self.assertFalse(combat.resolve_attack(9, a_target_number=15, bonus=4)["hit"])

    def test_out_of_range_die_raises(self):
        with self.assertRaises(ValueError):
            combat.resolve_attack(0, a_target_number=10, bonus=0)
        with self.assertRaises(ValueError):
            combat.resolve_attack(21, a_target_number=10, bonus=0)

    def test_hit_probability_endpoints(self):
        # TN 11, no bonus: faces 11-20 hit (incl. nat 20) -> 10/20.
        self.assertEqual(combat.hit_probability(11, 0), 0.50)
        # TN 22, no bonus: only the natural 20 lands.
        self.assertEqual(combat.hit_probability(22, 0), 0.05)


class HybridArmourDamageTests(SimpleTestCase):
    """§8 Hybrid: stops apply, but heavy impact weapons halve them vs Heavy."""

    def test_ordinary_weapon_subtracts_full_stops(self):
        self.assertEqual(combat.damage_after_armour(9, 5, "Striking", "Heavy"), 4)

    def test_heavy_weapon_vs_heavy_armour_ignores_half_stops(self):
        # Great hammer 9 vs plate stops 5 -> applies 5//2 = 2 -> 7 (spec §9).
        self.assertEqual(combat.damage_after_armour(9, 5, "Heavy Striking", "Heavy"), 7)
        self.assertEqual(
            combat.damage_after_armour(9, 5, "Heavy Thrusting", "Heavy"), 7
        )

    def test_carveout_only_applies_against_heavy_armour(self):
        # Heavy weapon vs Medium armour gets no break: full stops.
        self.assertEqual(
            combat.damage_after_armour(9, 3, "Heavy Striking", "Medium"), 6
        )

    def test_damage_floored_at_zero(self):
        self.assertEqual(combat.damage_after_armour(2, 5, "Piercing", "Heavy"), 0)


# Published balance surface (spec §6.1). Locked so a matrix edit that moves
# balance fails here. Baseline: aim 0, STR-fit 0, no shield, dodge 0. At +1 per
# skill level the published "+6" grid is skill level 6.
EXPECTED_UNSKILLED = {
    "Piercing": (0.50, 0.35, 0.15, 0.05),
    "Striking": (0.40, 0.35, 0.25, 0.15),
    "Thrusting": (0.45, 0.35, 0.25, 0.10),
    "Heavy Striking": (0.35, 0.35, 0.30, 0.25),
    "Heavy Thrusting": (0.35, 0.35, 0.30, 0.30),
    "Missile — Bows": (0.45, 0.35, 0.20, 0.05),
    "Missile — Crossbows": (0.40, 0.35, 0.30, 0.25),
    "Flexible / Snare": (0.40, 0.25, 0.10, 0.05),
}
EXPECTED_SKILL_SIX = {
    "Piercing": (0.80, 0.65, 0.45, 0.25),
    "Striking": (0.70, 0.65, 0.55, 0.45),
    "Thrusting": (0.75, 0.65, 0.55, 0.40),
    "Heavy Striking": (0.65, 0.65, 0.60, 0.55),
    "Heavy Thrusting": (0.65, 0.65, 0.60, 0.60),
    "Missile — Bows": (0.75, 0.65, 0.50, 0.35),
    "Missile — Crossbows": (0.70, 0.65, 0.60, 0.55),
    "Flexible / Snare": (0.70, 0.55, 0.40, 0.25),
}


class BalanceSurfaceTests(SimpleTestCase):
    def _assert_grid(self, expected, skill_level):
        bonus = combat.skill_bonus(skill_level)
        for weapon_class, row in expected.items():
            for tier, chance in zip(combat.ARMOUR_TIERS, row):
                target = combat.target_number(weapon_class, tier)
                with self.subTest(weapon_class=weapon_class, tier=tier):
                    self.assertAlmostEqual(
                        combat.hit_probability(target, bonus), chance, places=6
                    )

    def test_unskilled_grid(self):
        self._assert_grid(EXPECTED_UNSKILLED, skill_level=0)

    def test_skill_six_grid(self):
        self._assert_grid(EXPECTED_SKILL_SIX, skill_level=6)


class CatalogIntegrationTests(SimpleTestCase):
    def test_every_weapon_has_a_known_class(self):
        for weapon_id in equipment_catalog().weapons:
            if weapon_id in AMMUNITION:
                self.assertIsNone(get_weapon_class(weapon_id))
                continue
            with self.subTest(weapon_id=weapon_id):
                self.assertIn(get_weapon_class(weapon_id), combat.MATRIX)

    def test_every_armour_has_a_known_tier(self):
        for armour_id in equipment_catalog().armour:
            with self.subTest(armour_id=armour_id):
                self.assertIn(get_armour_tier(armour_id), combat.ARMOUR_TIERS)

    def test_every_shield_has_a_bonus(self):
        for shield_id in equipment_catalog().shields:
            with self.subTest(shield_id=shield_id):
                self.assertGreaterEqual(get_shield_bonus(shield_id), 1)

    def test_unknown_item_lookups_are_safe(self):
        self.assertIsNone(get_weapon_class("__nope__"))
        self.assertIsNone(get_armour_tier("__nope__"))
        self.assertEqual(get_shield_bonus("__nope__"), 0)


class ModelPropertyTests(SimpleTestCase):
    def test_dex_modifier_and_dodge_on_character(self):
        character = Character(dexterity=16, aging_dex_penalty=0)
        self.assertEqual(character.dex_modifier, 3)
        self.assertEqual(character.dodge_modifier, 3)

    def test_dodge_floors_at_zero_for_low_dex(self):
        character = Character(dexterity=8, aging_dex_penalty=0)
        self.assertEqual(character.dex_modifier, -1)
        self.assertEqual(character.dodge_modifier, 0)

    def test_unarmored_character_unaffected(self):
        """No equipped armour: combat DEX equals effective DEX."""
        character = Character(dexterity=16, aging_dex_penalty=0)
        self.assertEqual(character.armor_dex_penalty, 0)
        self.assertEqual(character.combat_dexterity, 16)
        self.assertEqual(character.dex_modifier, 3)
        self.assertEqual(character.dodge_modifier, 3)

    def test_armored_character_aim_and_dodge_drop_by_armor_penalty(self):
        """Equipped armour's DEX penalty folds into aim and dodge (attack-rolls.md)."""
        character = Character(dexterity=16, aging_dex_penalty=0)
        character.equipment = [
            {"item_id": "chainmail", "quantity": 1, "in_pack": False, "equipped": True},
        ]
        # Chainmail's DEX penalty is -3 (see test_equipment.test_armor_dex_penalty).
        self.assertEqual(character.armor_dex_penalty, -3)
        # Unarmored DEX 16 -> +3/+3. Armored: combat DEX 13 -> +1/+1.
        self.assertEqual(character.combat_dexterity, 13)
        self.assertEqual(character.dex_modifier, 1)
        self.assertEqual(character.dodge_modifier, 1)
        # effective_dexterity itself must stay armor-free (fatigue/physical/
        # experience-term consumers rely on this).
        self.assertEqual(character.effective_dexterity, 16)

    def test_combined_aging_and_armor_penalty_floors_at_zero(self):
        """Aging + armour DEX penalty combined can't push combat DEX negative."""
        character = Character(dexterity=10, aging_dex_penalty=8)
        character.equipment = [
            {"item_id": "chainmail", "quantity": 1, "in_pack": False, "equipped": True},
        ]
        # effective_dexterity = max(0, 10 - 8) = 2; armor penalty -3 would
        # drive combat_dexterity negative without the floor.
        self.assertEqual(character.effective_dexterity, 2)
        self.assertEqual(character.armor_dex_penalty, -3)
        self.assertEqual(character.combat_dexterity, 0)
        self.assertEqual(character.dex_modifier, -5)
        self.assertEqual(character.dodge_modifier, 0)


def _spec_matrix_table(markdown_path: Path) -> dict[str, dict[str, int]]:
    """Parse the spec's §6 base-Target-Number table out of the markdown.

    Only rows whose label is a known class *and* whose four value cells are
    plain integers are taken — that uniquely selects the §6 table (the §6.1
    grids use ``%`` cells, the §5 table uses weapon-id cells).
    """
    parsed: dict[str, dict[str, int]] = {}
    for raw_line in markdown_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip("*").strip() for cell in split_table_row(line)]
        if len(cells) != 1 + len(combat.ARMOUR_TIERS):
            continue
        label, values = cells[0], cells[1:]
        if label not in combat.MATRIX:
            continue
        try:
            numbers = [int(value) for value in values]
        except ValueError:
            continue
        parsed[label] = dict(zip(combat.ARMOUR_TIERS, numbers))
    return parsed


class SpecDriftGuardTests(SimpleTestCase):
    def test_spec_matrix_matches_code(self):
        parsed = _spec_matrix_table(SPEC)
        self.assertEqual(
            set(parsed),
            set(combat.MATRIX),
            "spec §6 classes drifted from combat.MATRIX",
        )
        self.assertEqual(parsed, combat.MATRIX)


class UnusualWeaponTransferTests(SimpleTestCase):
    """Unusual weapons share no training in either direction."""

    def test_unusual_weapon_gives_full_bonus_with_itself(self):
        self.assertEqual(
            combat.weapon_skill_bonus(
                4, skill_weapon_id="whip", attack_weapon_id="whip"
            ),
            4,
        )

    def test_no_transfer_between_unusual_weapons_sharing_a_class(self):
        # Whip and net are both Flexible / Snare, but Unusual weapons are too
        # unlike one another to share training.
        self.assertEqual(get_weapon_class("whip"), get_weapon_class("net"))
        self.assertEqual(
            combat.weapon_skill_bonus(
                4, skill_weapon_id="whip", attack_weapon_id="net"
            ),
            0,
        )

    def test_no_transfer_between_unusual_and_regular_class_mate(self):
        # Boomerang shares the Missile — Bows class row but is Unusual: no
        # transfer to or from the bows beside it.
        self.assertEqual(get_weapon_class("boomerang"), get_weapon_class("longbow"))
        for trained, attacking in (("boomerang", "longbow"), ("longbow", "boomerang")):
            self.assertEqual(
                combat.weapon_skill_bonus(
                    6, skill_weapon_id=trained, attack_weapon_id=attacking
                ),
                0,
            )

    def test_regular_same_class_transfer_still_half(self):
        self.assertEqual(
            combat.weapon_skill_bonus(
                4, skill_weapon_id="broadsword", attack_weapon_id="mace"
            ),
            2,
        )


class FumbleTableTests(SimpleTestCase):
    """§7's 1d6 fumble subtable (issue #105).

    The table is authored in ``attack-rolls.md``; ``test_rules_consistency``
    guards the wording. These tests pin the code's behaviour on every face.
    """

    def test_every_face_resolves_to_the_documented_band(self):
        expected = {
            1: "off_balance",
            2: "off_balance",
            3: "off_balance",
            4: "drop_weapon",
            5: "drop_weapon",
            6: "weapon_stress",
        }
        for die, key in expected.items():
            with self.subTest(die=die):
                self.assertEqual(combat.fumble_result(die)["key"], key)

    def test_result_carries_the_die_label_and_effect(self):
        result = combat.fumble_result(1)
        self.assertEqual(result["die"], 1)
        self.assertEqual(result["key"], "off_balance")
        self.assertIn("balance", result["label"].lower())
        self.assertIn("next action", result["effect"].lower())

    def test_weapon_stress_names_the_second_fumble(self):
        self.assertIn("second fumble", combat.fumble_result(6)["effect"].lower())

    def test_out_of_range_die_raises(self):
        for die in (0, 7, -1):
            with self.subTest(die=die):
                with self.assertRaises(ValueError):
                    combat.fumble_result(die)

    def test_table_covers_one_through_six_without_gaps(self):
        thresholds = [
            threshold for threshold, _key, _label, _effect in combat.FUMBLE_TABLE
        ]
        self.assertEqual(thresholds, sorted(thresholds))
        self.assertEqual(thresholds[-1], combat.FUMBLE_DIE_SIDES)


class CriticalHitTests(SimpleTestCase):
    """Natural 20: damage rolled twice, three times on a confirmed severe."""

    def test_crit_multiplier_constants(self):
        self.assertEqual(combat.CRIT_DAMAGE_ROLLS, 2)
        self.assertEqual(combat.SEVERE_CRIT_DAMAGE_ROLLS, 3)

    def test_unconfirmed_crit_doubles_the_damage_rolls(self):
        result = combat.resolve_attack(20, a_target_number=15, bonus=0)
        self.assertTrue(result["critical"])
        self.assertEqual(result["damage_multiplier"], combat.CRIT_DAMAGE_ROLLS)
        self.assertFalse(result["severe"])
        self.assertFalse(result["bleeding"])
        self.assertIsNone(result["confirm"])

    def test_confirm_hit_makes_it_severe(self):
        # 15 + 4 = 19 >= TN 17.
        result = combat.resolve_attack(20, 17, 4, confirm_roll=15)
        self.assertTrue(result["confirm"]["hit"])
        self.assertEqual(result["confirm"]["die"], 15)
        self.assertEqual(result["confirm"]["total"], 19)
        self.assertTrue(result["severe"])
        self.assertTrue(result["bleeding"])
        self.assertEqual(result["damage_multiplier"], combat.SEVERE_CRIT_DAMAGE_ROLLS)
        self.assertEqual(result["outcome"], "severe critical")

    def test_confirm_miss_leaves_a_plain_crit(self):
        # 5 + 4 = 9 < TN 17.
        result = combat.resolve_attack(20, 17, 4, confirm_roll=5)
        self.assertFalse(result["confirm"]["hit"])
        self.assertFalse(result["severe"])
        self.assertFalse(result["bleeding"])
        self.assertEqual(result["damage_multiplier"], combat.CRIT_DAMAGE_ROLLS)
        self.assertEqual(result["outcome"], "critical")

    def test_natural_one_on_the_confirm_is_a_plain_failure_to_confirm(self):
        # A bonus that would otherwise clear the TN outright: the natural 1
        # still fails to confirm, and it does NOT fumble.
        result = combat.resolve_attack(20, 2, 30, confirm_roll=1)
        self.assertFalse(result["confirm"]["hit"])
        self.assertFalse(result["severe"])
        self.assertFalse(result["fumble"])
        self.assertIsNone(result["fumble_detail"])
        self.assertEqual(result["damage_multiplier"], combat.CRIT_DAMAGE_ROLLS)

    def test_natural_twenty_on_the_confirm_always_confirms(self):
        result = combat.resolve_attack(20, 99, 0, confirm_roll=20)
        self.assertTrue(result["confirm"]["hit"])
        self.assertTrue(result["severe"])
        self.assertTrue(result["bleeding"])
        self.assertEqual(result["damage_multiplier"], combat.SEVERE_CRIT_DAMAGE_ROLLS)

    def test_out_of_range_confirm_roll_raises(self):
        for confirm in (0, 21):
            with self.subTest(confirm=confirm):
                with self.assertRaises(ValueError):
                    combat.resolve_attack(20, 15, 0, confirm_roll=confirm)

    def test_confirm_at_exactly_the_target_number_confirms(self):
        # Roll-over is >=, so the confirm lands on the nose at TN 17: 13+4.
        on_the_nose = combat.resolve_attack(20, 17, 4, confirm_roll=13)
        self.assertTrue(on_the_nose["confirm"]["hit"])
        self.assertTrue(on_the_nose["severe"])
        # One under is a miss, and the crit stays unconfirmed.
        one_short = combat.resolve_attack(20, 17, 4, confirm_roll=12)
        self.assertFalse(one_short["confirm"]["hit"])
        self.assertFalse(one_short["severe"])

    def test_natural_twenty_reports_the_total(self):
        # #101: `total` was never asserted; a nat 20 still reports die + bonus
        # even though the hit does not depend on it.
        self.assertEqual(combat.resolve_attack(20, 99, 7)["total"], 27)


class FumbleResolutionTests(SimpleTestCase):
    """Natural 1: the fumble subtable is rolled by the caller and passed in."""

    def test_fumble_roll_attaches_the_subtable_result(self):
        result = combat.resolve_attack(1, 10, 5, fumble_roll=6)
        self.assertTrue(result["fumble"])
        self.assertFalse(result["hit"])
        self.assertEqual(result["fumble_detail"]["key"], "weapon_stress")
        self.assertEqual(result["fumble_detail"]["die"], 6)

    def test_fumble_without_a_die_reports_no_subtable_result(self):
        result = combat.resolve_attack(1, 10, 5)
        self.assertTrue(result["fumble"])
        self.assertIsNone(result["fumble_detail"])

    def test_a_fumble_deals_no_damage(self):
        self.assertEqual(
            combat.resolve_attack(1, 10, 5, fumble_roll=1)["damage_multiplier"], 0
        )

    def test_out_of_range_fumble_roll_raises(self):
        with self.assertRaises(ValueError):
            combat.resolve_attack(1, 10, 5, fumble_roll=7)

    def test_out_of_range_fumble_roll_raises_even_when_it_would_be_ignored(self):
        """A bad die is a caller bug whatever the attack rolled."""
        for die in (12, 20):
            with self.subTest(die=die):
                with self.assertRaises(ValueError):
                    combat.resolve_attack(die, 10, 5, fumble_roll=0)

    def test_natural_one_reports_the_total(self):
        self.assertEqual(combat.resolve_attack(1, 2, 10)["total"], 11)


class ResolveBackCompatTests(SimpleTestCase):
    """The 3-arg call and its original keys survive the crit/fumble additions."""

    def test_positional_three_arg_call_still_works(self):
        self.assertEqual(
            combat.resolve_attack(12, 15, 4)["outcome"],
            "hit",
        )

    def test_original_keys_are_all_still_present(self):
        for die in (1, 12, 20):
            with self.subTest(die=die):
                result = combat.resolve_attack(die, 15, 0)
                self.assertLessEqual(
                    {"hit", "total", "critical", "fumble", "outcome"},
                    set(result),
                )

    def test_an_ordinary_hit_rolls_damage_once_and_a_miss_not_at_all(self):
        self.assertEqual(combat.resolve_attack(12, 15, 4)["damage_multiplier"], 1)
        self.assertEqual(combat.resolve_attack(9, 15, 4)["damage_multiplier"], 0)

    def test_ordinary_rolls_ignore_crit_and_fumble_fields(self):
        result = combat.resolve_attack(12, 15, 4)
        self.assertIsNone(result["confirm"])
        self.assertIsNone(result["fumble_detail"])
        self.assertFalse(result["severe"])
        self.assertFalse(result["bleeding"])

    def test_a_confirm_die_is_ignored_off_a_natural_twenty(self):
        """Only a natural 20 can be confirmed — an ordinary hit is not a crit."""
        for die, outcome in ((12, "hit"), (9, "miss"), (1, "fumble")):
            with self.subTest(die=die):
                result = combat.resolve_attack(die, 15, 4, confirm_roll=20)
                self.assertEqual(result["outcome"], outcome)
                self.assertIsNone(result["confirm"])
                self.assertFalse(result["severe"])
                self.assertFalse(result["bleeding"])

    def test_a_fumble_die_is_ignored_off_a_natural_one(self):
        for die in (12, 20):
            with self.subTest(die=die):
                result = combat.resolve_attack(die, 15, 4, fumble_roll=6)
                self.assertFalse(result["fumble"])
                self.assertIsNone(result["fumble_detail"])
