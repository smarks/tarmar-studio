"""Characters that are not complete yet (issue #571).

A character built in a Games Master session is a row from its first change,
before its attributes or Fatigue roll are known. Completeness is derived from
the record, incomplete characters are shown everywhere with a mark, and the
rules refuse them wherever a whole character is needed.
"""

import json

from battle.services import claimable_characters, seatable_characters
from characters.models import NEEDS_ATTRIBUTES, NEEDS_FATIGUE_ROLL, Character
from common.test_helpers import BaseTestCase, create_character
from parties.models import addable_characters


def incomplete_character(user, **overrides) -> Character:
    """A saved character with no attributes and no Fatigue roll."""
    fields = {
        "name": "Half Built",
        "strength": None,
        "dexterity": None,
        "intelligence": None,
        "wisdom": None,
        "constitution": None,
        "charisma": None,
        "fatigue_roll": None,
        **overrides,
    }
    return Character.objects.create(user=user, **fields)


class CompletenessTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user("incomplete-owner")

    def test_a_character_with_everything_is_complete(self):
        character = create_character(self.user)

        self.assertTrue(character.is_complete)
        self.assertEqual(character.missing_parts, [])
        self.assertEqual(character.status, "Ready")

    def test_the_marks_say_what_is_missing(self):
        character = incomplete_character(self.user)

        self.assertFalse(character.is_complete)
        self.assertEqual(
            character.missing_parts, [NEEDS_ATTRIBUTES, NEEDS_FATIGUE_ROLL]
        )
        self.assertEqual(character.status, "Incomplete")
        self.assertEqual(character.status_css_class, "incomplete-mark")

    def test_one_missing_attribute_is_enough_to_be_incomplete(self):
        character = create_character(self.user, charisma=None)

        self.assertEqual(character.missing_parts, [NEEDS_ATTRIBUTES])

    def test_derived_values_are_unknown_rather_than_invented(self):
        character = incomplete_character(self.user, height_roll="", weight_roll="")

        self.assertIsNone(character.fatigue)
        self.assertIsNone(character.body)
        self.assertIsNone(character.physical)
        self.assertIsNone(character.mental)
        self.assertIsNone(character.injury_thresholds)
        self.assertIsNone(character.weight_lbs)
        self.assertIsNone(character.height_inches)
        self.assertEqual(character.height_display, "")

    def test_effective_attributes_are_unknown_rather_than_zero(self):
        """Issue #630: an unrecorded attribute is not an attribute of 0.

        This assertion used to read ``assertEqual(effective_strength, 0)``,
        pinning the coercion that let the sheet quote "DEX Combat Mod -5" for
        a character nobody had entered a DEX for.
        """
        character = incomplete_character(self.user)

        self.assertIsNone(character.effective_strength)
        self.assertIsNone(character.effective_dexterity)
        self.assertIsNone(character.effective_intelligence)
        self.assertIsNone(character.effective_wisdom)
        self.assertIsNone(character.effective_constitution)
        self.assertIsNone(character.combat_dexterity)
        self.assertIsNone(character.dex_modifier)
        self.assertIsNone(character.dodge_modifier)

    def test_movement_and_encumbrance_are_unknown_rather_than_zero(self):
        """Issue #630: the sheet used to show "mod -24" and every gait at 0."""
        character = incomplete_character(self.user)

        self.assertIsNone(character.movement_modifier)
        self.assertIsNone(character.encumbrance_level)
        self.assertEqual(
            [gait["distance"] for gait in character.gait_readout], [None] * 4
        )

    def test_a_recorded_attribute_still_carries_its_aging_penalty(self):
        """The floor-at-0 rule is unchanged for attributes that exist.

        Unsaved, because ``save()`` reconciles the aging columns from the
        character's age (issue #680) and would drop a penalty set by hand.
        """
        character = Character(strength=10, aging_str_penalty=14)

        self.assertEqual(character.effective_strength, 0)

    def test_pools_are_refused_for_an_incomplete_character(self):
        self.assertEqual(len(create_character(self.user).pools()), 2)
        with self.assertRaisesMessage(ValueError, "needs Fatigue roll"):
            incomplete_character(self.user).pools()

    def test_playable_holds_only_complete_characters(self):
        complete = create_character(self.user, name="Whole")
        incomplete_character(self.user)
        create_character(self.user, name="No Roll", fatigue_roll=None)
        create_character(self.user, name="No Wisdom", wisdom=None)

        self.assertEqual(list(Character.objects.playable()), [complete])


class IncompleteCharactersAreNotSeatedTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user("incomplete-seats")
        self.complete = create_character(self.user, name="Whole")
        self.incomplete = incomplete_character(self.user)

    def test_battle_seating_offers_only_complete_characters(self):
        self.assertEqual(list(seatable_characters(self.user)), [self.complete])
        self.assertEqual(list(claimable_characters(self.user)), [self.complete])

    def test_a_party_musters_only_complete_characters(self):
        self.assertEqual(list(addable_characters(self.user)), [self.complete])

    def test_the_games_master_picker_refuses_an_incomplete_character(self):
        self.login_as(self.user)

        response = self.client.post(
            "/tarmar/gm/characters/attach/",
            json.dumps({"character_id": self.incomplete.pk}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("needs attributes", response.json()["error"])

    def test_the_games_master_picker_lists_only_complete_characters(self):
        self.login_as(self.user)

        response = self.client.get("/tarmar/gm/")

        self.assertEqual(
            list(response.context["attachable_characters"]), [self.complete]
        )


class IncompleteCharactersAreShownTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user("incomplete-shown")
        self.login_as(self.user)
        self.character = incomplete_character(self.user)

    def test_the_list_shows_the_row_with_its_marks(self):
        response = self.client.get("/tarmar/characters/")

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("Half Built", page)
        self.assertIn("needs attributes", page)
        self.assertIn("needs Fatigue roll", page)
        self.assertNotIn(">None<", page)
        self.assertNotIn(f"/tarmar/export/{self.character.pk}/", page)

    def test_the_edit_page_renders_with_its_marks(self):
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn('id="incomplete-marks"', page)
        self.assertIn("needs Fatigue roll", page)
        self.assertIn('id="fatigue">—</span>', page)
        self.assertIn('<span id="weight_lbs">—</span>', page)
        self.assertNotIn('value="None" min=', page)

    def test_the_sheet_states_no_combat_or_movement_number(self):
        """Issue #630: these tiles used to read -5, +0, -24 and four zeroes."""
        page = self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()

        self.assertIn('id="dex-combat-mod">—</div>', page)
        self.assertIn('id="dodge-modifier">—</div>', page)
        self.assertIn('id="movement-modifier">—</span>', page)
        self.assertIn('id="max-unencumbered">—</span>', page)
        self.assertIn('id="encumbrance-level">—</span>', page)
        for gait in ("sprint", "run", "jog", "walk"):
            with self.subTest(gait=gait):
                self.assertIn(f'id="gait-{gait}"', page)
        self.assertNotIn('Sprint</span><span class="gait-value">0<', page)

    def test_the_injury_table_says_it_is_not_known_yet(self):
        """Issue #639: eight empty cells and a sentence ending at "3d6 <="."""
        page = self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()

        self.assertIn('id="injury-unknown"', page)
        self.assertIn("Recorded once the attributes and the Fatigue roll are in", page)
        self.assertNotIn('id="injury-fatigue-minus_two_at"', page)
        self.assertNotIn('id="injury-fatigue-survival_save_target"', page)
        self.assertNotIn("Survival save is 3d6", page)

    def test_recording_the_last_part_brings_the_table_back(self):
        """The readout crosses completeness without a reload (issues #571, #639)."""
        # A Game Master types the scores; a player rolls them (issue #748).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("incomplete-gm"))
        for field, value in (
            ("strength", 12),
            ("dexterity", 12),
            ("intelligence", 12),
            ("wisdom", 12),
            ("constitution", 12),
            ("charisma", 12),
        ):
            self.post_json(
                f"/tarmar/characters/{self.character.pk}/update-field/",
                {"field": field, "value": value},
            )

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "fatigue_roll", "value": 7},
        )

        readout = response.json()["injury_thresholds_html"]
        self.assertIn('id="injury-fatigue-minus_two_at">5</td>', readout)
        self.assertNotIn("injury-unknown", readout)

    def test_the_sheet_offers_no_attack_form(self):
        """The form waits, hidden, until the character is complete.

        This used to assert the form was left out of the page, which was
        issue #764: completing the character could not show it without a
        reload. It is rendered inside a hidden wrapper now.
        """
        page = self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()

        self.assertIn('<div id="attack-ready" hidden>', page)
        self.assertLess(page.index('id="attack-ready"'), page.index('id="attack-form"'))
        self.assertIn('id="attack-incomplete">', page)

    def test_equipment_can_be_added_to_an_incomplete_character(self):
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "broadsword"},
        )

        self.assertEqual(response.status_code, 200)

    def test_the_edit_page_offers_a_control_for_the_fatigue_roll(self):
        """Issue #636: the marks named a blocking state with no remedy."""
        page = self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()

        self.assertIn('id="roll-fatigue-btn"', page)
        self.assertIn('id="fatigue-roll">—</span>', page)

    def test_the_fatigue_roll_can_be_made_from_the_edit_page(self):
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/roll-fatigue/", {}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.character.refresh_from_db()
        self.assertEqual(data["fatigue_roll"], self.character.fatigue_roll)
        self.assertIn(self.character.fatigue_roll, range(2, 13))
        self.assertIn("Fatigue roll (2d6) rolled", data["note"])
        self.assertNotIn(NEEDS_FATIGUE_ROLL, self.character.missing_parts)

    def test_a_player_gets_one_roll_and_the_control_then_goes(self):
        """The one-roll-per-player rule is issue #590's, enforced server-side."""
        first = self.post_json(
            f"/tarmar/characters/{self.character.pk}/roll-fatigue/", {}
        )
        self.assertFalse(first.json()["can_roll_fatigue"])

        second = self.post_json(
            f"/tarmar/characters/{self.character.pk}/roll-fatigue/", {}
        )

        self.assertEqual(second.status_code, 400)
        self.assertIn("only a Game Master", second.json()["error"])
        page = self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()
        self.assertNotIn('id="roll-fatigue-btn"', page)

    def test_a_game_master_may_have_it_rolled_again(self):
        game_master = self.create_dm("fatigue-dm")
        self.logout()
        self.login_as(game_master)
        self.post_json(f"/tarmar/characters/{self.character.pk}/roll-fatigue/", {})

        again = self.post_json(
            f"/tarmar/characters/{self.character.pk}/roll-fatigue/", {}
        )

        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["can_roll_fatigue"])

    def test_a_stranger_cannot_roll_it(self):
        stranger = self.create_user("fatigue-stranger")
        self.logout()
        self.login_as(stranger)

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/roll-fatigue/", {}
        )

        self.assertEqual(response.status_code, 403)
        self.character.refresh_from_db()
        self.assertIsNone(self.character.fatigue_roll)

    def test_an_attribute_can_be_filled_in_on_the_edit_page(self):
        # By a Game Master; a player rolls them instead (issue #748,
        # test_field_authors.SheetRollTest and PointBuyTest).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("incomplete-fill-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "strength", "value": 12},
        )

        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(self.character.strength, 12)


class IncompleteCharactersCannotAttackTest(BaseTestCase):
    """Issue #630: the attack endpoint used to resolve a swing for a DEX of 0."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("incomplete-attack")
        self.login_as(self.user)
        self.character = incomplete_character(
            self.user, equipment=[{"item_id": "dagger", "quantity": 1}]
        )

    def test_the_attack_endpoint_refuses_and_says_what_is_missing(self):
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/attack/", {"weapon_index": 0}
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("needs attributes", response.json()["error"])
        self.character.refresh_from_db()
        self.assertEqual(self.character.combat_log, [])

    def test_a_complete_character_still_resolves_an_attack(self):
        whole = create_character(
            self.user,
            name="Whole",
            equipment=[{"item_id": "dagger", "quantity": 1}],
        )

        response = self.post_json(
            f"/tarmar/characters/{whole.pk}/attack/", {"weapon_index": 0}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])


class IncompleteCharactersAreNotExportedTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user("incomplete-export")
        self.login_as(self.user)
        self.character = incomplete_character(self.user)

    def test_every_export_refuses_and_says_why(self):
        for fmt in ("pdf", "markdown", "json"):
            with self.subTest(fmt=fmt):
                response = self.client.get(f"/tarmar/export/{self.character.pk}/{fmt}/")

                self.assertEqual(response.status_code, 409)
                self.assertIn("needs attributes", response.content.decode())
