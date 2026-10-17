"""Archetype generation: real Character rows, catalog-respecting loadouts."""

import random
from dataclasses import replace
from unittest import mock

from django.test import SimpleTestCase

from battle import generation
from battle.checks import GENERATOR_CATALOG_CHECK_ID, check_generator_catalog_ids
from battle.generation import (
    ARCHETYPES,
    archetype_for_index,
    deal_equipment,
    generate_character,
    generate_name,
    pick_weapon,
    spell_loadout,
)
from battle.services import snapshot_combatant
from battle.spells import get_spell
from characters.constants import CHARACTER_NAME_PATTERN
from characters.equipment import get_item
from characters.equipment_rules import equipment_rules
from characters.tests.rules_fixtures import (
    DEFAULT_EQUIPMENT_CATALOG,
    DEFAULT_EQUIPMENT_RULES,
    seated_equipment_catalog,
    seated_equipment_rules,
)
from common.test_helpers import BaseTestCase

from .magic_fixtures import rules_with_channel, seated_rules


class GenerateNameTest(BaseTestCase):
    def test_same_seed_draws_the_same_name(self):
        self.assertEqual(
            generate_name(random.Random(7), set()),
            generate_name(random.Random(7), set()),
        )

    def test_consecutive_draws_are_distinct(self):
        rng = random.Random(7)
        used: set[str] = set()
        names = [generate_name(rng, used) for _draw in range(30)]
        self.assertEqual(len(set(names)), 30)
        self.assertEqual(used, set(names))

    def test_a_used_name_is_never_returned(self):
        first = generate_name(random.Random(7), set())
        second = generate_name(random.Random(7), {first})
        self.assertNotEqual(first, second)

    def test_names_are_valid_character_names(self):
        rng = random.Random(3)
        for _draw in range(20):
            name = generate_name(rng, set())
            self.assertRegex(name, CHARACTER_NAME_PATTERN)
            self.assertRegex(name, r"^[A-Z][a-z]+ [A-Z][a-z]+$")

    def test_exhausted_tables_fall_back_to_a_numbered_name(self):
        with (
            mock.patch.object(generation, "_GIVEN_STEMS", ("Al",)),
            mock.patch.object(generation, "_GIVEN_ENDINGS", ("a",)),
            mock.patch.object(generation, "_EPITHETS", ("Vale",)),
        ):
            used: set[str] = set()
            first = generate_name(random.Random(1), used)
            second = generate_name(random.Random(1), used)
        self.assertEqual(first, "Ala Vale")
        self.assertEqual(second, "Ala Vale (2)")


class ArchetypeCycleTest(BaseTestCase):
    def test_round_robin_covers_all_archetypes(self):
        drawn = [archetype_for_index(index) for index in range(len(ARCHETYPES))]
        self.assertEqual(drawn, list(ARCHETYPES))
        self.assertEqual(archetype_for_index(len(ARCHETYPES)), ARCHETYPES[0])

    def test_narrowed_mix_cycles_only_the_selection(self):
        mix = ("archer", "wizard")
        self.assertEqual(archetype_for_index(0, mix), "archer")
        self.assertEqual(archetype_for_index(3, mix), "wizard")


class PickWeaponTest(BaseTestCase):
    def test_respects_str_req(self):
        # STR 10 cannot lift the bastard sword (13) or broadsword (12) or
        # war ax (12); mace needs 11 — the last candidate is taken anyway.
        self.assertEqual(pick_weapon("armoured fighter", 13), "bastard_sword_1h")
        self.assertEqual(pick_weapon("armoured fighter", 12), "broadsword")
        self.assertEqual(pick_weapon("armoured fighter", 10), "mace")

    def test_duelist_falls_back_to_dagger(self):
        self.assertEqual(pick_weapon("unarmoured duelist", 8), "dagger")

    def test_brawler_carries_a_dagger(self):
        # Was test_brawler_has_no_weapon. Under tarmar-engine 0.9.5 bare hands
        # have no plain attack and enter hand-to-hand only by an entry
        # condition, so the brawler could not fight an armed enemy at all.
        # It carries the one weapon the HTH table allows besides bare hands
        # (#867, coordinator's ruling; Spencer may overrule).
        self.assertEqual(pick_weapon("brawler", 14), "dagger")
        self.assertEqual(pick_weapon("brawler", 3), "dagger")


class SpellLoadoutTest(BaseTestCase):
    def test_wizard_loadouts_vary_by_index(self):
        first, first_mana = spell_loadout("wizard", 0)
        second, second_mana = spell_loadout("wizard", 1)
        self.assertNotEqual(first, second)
        self.assertGreater(first_mana, 0)
        self.assertGreater(second_mana, 0)

    def test_non_casters_get_nothing(self):
        self.assertEqual(spell_loadout("archer", 0), ((), 0))

    def test_every_generated_caster_can_cast_each_of_its_spells(self):
        """Issue #960: a generated spell is at a mastery equal to its level.

        Under a Channel of one per recorded spell level, a spell recorded at
        mastery 1 would allow only level-1 spells; at its own level each fits.
        Coordinator's ruling, marked for Spencer.
        """
        cycles = {"wizard": 3, "fighter-mage": 2}
        rules = rules_with_channel(multiplier=1)
        channel = rules.channel
        assert channel is not None
        with seated_rules(rules):
            for archetype, variants in cycles.items():
                for variant in range(variants):
                    generated = generate_character(
                        None, "Casters", variant, archetypes=(archetype,)
                    )
                    seated = snapshot_combatant(generated, 1, (0, 0), 0)
                    for key in generated.spells:
                        spell = get_spell(key)
                        with self.subTest(archetype=archetype, spell=key):
                            self.assertGreaterEqual(
                                channel.channel_for(seated, spell),
                                spell.level,
                            )


class GenerateCharacterTest(BaseTestCase):
    def test_builds_a_real_character_row(self):
        user = self.create_dm()
        generated = generate_character(user, "Test Battle", 0)
        character = generated.character
        self.assertEqual(character.user, user)
        self.assertTrue(character.name.startswith("Test Battle"))
        self.assertFalse(character.is_played)
        self.assertEqual(generated.archetype, "armoured fighter")
        self.assertTrue(character.dice_log)  # rolled, not defaulted
        # Armoured fighter wears the armour its STR bears and carries a
        # weapon it can use. This asserted chainmail and a large shield for
        # any STR, which pinned #883: that kit overloads every generated
        # fighter. The load rule itself is DealEquipmentTest's.
        item_ids = [item["item_id"] for item in character.equipment]
        self.assertIn(character.encumbrance_level, ("unencumbered", "light", "medium"))
        weapon = get_item(item_ids[0])
        # Generated equipment is drawn from the catalog, so the lookup hits.
        self.assertIsNotNone(weapon)
        str_req = (weapon or {}).get("str_req")
        if str_req and item_ids[0] != "mace":
            self.assertGreaterEqual(character.effective_strength, str_req)
        # The weapon skill got points.
        self.assertTrue(character.skills)

    def test_wizard_gets_spells_and_mana(self):
        user = self.create_dm()
        generated = generate_character(user, "Test Battle", 4)  # index 4 = wizard
        self.assertEqual(generated.archetype, "wizard")
        self.assertTrue(generated.spells)
        self.assertGreater(generated.mana, 0)
        self.assertEqual(generated.character.free_spell_points, generated.mana)

    def test_archer_carries_arrows(self):
        user = self.create_dm()
        generated = generate_character(user, "Test Battle", 3)  # index 3 = archer
        self.assertEqual(generated.archetype, "archer")
        item_ids = [item["item_id"] for item in generated.character.equipment]
        self.assertIn("arrows_20", item_ids)

    def test_brawler_trains_its_dagger_and_no_brawling(self):
        # Was test_brawler_trains_brawling: the rules have no such skill, so
        # the grant goes and the brawler's Weapon skill is tied to its dagger
        # the way every other archetype's is (#867).
        user = self.create_dm()
        generated = generate_character(user, "Test Battle", 2)  # brawler
        self.assertEqual(generated.archetype, "brawler")
        self.assertNotIn("brawling", generated.character.skills)
        self.assertIn("weapon (dagger)", generated.character.skills)
        item_ids = [item["item_id"] for item in generated.character.equipment or []]
        self.assertEqual(item_ids, ["dagger"])

    def test_same_archetype_varies_across_the_cycle(self):
        user = self.create_dm()
        first = generate_character(user, "Variation", 4)
        second = generate_character(user, "Variation", 4 + len(ARCHETYPES))
        self.assertEqual(first.archetype, second.archetype)
        self.assertNotEqual(first.spells, second.spells)

    def test_names_are_unique_within_a_battle(self):
        user = self.create_dm()
        names = {
            generate_character(user, "Unique", index).character.name
            for index in range(4)
        }
        self.assertEqual(len(names), 4)

    def test_display_name_is_drawn_from_the_given_rng(self):
        user = self.create_dm()
        first = generate_character(user, "Det A", 0, rng=random.Random(5))
        second = generate_character(user, "Det B", 0, rng=random.Random(5))
        self.assertEqual(first.display_name, second.display_name)
        # The persisted row keeps the battle-name prefix for provenance and
        # carries the fantasy name after it.
        self.assertTrue(first.character.name.startswith("Det A "))
        self.assertIn(first.display_name, first.character.name)

    def test_used_names_are_respected_across_a_battle(self):
        user = self.create_dm()
        taken = generate_character(user, "Clash", 0, rng=random.Random(5)).display_name
        second = generate_character(
            user, "Clash", 1, rng=random.Random(5), used_names={taken}
        )
        self.assertNotEqual(second.display_name, taken)


def _worn(entries: list[dict]) -> list[str]:
    return [entry["item_id"] for entry in entries]


def _weight(entries: list[dict]) -> float:
    return sum(float((get_item(entry["item_id"]) or {})["weight"]) for entry in entries)


class DealEquipmentTest(BaseTestCase):
    """A generated fighter carries what its STR bears (issue #883).

    Every generated armoured fighter used to wear chainmail (30) and a large
    shield (20) beside its weapon: Overloaded ("Walk only, 1 hex max") at any
    STR a character can roll, so it could not Jog, charge or run.
    """

    def test_the_shield_goes_before_the_armour(self):
        # STR 18 bears 36 at Medium: chainmail and a broadsword (35), without
        # the large (20) or small (10) shield.
        self.assertEqual(
            _worn(deal_equipment("armoured fighter", "broadsword", 18)),
            ["broadsword", "chainmail"],
        )

    def test_the_weapon_steps_down_before_the_armour(self):
        # Review ruling on #883: shield, then weapon, then armour. STR 10
        # bears 20: leather (16) and a mace (6) is 22, so the mace gives way
        # to a lighter one-handed Striking weapon STR 10 meets, the hammer
        # (4), before the leather gives way to cloth. (The first ordering
        # dealt cloth and the mace.)
        self.assertEqual(
            _worn(deal_equipment("armoured fighter", "mace", 10)),
            ["hammer", "leather_armor"],
        )
        # STR 9 bears 18: the lightest Striking weapon it meets, the hatchet.
        self.assertEqual(
            _worn(deal_equipment("armoured fighter", "mace", 9)),
            ["hatchet", "leather_armor"],
        )

    def test_each_armour_is_tried_with_the_full_kit_before_it_goes(self):
        # STR 16 bears 32: chainmail with the broadsword is 35, so the
        # shields go, then the weapon steps down to the hatchet (32), which
        # fits, before the chainmail is given up.
        self.assertEqual(
            _worn(deal_equipment("armoured fighter", "broadsword", 16)),
            ["hatchet", "chainmail"],
        )
        # STR 15 bears 30: chainmail cannot be borne with any weapon, so the
        # leather is tried with the shields and the broadsword again. A small
        # shield would make 31; the broadsword stays, not the hatchet.
        self.assertEqual(
            _worn(deal_equipment("armoured fighter", "broadsword", 15)),
            ["broadsword", "leather_armor"],
        )

    def test_a_bow_is_never_stepped_down_to_a_sling(self):
        # The catalog's lighter "bows" are a sling, a boomerang and a rock;
        # an archer keeps its bow and sheds its leather instead.
        dealt = _worn(deal_equipment("archer", "longbow", 10))
        self.assertEqual(dealt[0], "longbow")

    def test_an_archer_sheds_its_leather(self):
        # A longbow and arrows (5) and leather (16) is 21: STR 10 bears 20.
        self.assertEqual(
            _worn(deal_equipment("archer", "longbow", 11)),
            ["longbow", "arrows_20", "leather_armor"],
        )
        self.assertEqual(
            _worn(deal_equipment("archer", "longbow", 10)),
            ["longbow", "arrows_20", "cloth_armor"],
        )

    def test_every_kit_is_the_heaviest_that_fits_at_every_strength(self):
        encumbrance = equipment_rules().encumbrance
        medium = encumbrance.keys.index(generation.HEAVIEST_GENERATED_LOAD)
        for archetype in ARCHETYPES:
            for strength in range(3, 19):
                with self.subTest(archetype=archetype, strength=strength):
                    weapon_id = pick_weapon(archetype, strength)
                    dealt = deal_equipment(archetype, weapon_id, strength)
                    level = encumbrance.level_for(_weight(dealt), strength)
                    full = deal_equipment(
                        archetype, weapon_id, strength, within_load=False
                    )
                    if dealt != full:
                        # Lightened only because the full kit was too heavy.
                        self.assertGreater(
                            encumbrance.keys.index(
                                encumbrance.level_for(_weight(full), strength)
                            ),
                            medium,
                        )
                    # A weapon alone may still be too much for a low STR; the
                    # kit is then as light as it goes: no armour and no
                    # shield, and a weapon of the dealt one's class.
                    if encumbrance.keys.index(level) > medium:
                        carried = [
                            item
                            for item in _worn(dealt)
                            if item not in ("arrows_20", "main_gauche")
                        ]
                        self.assertEqual(len(carried), 1)
                        self.assertEqual(
                            (get_item(carried[0]) or {}).get("weapon_class"),
                            (get_item(weapon_id or "") or {}).get("weapon_class"),
                        )

    def test_the_weapon_skill_is_trained_with_the_weapon_carried(self):
        from battle.generation import generate_character
        from characters.weapon_skill import weapon_skill_name

        for seed in range(1, 21):
            with self.subTest(seed=seed):
                generated = generate_character(
                    None,
                    "Load",
                    0,
                    ("armoured fighter",),
                    stats_roller=generation.stat_roller_for_slot(seed, 0),
                )
                carried = generated.character.equipment[0]["item_id"]
                self.assertIn(weapon_skill_name(carried), generated.character.skills)

    def test_a_weapon_alone_past_medium_still_goes_as_light_as_it_can(self):
        # STR 2 bears 4 at Medium; the mace alone weighs 6. Nothing fits, so
        # the fighter carries the lightest set: the weapon and no protection.
        self.assertEqual(_worn(deal_equipment("armoured fighter", "mace", 2)), ["mace"])

    def test_the_classic_profile_keeps_the_full_kit(self):
        # Classic Melee has no encumbrance: a figure's move comes from its
        # armour alone, so its fighters keep what the archetype names.
        self.assertEqual(
            _worn(deal_equipment("armoured fighter", "mace", 9, within_load=False)),
            ["mace", "chainmail", "large_shield"],
        )

    def test_a_battle_deals_by_its_rules_profile(self):
        from battle.services import create_battle

        dm = self.create_dm()
        for profile, kit_is_full in (("tarmar", False), ("classic-melee", True)):
            with self.subTest(profile=profile):
                battle = create_battle(
                    dm,
                    name=f"Load {profile}",
                    count=2,
                    seed=5,
                    archetypes=("armoured fighter",),
                    rules_profile=profile,
                )
                for combatant in battle.combatants.select_related("character"):
                    worn = [item["item_id"] for item in combatant.character.equipment]
                    self.assertEqual(
                        {"chainmail", "large_shield"} <= set(worn), kit_is_full
                    )
                    if not kit_is_full:
                        self.assertIn(
                            combatant.character.encumbrance_level,
                            ("unencumbered", "light", "medium"),
                        )


class GeneratorNamesWhatTheCanonCarriesTest(SimpleTestCase):
    """The battle's loadouts name catalog ids and an encumbrance level.

    They are the battle's design choices, not the canon's (issue #75): the ids
    each archetype is dealt and steps down through, the arrows dealt with a
    bow and the bows' class, the one hand-to-hand weapon, and the heaviest
    load the generator deals. ``battle.E005`` and ``battle.E004`` hold the
    loaded canon to carrying them, through the one list of refusals
    (:func:`battle.generation.catalog_ids_refused`) this test reads too.
    """

    def test_the_loaded_canon_carries_every_id_the_battle_names(self):
        self.assertEqual(generation.catalog_ids_refused(), [])
        self.assertEqual(check_generator_catalog_ids(), [])

    def test_a_canon_without_one_is_refused_by_the_check(self):
        catalog = DEFAULT_EQUIPMENT_CATALOG
        weapons = {
            item_id: weapon
            for item_id, weapon in catalog.weapons.items()
            if item_id not in ("saber", "arrows_20")
        }
        shields = {
            item_id: shield
            for item_id, shield in catalog.shields.items()
            if item_id != "small_shield"
        }
        without = replace(catalog, weapons=weapons, shields=shields)
        with seated_equipment_catalog(without), seated_equipment_rules():
            refused = generation.catalog_ids_refused()
            (message,) = check_generator_catalog_ids()
        self.assertIn(
            "_WEAPON_CANDIDATES['unarmoured duelist'] names 'saber', which is no "
            "weapon the battle board can seat",
            refused,
        )
        self.assertIn(
            "_SHIELD_STEPS['large_shield'] names 'small_shield', which is no shield",
            refused,
        )
        self.assertIn("_ARROWS names 'arrows_20', which is no weapon", refused)
        self.assertEqual(message.id, GENERATOR_CATALOG_CHECK_ID)
        for sentence in refused:
            self.assertIn(sentence, message.msg)

    def test_the_bows_class_must_be_a_loaded_missile_class(self):
        rules = replace(
            DEFAULT_EQUIPMENT_RULES, missile_classes=frozenset({"Missile — Crossbows"})
        )
        with seated_equipment_catalog(), seated_equipment_rules(rules):
            refused = generation.catalog_ids_refused()
        self.assertEqual(
            refused, [f"_ARROW_CLASS {generation._ARROW_CLASS!r} is no Missile class"]
        )

    def test_the_heaviest_generated_load_is_a_loaded_level(self):
        self.assertIn(
            generation.HEAVIEST_GENERATED_LOAD, equipment_rules().encumbrance.keys
        )
