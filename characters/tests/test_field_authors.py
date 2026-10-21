"""Who may set what on the character sheet (issues #748, #749, #752).

Spencer's standing rule, restated 2026-09-28: players are held to the rules
exactly, and a Game Master (``can_manage_users``) may override. On #749:
players roll provenance and wealth and cannot choose them; starting coin is
rolled once when wealth is first set and never rerolled by re-selecting; a
Game Master may set either and reroll coin explicitly.

The rescan's reproductions (scratchpad/rescan/paths/test_paths.py) asserted
each defect was present; the tests here assert the opposite of each.
"""

import importlib
import json
import re
from unittest.mock import patch

from django.apps import apps
from django.urls import reverse

from battle.services import snapshot_character
from character_export.export_utils import (
    generate_character_json,
    generate_character_markdown,
)
from characters import services
from characters.constants import CHARACTER_ATTRIBUTE_FIELDS
from characters.models import CHARACTER_FIELDS, Character
from characters.refusal_words import refusal_words
from characters.services import (
    FIELD_RULES,
    GAME_MASTER_UPDATE_FIELDS,
    PLAYER_UPDATE_FIELDS,
    CharacterChangeError,
    FieldAuthor,
)
from common.test_helpers import BaseTestCase, create_character

MIGRATION = importlib.import_module(
    "characters.migrations.0015_background_recorded_and_rich_coin"
)


class FieldRulesCoverTheModelTest(BaseTestCase):
    """Every stored field is decided once, in one table."""

    def test_every_model_field_is_in_exactly_one_of_the_three_lists(self):
        model_fields = {field.name for field in Character._meta.concrete_fields}
        by_author = {
            author: {
                name for name, rule in FIELD_RULES.items() if rule.author is author
            }
            for author in FieldAuthor
        }

        self.assertEqual(set(FIELD_RULES), model_fields)
        for author, names in by_author.items():
            for other in FieldAuthor:
                if other is not author:
                    self.assertFalse(names & by_author[other], (author, other))
        self.assertEqual(set().union(*by_author.values()), model_fields)

    def test_a_game_master_only_field_says_why(self):
        for name, rule in FIELD_RULES.items():
            with self.subTest(field=name):
                self.assertEqual(
                    bool(rule.reason), rule.author is FieldAuthor.GAME_MASTER
                )

    def test_the_sheet_edits_no_field_nobody_sets(self):
        for name in CHARACTER_FIELDS:
            with self.subTest(field=name):
                self.assertIsNot(FIELD_RULES[name].author, FieldAuthor.NOBODY)

    def test_the_role_lists(self):
        self.assertEqual(
            # people joined with #810: a people is chosen, not rolled.
            PLAYER_UPDATE_FIELDS,
            {"name", "height_roll", "weight_roll", "location", "people"},
        )
        self.assertEqual(GAME_MASTER_UPDATE_FIELDS, set(CHARACTER_FIELDS))


class ThePageAndTheServerAgreeTest(BaseTestCase):
    """The sheet renders an input exactly where the field update takes one."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("agree-player")
        self.character = create_character(self.player)

    def fields_rendered_editable(self, user) -> set[str]:
        self.logout()
        self.login_as(user)
        html = self.client.get(
            reverse("characters:edit", args=[self.character.pk])
        ).content.decode()
        return set(re.findall(r'class="editable-field[^"]*" data-field="(\w+)"', html))

    def test_a_player_gets_inputs_for_their_own_fields_only(self):
        self.assertEqual(
            self.fields_rendered_editable(self.player), PLAYER_UPDATE_FIELDS
        )

    def test_a_game_master_gets_every_input(self):
        self.assertEqual(
            self.fields_rendered_editable(self.create_dm("agree-gm")),
            # The Fatigue roll's cell is its roll control, for everyone (#636);
            # a Game Master's typed roll is the field update's alone.
            GAME_MASTER_UPDATE_FIELDS - {"fatigue_roll"},
        )

    def test_a_player_reads_the_reason_beside_a_rolled_value(self):
        self.login_as(self.player)
        html = self.client.get(
            reverse("characters:edit", args=[self.character.pk])
        ).content.decode()

        # One of each cell shape: attribute, choice, text and number.
        for name in (
            "strength",
            "provenance_class",
            "wealth_level",
            "provenance_detail",
            "appearance_roll",
            "total_xp",
            "starting_coin",
        ):
            with self.subTest(field=name):
                self.assertIn(
                    f'data-locked-field="{name}">{services.field_refusal(name)}',
                    html.replace("&#x27;", "'"),
                )
        # The Fatigue roll's cell is its roll readout (#636): no input for a
        # player, and no second roll once it is recorded.
        self.assertNotIn('data-field="fatigue_roll"', html)
        self.assertNotIn('id="roll-fatigue-btn"', html)


class PlayerFieldUpdateTest(BaseTestCase):
    """character_update_field as the owner, a player (issue #748)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("field-player")
        self.character = create_character(
            self.player, fatigue_roll=7, intelligence=10, location="City"
        )
        self.login_as(self.player)
        self.url = reverse("characters:update_field", args=[self.character.pk])

    def update(self, field, value):
        return self.post_json(self.url, {"field": field, "value": value})

    def assert_refused(self, field, value):
        before = getattr(self.character, field)
        response = self.update(field, value)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"], services.field_refusal(field))
        self.character.refresh_from_db()
        self.assertEqual(getattr(self.character, field), before)

    def test_the_fatigue_roll_after_the_roll_button_refused_a_second(self):
        roll = self.client.post(
            reverse("characters:roll_fatigue", args=[self.character.pk])
        )
        self.assertEqual(roll.status_code, 400)
        self.assert_refused("fatigue_roll", 12)

    def test_an_attribute_the_chat_path_refuses_is_refused_here_too(self):
        with self.assertRaises(CharacterChangeError):
            services.set_attribute(
                self.character.pk, "strength", 30, is_game_master=False
            )
        self.assert_refused("strength", 30)
        self.assert_refused("dexterity", 12)

    def test_raising_int_cannot_pass_a_failed_literacy_roll(self):
        Character.objects.filter(pk=self.character.pk).update(
            literacy_roll=14, literacy_rolled=True
        )
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_literate)

        self.assert_refused("intelligence", 18)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_literate)

    def test_xp_coin_appearance_provenance_and_wealth(self):
        self.assert_refused("total_xp", 512000)
        self.assert_refused("starting_coin", 1000000)
        self.assert_refused("appearance_roll", "1")
        self.assert_refused("provenance_class", "nobility")
        self.assert_refused("provenance_detail", "Monarch")
        self.assert_refused("wealth_level", "Rich")
        self.character.refresh_from_db()
        self.assertEqual(self.character.level, 1)

    def test_what_a_player_may_still_set(self):
        for field, value in (
            ("name", "Renamed"),
            ("height_roll", "5"),
            ("weight_roll", "2"),
            ("location", "Rural"),
        ):
            with self.subTest(field=field):
                self.assertEqual(self.update(field, value).status_code, 200)

    def test_a_game_master_sets_them_all(self):
        self.logout()
        self.login_as(self.create_dm("field-gm"))

        for field, value in (
            ("fatigue_roll", 12),
            ("strength", 30),
            ("total_xp", 512000),
            ("starting_coin", 1000000),
            ("appearance_roll", "a scar across one eye"),
            ("provenance_class", "nobility"),
        ):
            with self.subTest(field=field):
                self.assertEqual(self.update(field, value).status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(
            (self.character.strength, self.character.fatigue_roll), (30, 12)
        )
        result = services.update_field(
            self.character.pk, "strength", 25, is_game_master=True
        )
        self.assertIn(refusal_words().common.the_game_masters_call, result.note)

    def test_the_fatigue_service_holds_a_player_by_default(self):
        with self.assertRaisesMessage(
            CharacterChangeError,
            "The Fatigue roll is already recorded; only a Game Master can roll again",
        ):
            services.roll_fatigue(self.character.pk)

    def test_a_players_height_and_weight_are_one_face(self):
        for field, value in (
            ("height_roll", "6.6.6.6.6"),
            ("height_roll", "giant"),
            ("weight_roll", "99"),
        ):
            with self.subTest(field=field, value=value):
                response = self.update(field, value)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["error"],
                    services.player_chosen_face_reasons()[field],
                )
        self.character.refresh_from_db()
        self.assertEqual(
            (self.character.height_roll, self.character.weight_roll), ("4", "3")
        )

    def test_an_unchanged_background_beside_a_new_origin_is_not_a_choice(self):
        result = services.set_background(
            self.character.pk,
            {
                "location": "Rural",
                "wealth_level": "Moderate",
                "provenance_class": "commoner",
            },
        )

        self.assertEqual(result.character.location, "Rural")
        # The Origin's skill follows it (#754); nothing is said of the
        # restated provenance and wealth.
        self.assertEqual(
            result.note, "location set to Rural; survival 1 granted by the Rural origin"
        )


class PointBuyTest(BaseTestCase):
    """A player buys the scores nobody has recorded (#571), 65 points in all."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("buy-player")
        self.character = create_character(
            self.player,
            strength=None,
            dexterity=None,
            intelligence=None,
            wisdom=None,
            constitution=None,
            charisma=None,
        )
        self.login_as(self.player)
        self.url = reverse("characters:update_field", args=[self.character.pk])

    def update(self, field, value):
        return self.post_json(self.url, {"field": field, "value": value})

    def test_a_player_buys_six_scores_totalling_65_and_each_locks(self):
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertContains(page, 'id="point-buy-note"')
        self.assertContains(page, 'class="editable-field" data-field="strength"')

        first = self.update("strength", 15)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(
            first.json()["locked_reason"], services.field_refusal("strength")
        )
        self.assertEqual(self.update("strength", 16).status_code, 400)
        for name, value in (
            ("dexterity", 12),
            ("intelligence", 10),
            ("wisdom", 10),
            ("constitution", 10),
        ):
            self.assertEqual(self.update(name, value).status_code, 200, name)
        short = self.update("charisma", 7)
        self.assertEqual(short.status_code, 400)
        self.assertIn("charisma can be 8 to 8", short.json()["error"])
        self.assertEqual(self.update("charisma", 8).status_code, 200)

        self.character.refresh_from_db()
        self.assertEqual(
            sum(getattr(self.character, name) for name in CHARACTER_ATTRIBUTE_FIELDS),
            65,
        )
        self.assertIn(
            "strength: point-buy = 15 (typed on the sheet)", self.character.dice_log
        )
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertNotContains(page, 'id="point-buy-note"')
        self.assertNotContains(page, 'id="roll-attributes-btn"')

    def test_a_typed_score_is_held_to_3_to_18_and_to_room_for_the_rest(self):
        self.assertEqual(self.update("strength", 19).status_code, 400)
        self.assertEqual(self.update("strength", 2).status_code, 400)
        for name in ("strength", "dexterity", "intelligence"):
            self.assertEqual(self.update(name, 18).status_code, 200, name)
        # 54 spent, three left at 3 or more: at most 65 - 54 - 6 = 5.
        too_many = self.update("wisdom", 6)
        self.assertEqual(too_many.status_code, 400)
        self.assertIn("wisdom can be 3 to 5", too_many.json()["error"])

    def test_the_rest_of_a_partial_character_is_rolled(self):
        Character.objects.filter(pk=self.character.pk).update(charisma=12)

        response = self.client.post(
            reverse("characters:roll", args=[self.character.pk, "attributes"])
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(
            response.json()["note"].startswith("rolled the missing attributes")
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.charisma, 12)
        self.assertTrue(services.attributes_known(self.character))
        refused = self.client.post(
            reverse("characters:roll", args=[self.character.pk, "attributes"])
        )
        self.assertEqual(refused.status_code, 400)

    def test_a_bought_score_commits_the_character_to_the_buy(self):
        """Three 18s bought and three rolled averaged about 85 points, against
        the buy's 65 or the roll's 63 (#748 review)."""
        for name in ("strength", "dexterity", "constitution"):
            self.assertEqual(self.update(name, 18).status_code, 200, name)
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertNotContains(page, 'id="roll-attributes-btn"')

        refused = self.client.post(
            reverse("characters:roll", args=[self.character.pk, "attributes"])
        )

        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["error"], services.bought_not_rolled_reason())
        self.character.refresh_from_db()
        self.assertIsNone(self.character.wisdom)

    def test_a_game_master_may_still_roll_a_buyers_missing_scores(self):
        self.assertEqual(self.update("strength", 18).status_code, 200)
        self.logout()
        self.login_as(self.create_dm("buy-gm"))

        response = self.client.post(
            reverse("characters:roll", args=[self.character.pk, "attributes"])
        )

        self.assertEqual(response.status_code, 200, response.content)

    def test_scores_a_game_master_recorded_count_toward_the_65(self):
        Character.objects.filter(pk=self.character.pk).update(
            strength=18, dexterity=18, intelligence=18
        )
        for name in ("wisdom", "constitution"):
            self.assertEqual(self.update(name, 3).status_code, 200, name)

        last = self.update("charisma", 6)

        self.assertEqual(last.status_code, 400)
        self.assertIn("charisma can be 5 to 5", last.json()["error"])

    def test_the_field_update_carries_the_roll_offers(self):
        for name, value in (
            ("strength", 15),
            ("dexterity", 12),
            ("intelligence", 10),
            ("wisdom", 10),
            ("constitution", 10),
        ):
            data = self.update(name, value).json()
            self.assertFalse(data["sheet_rolls"]["attributes"]["available"], name)
        last = self.update("charisma", 8).json()
        self.assertEqual(
            last["sheet_rolls"]["attributes"],
            {"available": False, "confirm": services.SHEET_ROLLS["attributes"].confirm},
        )

    def test_a_game_master_typing_the_last_score_turns_the_roll_into_a_reroll(self):
        Character.objects.filter(pk=self.character.pk).update(
            strength=10, dexterity=10, intelligence=10, wisdom=10, constitution=10
        )
        self.logout()
        self.login_as(self.create_dm("last-score-gm"))

        data = self.update("charisma", 12).json()

        self.assertEqual(
            data["sheet_rolls"]["attributes"],
            {"available": True, "confirm": services.SHEET_ROLLS["attributes"].confirm},
        )

    def test_a_players_input_for_a_missing_score_is_3_to_18(self):
        page = self.client.get(
            reverse("characters:edit", args=[self.character.pk])
        ).content.decode()

        self.assertRegex(
            page, r'data-field="strength"><input type="number"[^>]* min="3" max="18"'
        )

    def test_a_buyer_with_no_buy_left_is_sent_to_a_game_master(self):
        self.assertEqual(self.update("charisma", 3).status_code, 200)
        Character.objects.filter(pk=self.character.pk).update(
            strength=18, dexterity=18, intelligence=18, wisdom=18
        )

        response = self.update("constitution", 3)

        self.assertEqual(response.status_code, 400)
        self.assertIn("ask a Game Master to record the rest", response.json()["error"])

    def test_no_buy_is_left_after_high_rolls(self):
        Character.objects.filter(pk=self.character.pk).update(
            strength=18, dexterity=18, intelligence=18, wisdom=18
        )

        response = self.update("charisma", 3)

        self.assertEqual(response.status_code, 400)
        self.assertIn("roll the missing ones", response.json()["error"])


class SheetRollTest(BaseTestCase):
    """The sheet's roll controls (issues #748, #749)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("roll-player")
        self.login_as(self.player)

    def roll(self, character, kind):
        return self.client.post(reverse("characters:roll", args=[character.pk, kind]))

    def test_a_player_rolls_missing_attributes_once(self):
        character = create_character(
            self.player,
            strength=None,
            dexterity=None,
            intelligence=None,
            wisdom=None,
            constitution=None,
            charisma=None,
        )
        page = self.client.get(reverse("characters:edit", args=[character.pk]))
        self.assertContains(page, 'id="roll-attributes-btn"')

        first = self.roll(character, "attributes")
        second = self.roll(character, "attributes")

        self.assertEqual(first.status_code, 200, first.content)
        self.assertFalse(first.json()["can_roll"])
        self.assertEqual(second.status_code, 400)
        self.assertIn("only a Game Master", second.json()["error"])
        character.refresh_from_db()
        self.assertTrue(services.attributes_known(character))
        self.assertIn("strength: 3d6", character.dice_log)
        page = self.client.get(reverse("characters:edit", args=[character.pk]))
        self.assertNotContains(page, 'id="roll-attributes-btn"')

    def test_a_player_rolls_provenance_and_wealth_once(self):
        character = create_character(
            self.player, provenance_recorded=False, wealth_recorded=False
        )
        page = self.client.get(reverse("characters:edit", args=[character.pk]))
        self.assertContains(page, 'id="roll-provenance-btn"')
        self.assertContains(page, 'id="roll-wealth-btn"')
        self.assertNotContains(page, 'id="roll-starting-coin-btn"')

        with patch("characters.dice.roll_d100", side_effect=[5, 1, 80, 42]):
            provenance = self.roll(character, "provenance")
            wealth = self.roll(character, "wealth")

        self.assertEqual(provenance.status_code, 200, provenance.content)
        self.assertEqual(wealth.status_code, 200, wealth.content)
        character.refresh_from_db()
        self.assertEqual(
            (character.provenance_class, character.provenance_detail),
            ("nobility", "Monarch"),
        )
        self.assertEqual(
            (character.wealth_level, character.starting_coin), ("Merchant", 142)
        )
        self.assertIn(
            "Provenance: d100=5 -> Nobility, d100=1 -> Monarch", character.dice_log
        )
        self.assertIn(
            "Wealth: d100=80 -> Merchant, starting coin = 100 + d100(42) = 142",
            character.dice_log,
        )
        for kind in ("provenance", "wealth", "starting_coin"):
            with self.subTest(kind=kind):
                self.assertEqual(self.roll(character, kind).status_code, 400)

    def test_a_rich_roll_leaves_the_coin_to_the_game_master(self):
        character = create_character(self.player, wealth_recorded=False)

        with patch("characters.dice.roll_d100", return_value=99):
            self.roll(character, "wealth")

        character.refresh_from_db()
        self.assertEqual(character.wealth_level, "Rich")
        self.assertIsNone(character.starting_coin)
        self.assertIn("starting coin = Consult GM", character.dice_log)
        page = self.client.get(reverse("characters:edit", args=[character.pk]))
        self.assertContains(page, 'data-readonly-field="starting_coin">Consult GM<')

    def test_an_unset_rich_coin_exports_as_consult_gm(self):
        rich = create_character(self.player, wealth_level="Rich", starting_coin=None)

        markdown = generate_character_markdown(rich)
        exported = json.loads(generate_character_json(rich))

        self.assertIn("- **Starting Coin:** Consult GM", markdown)
        self.assertIsNone(exported["data"]["starting_coin"])
        self.assertIsNone(Character.clean_field_value("starting_coin", None))

    def test_a_game_master_rerolls_coin_but_not_a_rich_one(self):
        character = create_character(
            self.player, wealth_level="Merchant", starting_coin=150
        )
        rich = create_character(self.player, wealth_level="Rich", starting_coin=None)
        self.logout()
        self.login_as(self.create_dm("coin-gm"))

        with patch("characters.dice.roll_d100", return_value=7):
            response = self.roll(character, "starting_coin")

        self.assertEqual(response.status_code, 200, response.content)
        character.refresh_from_db()
        self.assertEqual(character.starting_coin, 107)
        self.assertIn("Starting coin rerolled: 100 + d100(7) = 107", character.dice_log)
        refused = self.roll(rich, "starting_coin")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Consult GM", refused.json()["error"])

    def test_appearance_is_rolled_when_it_is_missing(self):
        character = create_character(self.player, appearance_roll="")

        with patch("characters.dice.roll_d6", return_value=6):
            response = self.roll(character, "appearance")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("for the Game Master to qualify", response.json()["note"])
        character.refresh_from_db()
        self.assertEqual(character.appearance_roll, "6")
        self.assertEqual(self.roll(character, "appearance").status_code, 400)

    def test_a_game_masters_reroll_asks_first_and_keeps_the_old_values(self):
        character = create_character(self.player)
        self.logout()
        self.login_as(self.create_dm("reroll-gm"))
        page = self.client.get(reverse("characters:edit", args=[character.pk]))
        for button in (
            "roll-attributes-btn",
            "roll-appearance-btn",
            "roll-provenance-btn",
        ):
            with self.subTest(button=button):
                self.assertRegex(
                    page.content.decode(),
                    rf'id="{button}"[^>]* data-confirm="Roll [^"]+ again\?',
                )

        with patch("characters.dice.roll_d6", return_value=5):
            self.roll(character, "appearance")
        with patch(
            "characters.services.roll_attribute_logged",
            return_value=(11, "3d6: [3, 4, 4] = 11"),
        ):
            self.roll(character, "attributes")

        character.refresh_from_db()
        history = {
            entry["field"]: (entry["old"], entry["new"]) for entry in character.history
        }
        self.assertEqual(history["appearance_roll"], ("3", "5"))
        self.assertEqual(history["strength"], ("14", "11"))
        self.assertEqual(history["charisma"], ("8", "11"))

    def test_a_first_roll_asks_nothing(self):
        character = create_character(self.player, wealth_recorded=False)

        page = self.client.get(reverse("characters:edit", args=[character.pk]))

        self.assertRegex(
            page.content.decode(),
            r'id="roll-wealth-btn" data-roll-kind="wealth" data-roll-url="[^"]+">',
        )

    def test_an_unknown_roll_and_a_strangers_roll(self):
        character = create_character(self.player, wealth_recorded=False)
        self.assertEqual(self.roll(character, "luck").status_code, 400)
        self.logout()
        self.login_as(self.create_user("roll-stranger"))
        self.assertEqual(self.roll(character, "wealth").status_code, 403)


class WealthIsRolledOnceTest(BaseTestCase):
    """Re-selecting a wealth level never rerolls the coin (issue #749)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("wealth-player")

    def test_six_toggles_by_a_game_master_keep_one_coin(self):
        character = create_character(
            self.player, wealth_level="Merchant", starting_coin=107
        )
        for _ in range(6):
            for level in ("Moderate", "Merchant"):
                result = services.update_field(
                    character.pk, "wealth_level", level, is_game_master=True
                )

        character.refresh_from_db()
        self.assertEqual(character.starting_coin, 107)
        self.assertEqual(
            result.note,
            "wealth_level set to Merchant; starting coin kept at 107; a Game "
            "Master may reroll it",
        )

    def test_a_players_session_cannot_choose_background(self):
        character = create_character(self.player)

        for values in ({"provenance_class": "nobility"}, {"wealth_level": "Rich"}):
            with self.subTest(values=values), self.assertRaises(CharacterChangeError):
                services.set_background(character.pk, values)

        character.refresh_from_db()
        self.assertEqual(
            (character.provenance_class, character.wealth_level),
            ("commoner", "Moderate"),
        )

    def test_a_game_masters_first_background_sets_the_coin_and_records_it(self):
        character = create_character(
            self.player, provenance_recorded=False, wealth_recorded=False
        )

        with patch("characters.dice.roll_d100", return_value=30):
            services.set_background(
                character.pk,
                {"provenance_class": "merchant", "wealth_level": "Merchant"},
                is_game_master=True,
            )

        character.refresh_from_db()
        self.assertEqual(character.starting_coin, 130)
        self.assertTrue(character.provenance_recorded)
        self.assertTrue(character.wealth_recorded)


class ExistingCharactersTest(BaseTestCase):
    """The migration decides what an existing character's player may roll."""

    def test_only_a_default_background_nobody_rolled_is_left_to_roll(self):
        player = self.create_user("migrated-player")
        created = create_character(
            player,
            dice_log="Provenance: d100=40 -> Commoner, d100=3 -> Laborer\n"
            "Wealth: d100=50 -> Moderate, starting coin = 100",
        )
        chat_built = create_character(player)
        set_later = create_character(player, provenance_class="merchant")
        rich = create_character(player, wealth_level="Rich", starting_coin=0)
        chosen_back = create_character(
            player,
            history=[
                {
                    "ts": "t",
                    "field": "wealth_level",
                    "old": "Rich",
                    "new": "Moderate",
                    "by": "gm",
                }
            ],
        )

        MIGRATION.mark_unrolled_backgrounds(apps, None)

        recorded = {
            character.pk: (character.provenance_recorded, character.wealth_recorded)
            for character in Character.objects.all()
        }
        self.assertEqual(recorded[created.pk], (True, True))
        self.assertEqual(recorded[chat_built.pk], (False, False))
        self.assertEqual(recorded[set_later.pk], (True, False))
        # A wealth set on record, even back to the default, is not re-rolled.
        self.assertEqual(recorded[chosen_back.pk], (False, True))
        rich.refresh_from_db()
        self.assertIsNone(rich.starting_coin)

        MIGRATION.restore_rich_coin(apps, None)
        rich.refresh_from_db()
        self.assertEqual(rich.starting_coin, 0)


class ItemModifiersTest(BaseTestCase):
    """Equipment modifiers are a Game Master's grant (issue #752)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("gear-player")
        self.character = create_character(
            self.player,
            equipment=[
                {"item_id": "broadsword", "quantity": 1},
                {"item_id": "chainmail", "quantity": 1, "equipped": True},
            ],
        )
        self.update_url = reverse(
            "characters:equipment_update", args=[self.character.pk]
        )
        self.add_url = reverse("characters:equipment_add", args=[self.character.pk])

    def test_a_player_cannot_give_a_catalog_sword_plus_ten(self):
        self.login_as(self.player)

        for url, body in (
            (self.update_url, {"index": 0, "to_hit_mod": 10, "damage_mod": 10}),
            (self.update_url, {"index": 1, "stops_mod": 10}),
            (self.add_url, {"item_id": "dagger", "critical_mod": 3}),
            (
                self.add_url,
                {
                    "is_custom": True,
                    "name": "Lucky Plate",
                    "category": "armor",
                    "stops_mod": 4,
                },
            ),
        ):
            with self.subTest(body=body):
                response = self.post_json(url, body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["error"], services.ITEM_MODIFIER_RULE.reason
                )
        self.character.refresh_from_db()
        self.assertEqual(self.character.weapon_to_hit_mod, 0)
        self.assertEqual(len(self.character.equipment), 2)

    def test_a_player_still_manages_their_own_gear(self):
        self.login_as(self.player)

        response = self.post_json(
            self.update_url, {"index": 0, "notes": "grandfather's"}
        )

        self.assertEqual(response.status_code, 200, response.content)

    def test_a_game_masters_grant_carries_their_name(self):
        game_master = self.create_dm("gear-gm")
        self.login_as(game_master)

        response = self.post_json(self.update_url, {"index": 0, "to_hit_mod": 1})

        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        sword = self.character.equipment[0]
        self.assertEqual(
            (sword["to_hit_mod"], sword["modifiers_set_by"]), (1, "gear-gm")
        )
        self.assertEqual(
            self.character.history[-1]["new"], "modifiers broadsword: to hit mod +1"
        )

    def test_the_battle_board_reads_no_modifier(self):
        """The board read the armour's modifiers and not the weapon's; it now
        reads none, the same way for every item (issue #752)."""
        plain = snapshot_character(
            self.character, 1, display_name="Plain", archetype="", spells=(), mana=0
        )
        equipment = json.loads(json.dumps(self.character.equipment))
        equipment[0].update(to_hit_mod=3, damage_mod=3)
        equipment[1].update(stops_mod=4, dex_mod=5)
        self.character.equipment = equipment
        self.character.save()

        granted = snapshot_character(
            self.character, 1, display_name="Granted", archetype="", spells=(), mana=0
        )

        self.assertEqual(self.character.armor_stops, plain.stops + 4)
        self.assertEqual(
            (granted.stops, granted.dexterity), (plain.stops, plain.dexterity)
        )
        self.assertEqual(granted.weapon, plain.weapon)

    def test_a_packed_weapon_is_not_on_the_tiles(self):
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1, "to_hit_mod": 2},
            {"item_id": "dagger", "quantity": 1, "to_hit_mod": 3, "in_pack": True},
        ]

        self.assertEqual(self.character.weapon_to_hit_mod, 2)

    def test_the_sheet_says_so_for_everyone(self):
        self.login_as(self.player)

        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))

        self.assertContains(
            page, services.ITEM_MODIFIER_RULE.reason.replace("'", "&#x27;")
        )
        self.assertContains(page, "the battle board reads no modifiers")
