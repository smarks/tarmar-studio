"""Beast → engine adaptation tests (pure: unsaved Beast instances, no DB)."""

import json
import time
from pathlib import Path

from django.test import SimpleTestCase

import bestiary
from battle.adaptation import (
    _DICE_ANYWHERE,
    BEAST_ARCHETYPE,
    NO_MODELLED_ATTACK_NOTE,
    beast_attack,
    beast_gaits,
    beast_movement_modifier,
    no_modelled_attack_name,
    snapshot_beast,
)
from battle.engine.state import WeaponState, bare_handed_damage
from bestiary.models import Beast
from common.test_helpers import make_beast as shared_make_beast

DIRE_WOLF_STATS = {
    "name": "Dire Wolf",
    "slug": "dire-wolf",
    "strength": 14,
    "intelligence": 6,
    "constitution": 14,
    "fatigue_pool": 46,
    "body_pool": 31,
    "armour_tier": "Light",
    "stops": 1,
    "size_hexes": 1,
}


def make_beast(**overrides):
    """An unsaved dire wolf for the adaptation layer."""
    return shared_make_beast(save=False, **{**DIRE_WOLF_STATS, **overrides})


class BeastWeaponTest(SimpleTestCase):
    def test_first_melee_attack_becomes_the_weapon(self):
        weapon = beast_attack(make_beast()).weapon
        self.assertEqual(weapon.name, "Bite")
        self.assertEqual(weapon.weapon_class, "Piercing")
        self.assertEqual(weapon.damage, "1d6+1")
        self.assertFalse(weapon.is_missile)
        self.assertEqual(weapon.item_id, "")

    def test_missile_class_attacks_are_skipped(self):
        # A dragon leads with breath; v1 beasts fight tooth and claw only.
        beast = make_beast(
            attacks=[
                {
                    "name": "Fire breath",
                    "damage": "3d6",
                    "weapon_class": "Missile — Bows",
                },
                {"name": "Bite", "damage": "2d6+2", "weapon_class": "Piercing"},
            ]
        )
        weapon = beast_attack(beast).weapon
        self.assertEqual(weapon.name, "Bite")

    def test_unrollable_damage_is_skipped(self):
        beast = make_beast(
            attacks=[
                {"name": "Venom", "damage": "special", "weapon_class": "Piercing"},
                {"name": "Claw", "damage": "1d6-1", "weapon_class": "Striking"},
            ]
        )
        self.assertEqual(beast_attack(beast).weapon.name, "Claw")

    def test_qualified_damage_rolls_its_leading_dice(self):
        # The catalog keeps qualifiers ("2d6, 2d6+1 in HTH"); the engine
        # rolls the leading expression.
        beast = make_beast(
            attacks=[
                {
                    "name": "Bite/claw",
                    "damage": "2d6, 2d6+1 in HTH",
                    "weapon_class": "Piercing",
                }
            ]
        )
        self.assertEqual(beast_attack(beast).weapon.damage, "2d6")

    def test_no_usable_attack_falls_back_to_bare_handed_by_str(self):
        # special-combat-situations.md's Bare-Handed Damage table by STR.
        beast = make_beast(attacks=[], strength=22)
        attack = beast_attack(beast)
        self.assertTrue(attack.substituted)
        self.assertEqual(attack.weapon.damage, bare_handed_damage(22))
        self.assertEqual(attack.weapon.damage, "1d6+2")

    def test_the_substitution_is_named_rather_than_silent(self):
        """It used to read "tooth and claw" — a name that says nothing about
        the catalog's own damage having been dropped (#645)."""
        attack = beast_attack(make_beast(attacks=[], strength=22))
        self.assertEqual(attack.weapon.name, no_modelled_attack_name(22))
        self.assertIn(NO_MODELLED_ATTACK_NOTE, attack.weapon.name)
        self.assertIn("ST 22", attack.weapon.name)

    def test_a_substituted_beast_keeps_the_class_its_catalog_attack_named(self):
        """The class decides the §6 matrix row and the §8 hybrid-armour
        bypass, so dropping it to the "Striking" default changed how the
        figure fought twice over — not just its dice (#645)."""
        beast = make_beast(
            strength=55,
            attacks=[
                {
                    "name": "Bite",
                    "damage": "per head; see source, p. 92",
                    "weapon_class": "Piercing",
                }
            ],
        )
        attack = beast_attack(beast)
        self.assertTrue(attack.substituted)
        self.assertEqual(attack.weapon.weapon_class, "Piercing")
        self.assertEqual(attack.weapon.damage, bare_handed_damage(55))

    def test_a_beast_with_no_catalog_attack_at_all_takes_the_default_class(self):
        attack = beast_attack(make_beast(attacks=[], strength=20))
        self.assertTrue(attack.substituted)
        self.assertEqual(attack.weapon.weapon_class, WeaponState().weapon_class)

    def test_the_primary_attacks_prose_is_read_for_a_single_expression(self):
        """The hydret's "three heads, 1d6-1 per head" is a rollable attack
        written as prose, not a missing one."""
        beast = make_beast(
            attacks=[
                {
                    "name": "Bite",
                    "damage": "three heads, 1d6-1 per head",
                    "weapon_class": "Piercing",
                }
            ]
        )
        attack = beast_attack(beast)
        self.assertFalse(attack.substituted)
        self.assertEqual(attack.weapon.damage, "1d6-1")
        self.assertEqual(attack.weapon.name, "Bite")

    def test_a_riders_conditional_dice_never_become_the_readied_weapon(self):
        """A spider bites for a flat hit the engine cannot express and then
        *may* poison. Reading dice out of the venom line would ready the
        save's damage as the bite, which is a different creature."""
        beast = make_beast(
            strength=6,
            attacks=[
                {"name": "Bite", "damage": "1 hit", "weapon_class": "Piercing"},
                {
                    "name": "venom",
                    "damage": "save at 4/ST or take additional 1d6+2 hits",
                    "weapon_class": "Piercing",
                },
            ],
        )
        attack = beast_attack(beast)
        self.assertTrue(attack.substituted)
        self.assertEqual(attack.weapon.damage, bare_handed_damage(6))
        self.assertEqual(attack.weapon.weapon_class, "Piercing")

    def test_a_later_entry_with_leading_dice_is_still_preferred(self):
        """The kraken's opening entry is a tentacle grapple with no dice and
        its beak bite is the attack; reading only the primary entry would
        have taken the weapon off it."""
        beast = make_beast(
            attacks=[
                {
                    "name": "Tentacle",
                    "damage": "grapple; 3/ST to break free",
                    "weapon_class": "Flexible / Snare",
                },
                {"name": "beak bite", "damage": "2d6", "weapon_class": "Piercing"},
            ]
        )
        attack = beast_attack(beast)
        self.assertFalse(attack.substituted)
        self.assertEqual(attack.weapon.name, "beak bite")
        self.assertEqual(attack.weapon.damage, "2d6")


class BeastGaitsTest(SimpleTestCase):
    def test_full_move_is_the_run_gait(self):
        # movement.md proportions walk 4 : jog 7 : run 12, scaled.
        self.assertEqual(beast_gaits(12), (4, 7, 12))

    def test_slow_beast_keeps_at_least_one_hex_per_gait(self):
        self.assertEqual(beast_gaits(2), (1, 1, 2))

    def test_rooted_beast_never_moves(self):
        self.assertEqual(beast_gaits(0), (0, 0, 0))

    def test_movement_modifier_is_the_one_the_run_row_implies(self):
        # Coordinator's ruling (#867): Run = 12 + modifier, the full move is
        # the run, so a 16-hex beast is +4 and a 6-hex one -6.
        self.assertEqual(beast_movement_modifier(16), 4)
        self.assertEqual(beast_movement_modifier(12), 0)
        self.assertEqual(beast_movement_modifier(6), -6)

    def test_a_seated_beast_has_no_sprint_and_its_modifier(self):
        combatant = snapshot_beast(make_beast(movement=16), 1, "Wolf", (0, 0), 0)
        self.assertEqual(combatant.move_sprint, 0)
        self.assertEqual(combatant.movement_modifier, 4)


class SnapshotBeastTest(SimpleTestCase):
    def test_snapshot_freezes_the_beast_into_engine_state(self):
        beast = make_beast()
        combatant = snapshot_beast(beast, 9, "Dire Wolf 2", (3, -2), 4)
        self.assertEqual(combatant.combatant_id, 9)
        self.assertEqual(combatant.name, "Dire Wolf 2")
        self.assertEqual(combatant.archetype, BEAST_ARCHETYPE)
        self.assertTrue(combatant.is_beast)
        self.assertEqual(combatant.position, (3, -2))
        self.assertEqual(combatant.facing, 4)
        self.assertEqual(combatant.strength, 14)
        self.assertEqual(combatant.dexterity, 14)
        self.assertEqual(combatant.constitution, 14)
        self.assertEqual(combatant.max_fatigue, 46)
        self.assertEqual(combatant.fatigue, 46)
        self.assertEqual(combatant.max_body, 31)
        self.assertEqual(combatant.body, 31)
        self.assertEqual(combatant.armour_tier, "Light")
        self.assertEqual(combatant.stops, 1)
        self.assertEqual(combatant.shield_bonus, 0)
        self.assertEqual(combatant.weapon.name, "Bite")
        self.assertEqual(combatant.weapon_skill_level, 0)
        self.assertEqual(
            (combatant.move_walk, combatant.move_jog, combatant.move_run),
            (4, 7, 12),
        )
        self.assertEqual(combatant.size_hexes, 1)

    def test_beasts_carry_no_magic(self):
        combatant = snapshot_beast(make_beast(), 1, "Wolf", (0, 0), 0)
        self.assertEqual(combatant.max_mana, 0)
        self.assertEqual(combatant.mana, 0)
        self.assertEqual(combatant.spells, [])

    def test_multi_hex_size_survives_the_snapshot_and_serialization(self):
        beast = make_beast(name="Young Dragon", size_hexes=4)
        combatant = snapshot_beast(beast, 2, "Young Dragon", (0, 0), 0)
        self.assertEqual(combatant.size_hexes, 4)
        self.assertEqual(len(combatant.footprint), 3)  # 4 rounds to the triangle
        from battle.engine.state import BattleState

        state = BattleState(combatants=[combatant])
        rebuilt = BattleState.from_dict(state.to_dict())
        self.assertEqual(rebuilt.combatants[0].size_hexes, 4)
        self.assertTrue(rebuilt.combatants[0].is_beast)


class GaitProportionsSourceTest(SimpleTestCase):
    def test_proportions_follow_the_character_gait_table(self):
        """The loaded canon's slowest and second-slowest gaits to its second
        fastest, which the engine seats as walk, jog and run (#75)."""
        from battle.adaptation import gait_proportions
        from characters.equipment_rules import equipment_rules

        gaits = equipment_rules().gaits
        distances = gaits.base_distances
        run = distances[gaits.order[1]]
        self.assertEqual(
            gait_proportions(),
            {
                "walk": distances[gaits.order[-1]] / run,
                "jog": distances[gaits.order[-2]] / run,
            },
        )


class CatalogSubstitutionDriftTest(SimpleTestCase):
    """How much of the shipped catalog fights on a substituted attack (#645).

    The count was 53 of 239 when the issue was filed, and nothing said so
    anywhere: the muster printed the category and the ST, the roster card
    printed "beast — tooth and claw", and a DM who mustered a Troll got a
    figure that did not fight like the Troll on the catalog page. Three of
    those 53 turned out to be parsing faults rather than gaps in the source
    (the arachne's and the pig's parenthesised damage, and the hydret's
    prose), so 50 remain — every one of them a beast the source extract
    genuinely gives no rollable damage for.

    This list is pinned by name rather than counted, so a change that adds
    a beast to it says *which* beast in the failure. Growing it is allowed;
    growing it unnoticed is what this stops.
    """

    substituted_beasts = frozenset(
        {
            "Air Elemental",
            "Am Bush",
            "Berserker Wasp",
            "Black Giant Ant Queen",
            "Bloodtree",
            "Brown Giant Ant Queen",
            "Brown Slime",
            "Bullfrog",
            "Cannon Flower",
            "Common Earth Elemental",
            "Common Leech",
            "Common Man o’ War",
            "Common Rat",
            "Common Scorpion",
            "Common Wasp",
            "Fire Ant",
            "Florawing",
            "Giant Leech",
            "Giant Spider (Adult)",
            "Giant Spider (Baby)",
            "Giant Wasp",
            "Gorilla",
            "Green Slime",
            "Head Fly",
            "Large Gate Spider",
            "Large Hydra",
            "Large Shadow Shark",
            "Lesser Parrot",
            "Mana Sponge",
            "Mandrake",
            "Minipus",
            "Monstrous Snapping Turtle",
            "Piranhakeet",
            "Pit Trap Plant",
            "Plocker",
            "Red Giant Ant Queen",
            "Red Giant Ant Worker",
            "Red Slime",
            "Scuttle",
            "Shadow Skate",
            "Silver Slime",
            "Small Bat",
            "Small Gate Spider",
            "Stonemason",
            "Troll",
            "Vampire Bat",
            "Vampire Man o’ War",
            "Venomous Snake",
            "Venomous Spider",
            "Water Form",
        }
    )

    @staticmethod
    def catalog_beasts() -> list[Beast]:
        """The shipped fixture as unsaved rows — no DB, so this stays pure."""
        fixture = json.loads(
            (
                Path(bestiary.__file__).resolve().parent / "fixtures" / "beasts.json"
            ).read_text()
        )
        return [Beast(**record["fields"]) for record in fixture]

    def test_exactly_the_pinned_beasts_fight_on_a_substituted_attack(self):
        substituted = {
            beast.name
            for beast in self.catalog_beasts()
            if beast_attack(beast).substituted
        }
        self.assertEqual(
            substituted - self.substituted_beasts,
            set(),
            "a catalog beast newly lost its modelled attack",
        )
        self.assertEqual(
            self.substituted_beasts - substituted,
            set(),
            "a catalog beast gained a modelled attack — drop it from the list",
        )

    def test_every_substituted_beast_says_so_in_its_weapons_name(self):
        for beast in self.catalog_beasts():
            attack = beast_attack(beast)
            if attack.substituted:
                self.assertIn(NO_MODELLED_ATTACK_NOTE, attack.weapon.name, beast.name)
            else:
                self.assertNotIn(
                    NO_MODELLED_ATTACK_NOTE, attack.weapon.name, beast.name
                )

    def test_a_substituted_beast_never_loses_a_class_its_catalog_named(self):
        """Dropping the class to the default silently moved the figure to a
        different §6 matrix row and a different §8 armour interaction."""
        for beast in self.catalog_beasts():
            attack = beast_attack(beast)
            if not attack.substituted:
                continue
            melee = [
                entry
                for entry in beast.attacks or []
                if (entry.get("weapon_class") or "")
                and not entry["weapon_class"].startswith("Missile")
            ]
            if melee:
                self.assertEqual(
                    attack.weapon.weapon_class, melee[0]["weapon_class"], beast.name
                )


class DiceAnywherePatternTest(SimpleTestCase):
    """The catalog's dice, found anywhere in a damage string (issue #44).

    The text is the bestiary catalog's. These pin what the pattern finds, so
    the rewrite changed how long it takes and nothing else.
    """

    def test_dice_are_found_wherever_they_sit(self):
        cases = {
            "three heads, 1d6-1 per head": ["1d6-1"],
            "2d6": ["2d6"],
            "1d6 + 2 and 3d6": ["1d6 + 2", "3d6"],
            "1d6 +  1": ["1d6 +  1"],
            "12d10 - 3 hits": ["12d10 - 3"],
            "save or take 2d6, 1d6+2": ["2d6", "1d6+2"],
            "2d6+": ["2d6"],
            "12345d6": ["12345d6"],
            "x2d6": ["2d6"],
        }
        for damage, expected in cases.items():
            with self.subTest(damage=damage):
                self.assertEqual(_DICE_ANYWHERE.findall(damage), expected)

    def test_text_without_a_whole_expression_finds_none(self):
        for damage in ("d6", "2d", "2 d 6", "1 hit", "1d", "dd6", ""):
            with self.subTest(damage=damage):
                self.assertEqual(_DICE_ANYWHERE.findall(damage), [])

    def test_a_catalog_attack_of_prose_is_still_read(self):
        beast = make_beast(
            attacks=[
                {
                    "name": "Bite",
                    "damage": "three heads, 1d6 - 1 per head",
                    "weapon_class": "Piercing",
                }
            ]
        )
        self.assertEqual(beast_attack(beast).weapon.damage, "1d6-1")

    def test_the_pattern_stays_fast_on_crafted_input(self):
        # The tests above check what the pattern finds; this one checks how
        # long it takes. The old pattern took about 10 s on each digit run
        # below (not the guard; measured on one machine, 2026-10-18), so code
        # that backtracks again fails here within the minute while the new
        # pattern takes milliseconds. The last case goes through beast_attack,
        # which is how a catalog entry reaches the pattern.
        crafted = {
            "a run of digits": "1" * 120_000,
            "a run of digits then d": "1" * 120_000 + "d",
            "a run of digits then d and a sign": "1" * 120_000 + "d+",
            "guard: dice then spaces": "1d6" + " " * 120_000,
        }
        for label, damage in crafted.items():
            with self.subTest(input=label):
                started = time.perf_counter()
                _DICE_ANYWHERE.findall(damage)
                self.assertLess(time.perf_counter() - started, 1.0)
        beast = make_beast(
            attacks=[
                {"name": "Bite", "damage": "1" * 120_000, "weapon_class": "Piercing"}
            ]
        )
        started = time.perf_counter()
        attack = beast_attack(beast)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(attack.substituted)
