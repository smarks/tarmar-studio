"""The sheet repaints what a change moves, and the small sheet fixes beside it.

Issues #763, #764, #765, #766, #792 (the stale-repaint class: a response to a
change made without a reload carries what changed, and the page's painter
writes it), #800 (stacked equipment past the import cap), #804 and #846 (the
shield in use and the weapon in hand), #805 (nonsense heights and weights) and
#851 (buying before the wealth roll). The browser half of each repaint is in
static/js/__tests__/character-edit.test.js and e2e/character-sheet-repaints.spec.js.
"""

import json
import re
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from battle.services import _seated_arms, _shield_bonus
from character_export.export_utils import generate_character_json
from characters import services
from characters.import_service import CharacterImportError, validate_import_payload
from characters.models import Character
from characters.purse import WEALTH_FIRST_REFUSAL
from characters.tests.test_incomplete_characters import incomplete_character
from characters.views.characters import sheet_repaint
from common.test_helpers import BaseTestCase, create_character


def page_config(page: str) -> dict:
    """The sheet's character-edit-config block, parsed."""
    match = re.search(
        r'<script id="character-edit-config" type="application/json">(.*?)</script>',
        page,
        re.S,
    )
    assert match is not None
    return json.loads(match.group(1))


class SheetTestCase(BaseTestCase):
    """A Game Master's session on one character, and helpers for its endpoints."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("sheet-owner")
        self.game_master = self.create_dm("sheet-gm")
        self.character = create_character(self.owner)
        self.login_as(self.game_master)

    def as_player(self) -> None:
        """Sign in as the character's owner, keeping the passphrase gate passed."""
        self.logout()
        self.login_as(self.owner)

    def url(self, name: str, character: Character | None = None) -> str:
        return reverse(f"characters:{name}", args=[(character or self.character).pk])

    def update(self, field: str, value: object, character: Character | None = None):
        return self.post_json(
            self.url("update_field", character), {"field": field, "value": value}
        )


class AttributeEditMovesMovementTest(SheetTestCase):
    """#763: STR, DEX and CON move encumbrance, movement and carrying capacity."""

    def test_each_of_str_dex_con_carries_the_movement_keys(self):
        for field, value in (("strength", 9), ("dexterity", 15), ("constitution", 8)):
            with self.subTest(field=field):
                data = self.update(field, value).json()
                self.character.refresh_from_db()
                for key in (
                    "encumbrance_level",
                    "encumbrance_label",
                    "encumbrance_badge_classes",
                    "movement_penalty",
                    "movement_restriction_label",
                    "movement_modifier",
                    "gait_readout",
                    "lightest_load_limit",
                ):
                    self.assertIn(key, data)
                self.assertEqual(
                    data["movement_modifier"], self.character.movement_modifier
                )
                self.assertEqual(
                    data["lightest_load_limit"], self.character.lightest_load_limit
                )
                self.assertEqual(data["gait_readout"], self.character.gait_readout)
                self.assertEqual(
                    data["encumbrance_label"], self.character.encumbrance_label
                )

    def test_a_lower_strength_is_heavier_load_in_the_response(self):
        self.character.equipment = [{"item_id": "tower_shield", "quantity": 1}]
        self.character.save()
        before = self.update("strength", 18).json()["encumbrance_level"]
        after = self.update("strength", 3).json()["encumbrance_level"]
        self.assertNotEqual(before, after)
        self.assertEqual(
            after, Character.objects.get(pk=self.character.pk).encumbrance_level
        )

    def test_a_field_that_moves_nothing_physical_sends_no_movement(self):
        data = self.update("charisma", 11).json()
        self.assertNotIn("movement_modifier", data)

    def test_a_run_of_years_sends_weight_and_carrying_capacity(self):
        response = self.post_json(
            self.url("add_years"), {"track": "laborer", "years": 1}
        )
        data = response.json()
        self.character.refresh_from_db()
        self.assertEqual(data["character"]["weight_lbs"], self.character.weight_lbs)
        self.assertEqual(
            data["movement"]["lightest_load_limit"],
            self.character.lightest_load_limit,
        )

    def test_the_page_renders_the_capacity_the_painter_writes(self):
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn(
            f'<span id="max-unencumbered">{self.character.lightest_load_limit}</span>',
            page,
        )


class CompletionRepaintTest(SheetTestCase):
    """#764: completing a character on the sheet shows what completion unlocks."""

    def setUp(self):
        super().setUp()
        self.character = incomplete_character(
            self.owner,
            strength=12,
            dexterity=12,
            intelligence=12,
            wisdom=12,
            constitution=12,
        )

    def test_the_last_attribute_answers_complete_with_the_export_links(self):
        self.character.fatigue_roll = 7
        self.character.save()
        data = self.update("charisma", 10).json()
        self.assertTrue(data["is_complete"])
        self.assertIn("Export PDF", data["completion_html"])
        self.assertNotIn("incomplete-marks", data["completion_html"])
        self.assertEqual(data["incomplete_marks_html"].strip(), "")

    def test_an_edit_that_leaves_a_part_missing_names_what_is_left(self):
        data = self.update("charisma", 10).json()
        self.assertFalse(data["is_complete"])
        self.assertIn("needs Fatigue roll", data["completion_html"])
        self.assertNotIn("needs attributes", data["incomplete_marks_html"])

    def test_the_fatigue_roll_that_completes_it_says_so(self):
        self.character.charisma = 10
        self.character.save()
        data = self.client.post(self.url("roll_fatigue")).json()
        self.assertTrue(data["is_complete"])
        self.assertIn("Export Markdown", data["completion_html"])
        self.assertIn("fatigue_roll", data["recent_changes_html"])

    def test_the_attack_form_and_skill_check_are_rendered_hidden_until_then(self):
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn('<div id="attack-ready" hidden>', page)
        self.assertIn('id="attack-form"', page)
        self.assertIn('data-result="skill-check-result" hidden>', page)
        self.assertIn('<div id="sheet-completion">', page)

    def test_a_complete_character_renders_them_shown(self):
        complete = create_character(self.owner, name="Whole")
        page = self.client.get(self.url("edit", complete)).content.decode()
        self.assertIn('<div id="attack-ready">', page)
        self.assertIn('id="attack-incomplete" hidden>', page)
        self.assertIn('data-result="skill-check-result">', page)

    def test_a_player_whose_last_bought_score_closes_the_point_buy_is_told(self):
        self.as_player()
        buyer = incomplete_character(self.owner, name="Buyer")
        data = self.update("strength", 13, buyer).json()
        self.assertTrue(data["point_buy_open"])


class SkillCheckOptionsTest(SheetTestCase):
    """#765: the options a response sends follow the skills held.

    The Attack panel's own skill select went with #753 (b2ec6e0); the skill
    check's select was the remaining instance of the same stale list.
    """

    def test_a_rename_sends_the_new_name_and_not_the_old(self):
        self.character.skills = {"herbalism": 3}
        self.character.save()
        data = self.post_json(
            self.url("rename_skill"), {"old_name": "herbalism", "new_name": "herb lore"}
        ).json()
        self.assertIn('value="herb lore"', data["skill_check_options_html"])
        self.assertNotIn('value="herbalism"', data["skill_check_options_html"])

    def test_an_allocation_sends_the_new_skill(self):
        self.character.free_skill_points = 2
        self.character.save()
        data = self.post_json(self.url("allocate_skill"), {"skill": "herbalism"}).json()
        self.assertIn('value="herbalism"', data["skill_check_options_html"])

    def test_the_page_and_the_response_render_the_same_options(self):
        page = self.client.get(self.url("edit")).content.decode()
        data = self.update("charisma", 9).json()
        for line in data["skill_check_options_html"].strip().splitlines():
            self.assertIn(line.strip(), page)


class IncompleteEquipmentTest(SheetTestCase):
    """#766: an incomplete character's equipment answers carry nulls, not errors."""

    def test_adding_an_item_answers_with_unknown_movement(self):
        character = incomplete_character(self.owner)
        response = self.post_json(
            self.url("equipment_add", character), {"item_id": "backpack", "free": True}
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertIsNone(data["encumbrance_level"])
        self.assertIsNone(data["movement_modifier"])
        self.assertIsNone(data["lightest_load_limit"])
        self.assertEqual(len(data["equipment"]), 1)


class PointControlsAndLogsTest(SheetTestCase):
    """#792: controls rendered hidden rather than left out, and the live logs."""

    def test_a_sheet_with_no_points_renders_the_spending_controls_hidden(self):
        self.as_player()
        self.character.free_skill_points = 0
        self.character.free_spell_points = 0
        self.character.save()
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn('<div class="add-skill-control" style="display: none;">', page)
        self.assertIn('id="spell-points-item" hidden>', page)
        self.assertIn('class="learn-spell needs-free-points"', page)

    def test_a_game_master_always_has_the_teach_control(self):
        self.character.free_skill_points = 0
        self.character.save()
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn(
            '<div class="learn-spell" data-mechanics-form="learnSpellUrl" data-reload="true">',
            page,
        )

    def test_every_change_sends_the_recent_changes_rows(self):
        data = self.update("charisma", 9).json()
        self.assertIn("charisma", data["recent_changes_html"])
        equipment = self.post_json(
            self.url("equipment_add"), {"item_id": "backpack", "free": True}
        )
        self.assertEqual(equipment.status_code, 200, equipment.content)
        self.assertIn("purse", equipment.json()["recent_changes_html"])

    def test_a_purchase_sends_the_spent_line_with_the_purse(self):
        data = self.post_json(self.url("equipment_add"), {"item_id": "dagger"}).json()
        self.character.refresh_from_db()
        from characters.purse import format_coin

        self.assertEqual(
            data["coin_adjustment"], format_coin(self.character.coin_adjustment)
        )
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn(
            f'<td id="coin-adjustment">{data["coin_adjustment"]} gp</td>', page
        )
        self.assertIn(
            f'<td id="wealth-earned">{self.character.money_earned} gp</td>', page
        )

    def test_an_attack_sends_its_log_line(self):
        self.character.equipment = [{"item_id": "saber", "quantity": 1}]
        self.character.save()
        data = self.post_json(
            self.url("attack"), {"weapon_index": 0, "armour_tier": "None"}
        ).json()
        self.assertIn('class="combat-log-entry"', data["combat_log_entry_html"])
        self.assertIn("Saber", data["combat_log_entry_html"])

    def test_the_empty_logs_render_hidden_for_the_first_line_to_show(self):
        fresh = create_character(self.owner, name="Fresh", history=[], dice_log="")
        page = self.client.get(self.url("edit", fresh)).content.decode()
        self.assertIn('<div id="dice-log" class="stats-section dice-log" hidden>', page)
        self.assertIn('<ul class="combat-log-list" id="combat-log-list">', page)
        self.assertIn('<p class="no-combat-log">No attacks resolved yet.</p>', page)

    def test_the_party_edit_page_marks_every_place_the_name_shows(self):
        from parties.models import Party

        party = Party.objects.create(name="The Band", owner=self.game_master)
        page = self.client.get(
            reverse("parties:edit", args=[party.pk])
        ).content.decode()
        self.assertEqual(page.count("<span data-name-chrome>The Band</span>"), 2)
        self.assertIn("paintNameChrome(data.value", page)


class ShieldInUseTest(SheetTestCase):
    """#804: one shield, carried outside the pack, counts."""

    def test_two_shields_do_not_stack(self):
        self.character.equipment = [
            {"item_id": "small_shield", "quantity": 1},
            {"item_id": "tower_shield", "quantity": 1},
        ]
        self.character.save()
        self.assertEqual(self.character.shield_in_use_index, 0)
        self.assertEqual(self.character.armor_stops, 1)
        self.assertEqual(self.character.armor_dex_penalty, 0)
        self.assertEqual(_shield_bonus(self.character), 1)

    def test_a_shield_in_a_dropped_pack_counts_for_nothing(self):
        self.character.equipment = [
            {"item_id": "tower_shield", "quantity": 1, "in_pack": True},
            {"item_id": "large_shield", "quantity": 1, "in_pack": True},
        ]
        self.character.pack_dropped = True
        self.character.save()
        self.assertIsNone(self.character.shield_in_use_index)
        self.assertEqual(self.character.armor_stops, 0)
        self.assertEqual(self.character.armor_dex_penalty, 0)
        self.assertEqual(
            self.character.combat_dexterity, self.character.effective_dexterity
        )
        self.assertEqual(_shield_bonus(self.character), 0)

    def test_readying_another_shield_puts_the_first_away(self):
        self.character.equipment = [
            {"item_id": "small_shield", "quantity": 1},
            {"item_id": "tower_shield", "quantity": 1, "in_pack": True},
        ]
        self.character.save()
        data = self.post_json(
            self.url("equipment_update"), {"index": 1, "equipped": True}
        ).json()
        self.assertEqual(data["shield_in_use_index"], 1)
        self.assertEqual(data["armor_stops"], 3)
        self.assertFalse(data["equipment"][1]["in_pack"])
        self.assertFalse(data["equipment"][0]["equipped"])

    def test_a_two_handed_weapon_leaves_no_hand_for_a_players_shield(self):
        self.as_player()
        self.character.equipment = [
            {"item_id": "great_hammer", "quantity": 1},
            {"item_id": "large_shield", "quantity": 1},
        ]
        self.character.save()
        self.assertIsNone(self.character.shield_in_use_index)
        response = self.post_json(
            self.url("equipment_update"), {"index": 1, "equipped": True}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("both hands", response.json()["error"])

    def test_a_player_cannot_ready_a_shield_from_a_dropped_pack(self):
        self.as_player()
        self.character.equipment = [
            {"item_id": "small_shield", "quantity": 1, "in_pack": True}
        ]
        self.character.pack_dropped = True
        self.character.save()
        response = self.post_json(
            self.url("equipment_update"), {"index": 0, "equipped": True}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("dropped pack", response.json()["error"])

    def test_a_player_readies_their_own_shield(self):
        self.as_player()
        self.character.equipment = [
            {"item_id": "small_shield", "quantity": 1},
            {"item_id": "large_shield", "quantity": 1},
        ]
        self.character.save()
        response = self.post_json(
            self.url("equipment_update"), {"index": 1, "equipped": True}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["shield_in_use_index"], 1)

    def test_worn_armour_still_counts_beside_the_shield(self):
        self.character.equipment = [
            {"item_id": "small_shield", "quantity": 1},
            {"item_id": "leather_armor", "quantity": 1, "equipped": True},
        ]
        self.character.save()
        armour_only = create_character(
            self.owner,
            equipment=[{"item_id": "leather_armor", "quantity": 1, "equipped": True}],
        )
        self.assertEqual(self.character.armor_stops, armour_only.armor_stops + 1)


class WeaponInHandTest(SheetTestCase):
    """#846: the tiles, the Attack panel and the board read the weapon in hand."""

    def setUp(self):
        super().setUp()
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1, "to_hit_mod": 2, "damage_mod": 1},
            {"item_id": "dagger", "quantity": 1, "to_hit_mod": 1, "damage_mod": 3},
        ]
        self.character.save()

    def test_the_tiles_read_one_weapon_not_the_sum(self):
        self.assertEqual(self.character.weapon_in_hand_index, 0)
        self.assertEqual(self.character.weapon_to_hit_mod, 2)
        self.assertEqual(self.character.weapon_damage_mod, 1)

    def test_readying_the_dagger_moves_the_tiles_the_panel_and_the_board(self):
        data = self.post_json(
            self.url("equipment_update"), {"index": 1, "equipped": True}
        ).json()
        self.assertEqual(data["weapon_in_hand_index"], 1)
        self.assertEqual(data["weapon_to_hit_mod"], 1)
        self.assertEqual(data["weapon_damage_mod"], 3)
        in_hand = [
            weapon["item_id"] for weapon in data["attack_weapons"] if weapon["in_hand"]
        ]
        self.assertEqual(in_hand, ["dagger"])
        self.character.refresh_from_db()
        weapon = _seated_arms(self.character).weapon
        self.assertEqual(weapon.item_id, "dagger")

    def test_packing_the_weapon_in_hand_puts_it_down(self):
        self.post_json(self.url("equipment_update"), {"index": 1, "equipped": True})
        data = self.post_json(
            self.url("equipment_update"), {"index": 1, "in_pack": True}
        ).json()
        self.assertFalse(data["equipment"][1]["equipped"])
        self.assertEqual(data["weapon_in_hand_index"], 0)

    def test_nothing_carried_outside_the_pack_is_nothing_in_hand(self):
        for item in self.character.equipment:
            item["in_pack"] = True
        self.character.save()
        self.assertIsNone(self.character.weapon_in_hand_index)
        self.assertEqual(self.character.weapon_to_hit_mod, 0)
        # The board still seats a usable weapon it finds, as it always has.
        weapon = _seated_arms(self.character).weapon
        self.assertEqual(weapon.item_id, "broadsword")

    def test_the_page_selects_the_weapon_in_hand_in_the_attack_panel(self):
        self.character.equipment[1]["equipped"] = True
        self.character.save()
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn('data-item-id="dagger" selected>', page)
        self.assertIn("— in hand</option>", page)


class StackedEquipmentCapTest(BaseTestCase):
    """#800: a line of equipment never holds more than the importer accepts."""

    def setUp(self):
        super().setUp()
        self.character = create_character(self.create_user("stacker"))

    def test_two_gifts_of_999_are_refused_past_the_cap(self):
        services.apply_equipment(self.character, "arrows", 999)
        with self.assertRaises(services.CharacterChangeError):
            services.apply_equipment(self.character, "arrows", 999)
        self.assertEqual([item["quantity"] for item in self.character.equipment], [999])
        self.character.save()
        validate_import_payload(json.loads(generate_character_json(self.character)))

    def test_stacking_up_to_the_cap_is_allowed(self):
        services.apply_equipment(self.character, "arrows", 500)
        services.apply_equipment(self.character, "arrows", 499)
        self.assertEqual([item["quantity"] for item in self.character.equipment], [999])

    def test_a_stack_past_the_cap_would_not_have_imported(self):
        self.character.equipment = [{"item_id": "arrows", "quantity": 1998}]
        self.character.save()
        with self.assertRaises(CharacterImportError):
            validate_import_payload(json.loads(generate_character_json(self.character)))


class WealthBeforePurchaseTest(SheetTestCase):
    """#851: a player buys nothing until wealth is rolled; a Game Master may."""

    def setUp(self):
        super().setUp()
        self.character.wealth_recorded = False
        self.character.save()

    def test_a_player_is_refused_a_catalog_purchase(self):
        self.as_player()
        response = self.post_json(self.url("equipment_add"), {"item_id": "broadsword"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], WEALTH_FIRST_REFUSAL)
        self.character.refresh_from_db()
        self.assertEqual(self.character.equipment, [])

    def test_a_player_is_refused_more_of_an_item(self):
        self.character.equipment = [{"item_id": "arrows_20", "quantity": 1}]
        self.character.save()
        self.as_player()
        response = self.post_json(
            self.url("equipment_update"), {"index": 0, "quantity": 3}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], WEALTH_FIRST_REFUSAL)

    def test_a_player_is_refused_a_priced_custom_item(self):
        self.as_player()
        response = self.post_json(
            self.url("equipment_add"),
            {"is_custom": True, "name": "Lute", "category": "gear", "cost": 5},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], WEALTH_FIRST_REFUSAL)

    def test_a_player_may_still_pack_and_note_what_they_have(self):
        self.character.equipment = [{"item_id": "arrows", "quantity": 1}]
        self.character.save()
        self.as_player()
        response = self.post_json(
            self.url("equipment_update"), {"index": 0, "in_pack": True}
        )
        self.assertEqual(response.status_code, 200)

    def test_after_the_wealth_roll_the_player_buys(self):
        self.character.wealth_recorded = True
        self.character.save()
        self.as_player()
        response = self.post_json(self.url("equipment_add"), {"item_id": "dagger"})
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_game_master_may_buy_or_give_before_it(self):
        bought = self.post_json(self.url("equipment_add"), {"item_id": "dagger"})
        self.assertEqual(bought.status_code, 200, bought.content)
        given = self.post_json(
            self.url("equipment_add"), {"item_id": "dagger", "free": True}
        )
        self.assertEqual(given.status_code, 200)

    def test_a_players_chat_purchase_is_refused_the_same_way(self):
        with self.assertRaises(services.CharacterChangeError) as refusal:
            services.apply_equipment(self.character, "dagger", 1, purchase=True)
        self.assertEqual(str(refusal.exception), WEALTH_FIRST_REFUSAL)


class HeightAndWeightTest(SheetTestCase):
    """#805: nonsense is refused, and a stored nonsense value reads as unknown."""

    def test_a_game_master_is_refused_nonsense(self):
        for field, value in (
            ("height_roll", "tall"),
            ("weight_roll", "heavy"),
            ("height_roll", "3.4"),
        ):
            with self.subTest(field=field, value=value):
                response = self.update(field, value)
                self.assertEqual(response.status_code, 400)
                self.assertIn("die faces 1 to 6", response.json()["error"])

    def test_a_game_master_may_state_a_rolled_chain(self):
        data = self.update("height_roll", "6.6.2").json()
        self.assertEqual(data["height_display"], "6'8\"")

    def test_a_stored_nonsense_value_is_unknown_not_a_three(self):
        character = Character(height_roll="tall", weight_roll="heavy", strength=10)
        self.assertEqual(character.height_display, "")
        self.assertIsNone(character.weight_lbs)
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn('<span id="height_display">(5&#x27;8&quot;)</span>', page)

    def test_the_sheet_shows_a_dash_for_no_height(self):
        blank = create_character(self.owner, name="Blank", height_roll="")
        page = self.client.get(self.url("edit", blank)).content.decode()
        self.assertIn('<span id="height_display">(—)</span>', page)


class SheetRulesSeamTest(SheetTestCase):
    """The seams with the sheetrules batch (#769, #770, #801, #810)."""

    def test_a_game_master_is_offered_the_grant_the_endpoint_takes(self):
        """#770's server half is the sheetrules batch's, and it has landed: the
        gate is on, so a Game Master at no points keeps the controls. This
        test used to hold the interim state (the gate off) until it did."""
        self.character.skills = {"herbalism": 1}
        self.character.free_skill_points = 0
        self.character.save()
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn(
            'class="allocate-btn button secondary" data-skill="herbalism"', page
        )
        self.assertIn('<div class="add-skill-control">', page)
        self.assertTrue(page_config(page)["canGrantSkillPoints"])

    def test_a_game_master_keeps_the_allocate_controls_once_it_does(self):
        self.character.skills = {"herbalism": 1}
        self.character.free_skill_points = 0
        self.character.save()
        with mock.patch(
            "characters.views.characters.GAME_MASTER_GRANTS_SKILL_POINTS", True
        ):
            page = self.client.get(self.url("edit")).content.decode()
        self.assertIn(
            'class="allocate-btn button secondary" data-skill="herbalism"', page
        )
        self.assertIn('<div class="add-skill-control">', page)
        self.assertTrue(page_config(page)["canGrantSkillPoints"])

    def test_a_player_at_no_points_has_neither(self):
        self.as_player()
        self.character.skills = {"herbalism": 1}
        self.character.free_skill_points = 0
        self.character.save()
        page = self.client.get(self.url("edit")).content.decode()
        self.assertNotIn('class="allocate-btn', page)
        self.assertIn('<div class="add-skill-control" style="display: none;">', page)

    def test_an_xp_save_sends_the_skills_list_and_the_free_points(self):
        data = self.update("total_xp", 5000).json()
        self.assertIn("skills_html", data)
        self.assertIn("total_allocated_points", data)
        self.assertIn("free_skill_points", data)

    def test_the_new_cells_join_the_editable_fields_only_with_their_model_fields(self):
        from characters.models import (
            CHARACTER_FIELDS,
            SHEET_RULES_CELL_FIELDS,
            character_has_field,
        )

        for name in SHEET_RULES_CELL_FIELDS:
            with self.subTest(field=name):
                self.assertEqual(name in CHARACTER_FIELDS, character_has_field(name))
        self.assertTrue(character_has_field("height_roll"))
        self.assertFalse(character_has_field("no_such_field"))


class ReviewFollowUpTest(SheetTestCase):
    """The review of the batch: stored pack state, hands, notes, nulls."""

    def dropped_pack_shield(self) -> None:
        self.character.equipment = [
            {"item_id": "small_shield", "quantity": 1, "in_pack": True}
        ]
        self.character.pack_dropped = True
        self.character.save()

    def test_a_player_cannot_unpack_and_ready_from_a_dropped_pack_at_once(self):
        self.dropped_pack_shield()
        self.as_player()
        response = self.post_json(
            self.url("equipment_update"),
            {"index": 0, "in_pack": False, "equipped": True},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("dropped pack", response.json()["error"])
        self.character.refresh_from_db()
        self.assertTrue(self.character.equipment[0]["in_pack"])

    def test_a_player_cannot_unpack_from_a_dropped_pack(self):
        self.dropped_pack_shield()
        self.as_player()
        response = self.post_json(
            self.url("equipment_update"), {"index": 0, "in_pack": False}
        )
        self.assertEqual(response.status_code, 400)

    def test_a_player_may_still_unpack_with_the_pack_on(self):
        self.character.equipment = [
            {"item_id": "small_shield", "quantity": 1, "in_pack": True}
        ]
        self.character.save()
        self.as_player()
        response = self.post_json(
            self.url("equipment_update"), {"index": 0, "in_pack": False}
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_game_master_may_draw_from_a_dropped_pack(self):
        self.dropped_pack_shield()
        response = self.post_json(
            self.url("equipment_update"), {"index": 0, "equipped": True}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["shield_in_use_index"], 0)

    def test_a_game_master_may_ready_a_shield_beside_a_two_handed_weapon(self):
        self.character.equipment = [
            {"item_id": "great_hammer", "quantity": 1},
            {"item_id": "large_shield", "quantity": 1},
        ]
        self.character.save()
        response = self.post_json(
            self.url("equipment_update"), {"index": 1, "equipped": True}
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertIsNone(data["shield_in_use_index"])
        self.assertIn("both hands", data["shield_idle_reason"])

    def test_readying_a_two_handed_weapon_says_why_the_shield_stopped(self):
        self.as_player()
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1},
            {"item_id": "large_shield", "quantity": 1},
            {"item_id": "great_hammer", "quantity": 1},
        ]
        self.character.save()
        before = self.post_json(
            self.url("equipment_update"), {"index": 0, "notes": "x"}
        ).json()
        self.assertEqual(before["shield_in_use_index"], 1)
        self.assertEqual(before["shield_idle_reason"], "")
        data = self.post_json(
            self.url("equipment_update"), {"index": 2, "equipped": True}
        ).json()
        self.assertIsNone(data["shield_in_use_index"])
        self.assertIn("both hands", data["shield_idle_reason"])

    def test_an_archer_with_a_shield_holds_the_one_handed_weapon(self):
        self.character.equipment = [
            {"item_id": "longbow", "quantity": 1},
            {"item_id": "broadsword", "quantity": 1},
            {"item_id": "small_shield", "quantity": 1},
        ]
        self.character.save()
        self.assertEqual(self.character.weapon_in_hand_index, 1)
        self.assertEqual(self.character.shield_in_use_index, 2)
        self.assertEqual(_shield_bonus(self.character), 1)
        weapon = _seated_arms(self.character).weapon
        self.assertEqual(weapon.item_id, "broadsword")

    def test_without_a_shield_the_first_weapon_is_in_hand(self):
        self.character.equipment = [
            {"item_id": "longbow", "quantity": 1},
            {"item_id": "broadsword", "quantity": 1},
        ]
        self.character.save()
        self.assertEqual(self.character.weapon_in_hand_index, 0)

    def test_a_readied_bow_is_kept_even_beside_a_shield(self):
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1},
            {"item_id": "longbow", "quantity": 1, "equipped": True},
            {"item_id": "small_shield", "quantity": 1},
        ]
        self.character.save()
        self.assertEqual(self.character.weapon_in_hand_index, 1)
        self.assertIsNone(self.character.shield_in_use_index)

    def test_a_game_masters_purchase_before_the_wealth_roll_is_marked(self):
        from characters.purse import GAME_MASTER_BEFORE_WEALTH_NOTE

        self.character.wealth_recorded = False
        self.character.save()
        data = self.post_json(self.url("equipment_add"), {"item_id": "dagger"}).json()
        self.assertEqual(data["notice"], GAME_MASTER_BEFORE_WEALTH_NOTE)
        self.assertIn("placeholder starting coin", data["recent_changes_html"])
        given = self.post_json(
            self.url("equipment_add"), {"item_id": "dagger", "free": True}
        ).json()
        self.assertNotIn("notice", given)

    def test_after_the_wealth_roll_a_game_masters_purchase_has_no_note(self):
        data = self.post_json(self.url("equipment_add"), {"item_id": "dagger"}).json()
        self.assertNotIn("notice", data)

    def test_a_wealth_change_sends_the_purse(self):
        for field, value in (("wealth_level", "Moderate"), ("starting_coin", 40)):
            with self.subTest(field=field):
                data = self.update(field, value).json()
                self.assertIn("purse", data)
                self.assertIn("coin_adjustment", data)

    def test_a_null_height_is_blank_for_a_game_master_and_refused_for_a_player(self):
        response = self.update("height_roll", None)
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.height_roll, "")
        self.as_player()
        refused = self.update("weight_roll", None)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(
            refused.json()["error"],
            services.player_chosen_face_reasons()["weight_roll"],
        )

    def test_a_custom_one_handed_weapon_is_not_the_default_beside_a_shield(self):
        """The board cannot seat a custom weapon, so the default passes it."""
        sickle = {
            "item_id": "custom",
            "name": "Sickle",
            "category": "weapons",
            "damage": "1d6",
            "quantity": 1,
        }
        self.character.equipment = [
            {"item_id": "longbow", "quantity": 1},
            sickle,
            {"item_id": "small_shield", "quantity": 1},
        ]
        self.character.save()
        self.assertEqual(self.character.weapon_in_hand_index, 0)
        self.assertIsNone(self.character.shield_in_use_index)
        weapon = _seated_arms(self.character).weapon
        self.assertEqual(weapon.item_id, "longbow")
        self.assertEqual(_shield_bonus(self.character, weapon), 0)

    def test_a_readied_custom_weapon_leaves_the_board_a_bow_and_no_shield(self):
        """Readied, the sickle is in hand on the sheet; the board still seats
        the bow it can use, and that bow takes the shield's hand."""
        self.character.equipment = [
            {"item_id": "longbow", "quantity": 1},
            {
                "item_id": "custom",
                "name": "Sickle",
                "category": "weapons",
                "damage": "1d6",
                "quantity": 1,
                "equipped": True,
            },
            {"item_id": "small_shield", "quantity": 1},
        ]
        self.character.save()
        self.assertEqual(self.character.weapon_in_hand_index, 1)
        self.assertEqual(self.character.shield_in_use_index, 2)
        weapon = _seated_arms(self.character).weapon
        self.assertEqual(weapon.item_id, "longbow")
        self.assertEqual(_shield_bonus(self.character, weapon), 0)
        self.assertEqual(_shield_bonus(self.character), 1)


class StaleSweepFollowUpTest(SheetTestCase):
    """The #792 class swept again (batch G): what a change moved, sent to paint."""

    def test_every_repaint_carries_the_mana_in_hand(self):
        self.character.free_spell_points = 3
        self.character.save()
        data = self.update("name", "Mana Holder").json()
        self.assertEqual(data["current_mana"], 3)

    def test_dismissing_the_import_notice_sends_the_history_line(self):
        self.character.import_notice = "Left out: rolled attributes."
        self.character.save()
        data = self.post_json(self.url("dismiss_import_notice"), {}).json()
        self.assertTrue(data["success"])
        self.assertIn("import_notice", data["recent_changes_html"])

    def test_a_game_master_has_the_coin_reroll_hidden_until_the_wealth_gives_coin(self):
        self.character.wealth_level = "Rich"
        self.character.save()
        page = self.client.get(self.url("edit")).content.decode()
        button = re.search(r'<button[^>]*id="roll-starting-coin-btn"[^>]*>', page)
        assert button is not None
        self.assertIn(" hidden", button.group(0))

        data = self.update("wealth_level", "Moderate").json()
        self.assertTrue(data["sheet_rolls"]["starting_coin"]["available"])

    def test_a_player_is_still_not_given_the_coin_reroll(self):
        self.as_player()
        page = self.client.get(self.url("edit")).content.decode()
        self.assertNotIn('id="roll-starting-coin-btn"', page)


class LifeAndNameRepaintTest(SheetTestCase):
    """#903: whether the character lives, the saved name and the slot that needs both.

    Each is server-rendered state a change made without a reload moves: a
    fatal run of years kills the character, the last score or the Fatigue
    roll completes it, and a rename changes every place the sheet prints the
    name. Every response carries them through ``sheet_repaint``; the page
    renders each control it hides by them, so the painter has one to show.
    """

    FATAL_DICE = (1, 1, 1, 3)

    def add_fatal_year(self) -> dict:
        """Roll one year whose survival check is the automatic death."""
        with mock.patch(
            "characters.skills.roll_survival_dice", return_value=self.FATAL_DICE
        ):
            response = self.post_json(
                self.url("add_years"), {"track": "campaigner", "years": 1}
            )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_a_field_update_carries_the_saved_name_and_the_slot(self):
        data = self.update("charisma", 9).json()
        self.assertEqual(data["name"], self.character.name)
        self.assertTrue(data["is_alive"])
        self.assertIn(
            f'id="play-with-gm-{self.character.pk}"', data["play_with_gm_html"]
        )
        self.assertIn('<form class="play-with-gm', data["play_with_gm_html"])

    def test_a_rename_answers_with_the_name_it_saved(self):
        data = self.update("name", "  Brannoc  ").json()
        self.character.refresh_from_db()
        self.assertEqual(data["name"], self.character.name)
        self.assertEqual(data["name"], "Brannoc")
        self.assertIn("Take Brannoc to", data["play_with_gm_html"])

    def test_a_rename_re_renders_the_survival_saves_confirm(self):
        body = self.character.body
        assert body is not None
        self.character.body_damage = body + (body + 1) // 2
        self.character.save()
        data = self.update("name", "Brannoc").json()
        self.assertIn("Roll Brannoc's survival save?", data["injury_thresholds_html"])

    def test_a_field_that_is_not_the_name_sends_no_injury_readout(self):
        self.assertNotIn("injury_thresholds_html", self.update("charisma", 9).json())

    def test_a_fatal_run_answers_dead_with_no_way_to_the_table(self):
        data = self.add_fatal_year()
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_alive)
        self.assertFalse(data["is_alive"])
        self.assertEqual(data["name"], self.character.name)
        self.assertIn(
            f'id="play-with-gm-{self.character.pk}"', data["play_with_gm_html"]
        )
        self.assertNotIn("<form", data["play_with_gm_html"])

    def test_the_last_score_offers_the_way_to_the_table(self):
        character = incomplete_character(
            self.owner,
            strength=12,
            dexterity=12,
            intelligence=12,
            wisdom=12,
            constitution=12,
            fatigue_roll=7,
        )
        before = self.client.get(self.url("edit", character)).content.decode()
        self.assertNotIn('<form class="play-with-gm', before)
        data = self.update("charisma", 10, character).json()
        self.assertTrue(data["is_complete"])
        self.assertIn('<form class="play-with-gm', data["play_with_gm_html"])

    def test_the_slot_offers_the_viewers_own_sessions(self):
        from gm.models import GMSession

        GMSession.objects.create(user=self.game_master, name="Kell's table")
        GMSession.objects.create(user=self.owner, name="The owner's own")
        data = self.update("charisma", 9).json()
        self.assertIn("Kell&#x27;s table", data["play_with_gm_html"])
        self.assertNotIn("The owner", data["play_with_gm_html"])

    def test_an_allocated_skill_joins_the_type_ahead(self):
        self.character.free_skill_points = 1
        self.character.save()
        data = self.post_json(
            self.url("allocate_skill"), {"skill": "basket weaving"}
        ).json()
        self.assertIn(
            '<option value="Basket Weaving"></option>',
            data["skill_name_suggestions_html"],
        )

    def test_the_page_and_the_response_render_the_same_type_ahead(self):
        page = self.client.get(self.url("edit")).content.decode()
        data = self.update("charisma", 9).json()
        self.assertIn(data["skill_name_suggestions_html"].strip(), page)

    def test_equipment_and_mechanics_responses_carry_them_too(self):
        equipment = self.post_json(
            self.url("equipment_add"), {"item_id": "backpack", "free": True}
        ).json()
        dismissal = self.post_json(self.url("dismiss_import_notice"), {}).json()
        for data in (equipment, dismissal):
            with self.subTest(keys=sorted(data)[:3]):
                self.assertEqual(data["name"], self.character.name)
                self.assertIn("is_alive", data)
                self.assertIn("play_with_gm_html", data)
                self.assertIn("skill_name_suggestions_html", data)

    def test_a_living_sheet_renders_revive_and_hides_it(self):
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn(
            '<button type="button" id="mark-dead-btn" class="button danger" '
            "data-alive-only>",
            page,
        )
        self.assertIn(
            '<button type="button" id="revive-btn" class="button" data-dead-only hidden>',
            page,
        )
        self.assertIn('id="magic-controls" data-alive-only>', page)

    def test_a_dead_sheet_renders_the_living_controls_hidden(self):
        self.character.is_alive = False
        self.character.death_cause = "Fell"
        self.character.save()
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn(
            'id="mark-dead-btn" class="button danger" data-alive-only hidden>', page
        )
        self.assertIn('id="revive-btn" class="button" data-dead-only>', page)
        self.assertIn('id="magic-controls" data-alive-only hidden>', page)
        self.assertIn(
            f"<h3><span data-name-chrome>{self.character.name}</span> died</h3>", page
        )

    def test_the_sessions_holding_it_mark_the_name(self):
        from gm.models import GMSession

        session = GMSession.objects.create(user=self.game_master, name="Kell's table")
        session.characters.add(self.character)
        self.as_player()
        page = self.client.get(self.url("edit")).content.decode()
        self.assertIn(
            f"<span data-name-chrome>{self.character.name}</span> is in", page
        )

    def test_the_config_names_the_history_the_record_keeps(self):
        from characters.models import MAX_EXPERIENCE_LOG_ENTRIES

        page = self.client.get(self.url("edit")).content.decode()
        self.assertEqual(
            page_config(page)["maxExperienceLogEntries"], MAX_EXPERIENCE_LOG_ENTRIES
        )


class OneRepaintPerResponseTest(SheetTestCase):
    """#933: a response carries the sheet repaint once, not once per payload in it.

    A run of years nests the equipment payload under ``movement``. That
    payload carried ``sheet_repaint`` as well, so the "Play with the Game
    Master" slot and its session queries, the completion partial, the skill
    check's options, Recent Changes and the type-ahead all rendered twice,
    and the page painted the same values twice.
    """

    SAFE_DICE = (6, 6, 6, 18)

    def add_year(self) -> tuple[dict, int]:
        """Roll one survivable year; the response and its session queries."""
        with (
            mock.patch(
                "characters.skills.roll_survival_dice", return_value=self.SAFE_DICE
            ),
            CaptureQueriesContext(connection) as captured,
        ):
            response = self.post_json(
                self.url("add_years"), {"track": "laborer", "years": 1}
            )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json(), session_queries(captured)

    def test_a_run_of_years_queries_the_sessions_for_one_slot(self):
        from gm.models import GMSession

        GMSession.objects.create(user=self.game_master, name="Kell's table")
        with CaptureQueriesContext(connection) as one_repaint:
            sheet_repaint(self.character, self.game_master)
        per_repaint = session_queries(one_repaint)
        # The slot is offered, so a repaint does query the sessions; a test
        # that counted zero against zero would prove nothing.
        self.assertGreater(per_repaint, 0)
        _data, queried = self.add_year()
        self.assertEqual(queried, per_repaint)

    def test_the_movement_payload_leaves_the_repaint_to_the_response(self):
        data, _queried = self.add_year()
        self.character.refresh_from_db()
        for key in sheet_repaint(self.character, self.game_master):
            with self.subTest(key=key):
                self.assertIn(key, data)
                self.assertNotIn(key, data["movement"])
        # The movement payload still carries what updateEquipmentDisplay paints.
        self.assertEqual(
            data["movement"]["lightest_load_limit"],
            self.character.lightest_load_limit,
        )
        self.assertEqual(data["movement"]["equipment"], self.character.equipment)


def session_queries(captured: CaptureQueriesContext) -> int:
    """How many captured queries read the Games Master sessions table."""
    return sum('"gm_gmsession"' in query["sql"] for query in captured.captured_queries)
